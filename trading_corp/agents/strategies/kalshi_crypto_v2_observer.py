"""kalshi_crypto_v2 — READ-ONLY forward logger (Phase 1 T2 research observer).

Logs, on a 30s cycle, to 4 dedicated kcv2_* tables:
  a. cfbenchmarks_value index (raw value + trailing-60s avg) for BTC/ETH/SOL/XRP
  b. near-money BOTH-SIDED raw quotes for active 15-min + hourly Kalshi markets
  c. lifted SFP signal state (bitunix_sfp AS-IS) computed from bitunix_bar_history

STRICTLY READ-ONLY: no order/placement imports or code paths anywhere. No broker,
no division wiring, no auto_execute. This is a research collector; the old
`kalshi_crypto` division is untouched.

Integrity guards AT WRITE TIME (KT doc s9):
  * store RAW quotes only (no derived implieds); sum_to_1_ok flag per LIVE quote
  * heartbeat row every cycle with per-category counts; zero-row cycle -> alarm=1
  * join on market_ticker + cycle_id (never timestamp-tolerance)
Conditions: kcv2_quotes.band_pct recorded per row (near-money band used);
kcv2_signals.computed_bar_ts_ms = the bar the SFP state was computed from.

Creds: env KALSHI_KAREN_API_KEY_ID / KALSHI_KAREN_PRIVATE_KEY_PEM first, then
Azure Key Vault fallback (DefaultAzureCredential + $KEY_VAULT_URI). In-memory
only. Secret values never logged.

Run: python -m trading_corp.agents.strategies.kalshi_crypto_v2_observer
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from trading_corp.agents.strategies.bitunix_sfp import (
    MODE_CONSIDERABLE, MODE_REAL, SfpBar, SfpDetector,
)

log = logging.getLogger("kalshi_crypto_v2_observer")

# ── Config (ONE place; changeable without schema change) ─────────────────────
CADENCE_SEC = 30
BAND_PCT = 0.01                      # near-money moneyness band (recorded per row)
BAR_LOOKBACK = 300                   # 15m bars fed to the SFP detector
SUM_TO_1 = (0.5, 1.5)                # LIVE quote sanity band

INDEX_IDS = {"BTC": "BRTI", "ETH": "ETHUSD_RTI", "SOL": "SOLUSD_RTI", "XRP": "XRPUSD_RTI"}
BITUNIX_SYMBOL = {"BTC": "BTCUSDT", "ETH": "ETHUSDT", "SOL": "SOLUSDT", "XRP": "XRPUSDT"}
SERIES_15M = {"BTC": "KXBTC15M", "ETH": "KXETH15M", "SOL": "KXSOL15M", "XRP": "KXXRP15M"}
SERIES_HOURLY_LADDER = {"BTC": "KXBTC", "ETH": "KXETH", "SOL": "KXSOLE", "XRP": "KXXRP"}
SERIES_HOURLY_DIR = {"BTC": "KXBTCD", "ETH": "KXETHD", "SOL": "KXSOLD", "XRP": "KXXRPD"}
ASSETS = ["BTC", "ETH", "SOL", "XRP"]

CF_WS_ENDPOINT = os.getenv("CF_WS_ENDPOINT", "wss://external-api-ws.kalshi.com/trade-api/ws/v2")
CF_WS_SIGN_PATH = os.getenv("CF_WS_SIGN_PATH", "/trade-api/ws/v2")
REST_BASE = "https://api.elections.kalshi.com/trade-api/v2"
_REST_API_PATH = "/trade-api/v2"
_CTX = ssl.create_default_context()


def _db_path() -> str:
    url = os.getenv("TRADING_CORP_DB_URL", "data/trading_corp.db")
    return url.replace("sqlite:///", "").replace("sqlite://", "")


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


# ── Creds + signing (in-memory; env override then Key Vault) ─────────────────
def load_creds() -> tuple[str, str]:
    kid = os.getenv("KALSHI_KAREN_API_KEY_ID")
    pem = os.getenv("KALSHI_KAREN_PRIVATE_KEY_PEM")
    if kid and pem:
        return kid, pem.replace("\\n", "\n")
    vault = os.getenv("KEY_VAULT_URI")
    if not vault:
        raise RuntimeError("kcv2: no KALSHI_KAREN_* env and no KEY_VAULT_URI for fallback")
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
    c = SecretClient(vault_url=vault, credential=DefaultAzureCredential())
    kid = c.get_secret("KALSHI-KAREN-API-KEY-ID").value
    pem = c.get_secret("KALSHI-KAREN-PRIVATE-KEY-PEM").value
    if not kid or not pem:
        raise RuntimeError("kcv2: Key Vault returned empty KALSHI-KAREN secret(s)")
    return kid, pem.replace("\\n", "\n")


def make_signer(pem: str):
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    key = serialization.load_pem_private_key(pem.encode(), password=None)

    def sign(method: str, path: str) -> tuple[str, str]:
        ts = str(int(time.time() * 1000))
        sig = key.sign(f"{ts}{method}{path}".encode(),
                       padding.PSS(mgf=padding.MGF1(hashes.SHA256()),
                                   salt_length=padding.PSS.DIGEST_LENGTH),
                       hashes.SHA256())
        return ts, base64.b64encode(sig).decode()

    return sign


class Observer:
    def __init__(self) -> None:
        self.kid, pem = load_creds()
        self._sign = make_signer(pem)
        self._index: dict[str, dict] = {}          # asset -> {value, avg60, window, received_at}
        self._ws_connected = False
        self._sfp_cache: dict[str, tuple[int, list[dict]]] = {}  # asset -> (max_bar_ts, rows)
        self._cycle = 0

    # -- signed REST --
    def rest_get(self, endpoint: str, params: dict | None = None) -> dict:
        ts, sig = self._sign("GET", _REST_API_PATH + endpoint)
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        qs = ("?" + urllib.parse.urlencode(clean)) if clean else ""
        req = urllib.request.Request(
            REST_BASE + endpoint + qs, method="GET",
            headers={"KALSHI-ACCESS-KEY": self.kid, "KALSHI-ACCESS-SIGNATURE": sig,
                     "KALSHI-ACCESS-TIMESTAMP": ts, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20, context=_CTX) as r:
            return json.loads(r.read().decode())

    # -- cfbenchmarks WS: keep latest index per asset --
    async def ws_loop(self) -> None:
        import websockets
        id_to_asset = {v: k for k, v in INDEX_IDS.items()}
        backoff = 1.0
        while True:
            try:
                ts, sig = self._sign("GET", CF_WS_SIGN_PATH)
                headers = {"KALSHI-ACCESS-KEY": self.kid, "KALSHI-ACCESS-SIGNATURE": sig,
                           "KALSHI-ACCESS-TIMESTAMP": ts}
                try:
                    ws = await websockets.connect(CF_WS_ENDPOINT, additional_headers=headers,
                                                  ping_interval=20, ping_timeout=10)
                except TypeError:
                    ws = await websockets.connect(CF_WS_ENDPOINT, extra_headers=headers,
                                                  ping_interval=20, ping_timeout=10)
                await ws.send(json.dumps({"id": 1, "cmd": "subscribe", "params": {
                    "channels": ["cfbenchmarks_value"], "index_ids": list(INDEX_IDS.values())}}))
                self._ws_connected = True
                backoff = 1.0
                log.info("kcv2: cfbenchmarks WS connected")
                async for raw in ws:
                    try:
                        m = json.loads(raw)
                    except Exception:
                        continue
                    if m.get("type") != "cfbenchmarks_value":
                        continue
                    inner = m.get("msg", {})
                    asset = id_to_asset.get(inner.get("index_id"))
                    if not asset:
                        continue
                    val = None
                    try:
                        val = _f(json.loads(inner.get("data", "{}")).get("value"))
                    except Exception:
                        pass
                    a60 = inner.get("avg_60s_data") or {}
                    self._index[asset] = {
                        "value": val, "avg60": _f(a60.get("value")),
                        "window": a60.get("window_size"), "received_at": inner.get("received_at")}
            except Exception as e:
                self._ws_connected = False
                log.warning("kcv2: WS dropped (%s); reconnect in %.0fs", type(e).__name__, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    # -- SFP signal state from bitunix_bar_history (cached by max bar ts) --
    def sfp_state(self, conn, asset: str) -> list[dict]:
        sym = BITUNIX_SYMBOL[asset]
        try:
            rows = conn.execute(
                "SELECT ts_ms,open,high,low,close FROM bitunix_bar_history "
                "WHERE symbol=? AND timeframe='15m' ORDER BY ts_ms DESC LIMIT ?",
                (sym, BAR_LOOKBACK)).fetchall()
        except Exception:
            return [{"sfp_mode": None, "bos_tf": "15m", "state": "NONE",
                     "swept_swing_level": None, "swept_low": None, "bos_ref_high": None,
                     "computed_bar_ts_ms": None}]
        if not rows:
            return [{"sfp_mode": None, "bos_tf": "15m", "state": "NONE",
                     "swept_swing_level": None, "swept_low": None, "bos_ref_high": None,
                     "computed_bar_ts_ms": None}]
        rows = rows[::-1]
        max_ts = rows[-1][0]
        cached = self._sfp_cache.get(asset)
        if cached and cached[0] == max_ts:
            return cached[1]
        bars = [SfpBar(ts_ms=r[0], open=r[1], high=r[2], low=r[3], close=r[4]) for r in rows]
        out: list[dict] = []
        for mode in (MODE_REAL, MODE_CONSIDERABLE):
            det = SfpDetector(mode=mode)
            det.warm_start(bars)
            trans = det.drain_transitions()
            confirmed = [t for t in trans if t.status == "CONFIRMED" and t.status_bar_ts_ms == max_ts]
            for t in confirmed:
                out.append({"sfp_mode": mode, "bos_tf": "15m", "state": "CONFIRMED",
                            "swept_swing_level": t.swept_level, "swept_low": t.swept_wick,
                            "bos_ref_high": t.bos_ref_high, "computed_bar_ts_ms": max_ts})
            active = list(det._watches)  # armed, unresolved watches (read-only introspection)
            for w in active:
                out.append({"sfp_mode": mode, "bos_tf": "15m", "state": "ARMED",
                            "swept_swing_level": w.level, "swept_low": w.swept_low,
                            "bos_ref_high": None, "computed_bar_ts_ms": max_ts})
            if not confirmed and not active:
                out.append({"sfp_mode": mode, "bos_tf": "15m", "state": "NONE",
                            "swept_swing_level": None, "swept_low": None,
                            "bos_ref_high": None, "computed_bar_ts_ms": max_ts})
        self._sfp_cache[asset] = (max_ts, out)
        return out

    # -- near-money quote rows for one series --
    def quote_rows(self, asset: str, cadence: str, series: str, index_val: float | None) -> list[dict]:
        try:
            resp = self.rest_get("/markets", {"series_ticker": series, "status": "open", "limit": 1000})
        except Exception as e:
            log.warning("kcv2: markets fetch %s failed: %s", series, type(e).__name__)
            return []
        rows = []
        for m in resp.get("markets", []) or []:
            fl = _f(m.get("floor_strike"))
            moneyness = ((fl - index_val) / index_val) if (fl is not None and index_val) else None
            if moneyness is not None and abs(moneyness) > BAND_PCT:
                continue                                   # outside near-money band -> not logged
            ya, na = _f(m.get("yes_ask_dollars")), _f(m.get("no_ask_dollars"))
            s1 = 1 if (ya is not None and na is not None and SUM_TO_1[0] <= ya + na <= SUM_TO_1[1]) else 0
            rows.append({
                "asset": asset, "cadence": cadence, "series": series,
                "event_ticker": m.get("event_ticker"), "market_ticker": m.get("ticker"),
                "floor_strike": fl, "index_value": index_val, "moneyness": moneyness,
                "band_pct": BAND_PCT,
                "yes_bid": _f(m.get("yes_bid_dollars")), "yes_ask": ya,
                "no_bid": _f(m.get("no_bid_dollars")), "no_ask": na,
                "last_price": _f(m.get("last_price_dollars")), "volume": _f(m.get("volume_fp")),
                "open_interest": _f(m.get("open_interest_fp")), "status": m.get("status"),
                "sum_to_1_ok": s1})
        return rows

    # -- one 30s cycle (sync; run via to_thread so WS keeps consuming) --
    def cycle(self) -> None:
        import sqlite3
        self._cycle += 1
        cid = self._cycle
        now_ms = int(time.time() * 1000)
        conn = sqlite3.connect(_db_path(), timeout=30)
        try:
            n_idx = n_q = n_sig = n_mkt = 0
            # (a) index
            for asset in ASSETS:
                ix = self._index.get(asset)
                if not ix:
                    continue
                conn.execute(
                    "INSERT INTO kcv2_index_ticks(ts_ms,cycle_id,index_id,asset,value,"
                    "avg60_value,avg60_window_size,received_at_ms) VALUES(?,?,?,?,?,?,?,?)",
                    (now_ms, cid, INDEX_IDS[asset], asset, ix.get("value"), ix.get("avg60"),
                     ix.get("window"), ix.get("received_at")))
                n_idx += 1
            # (b) quotes (near-money) across 15m + hourly ladder + hourly directional
            for asset in ASSETS:
                iv = (self._index.get(asset) or {}).get("value")
                for cadence, smap in (("15m", SERIES_15M), ("hourly_ladder", SERIES_HOURLY_LADDER),
                                      ("hourly_dir", SERIES_HOURLY_DIR)):
                    for qr in self.quote_rows(asset, cadence, smap[asset], iv):
                        n_mkt += 1
                        conn.execute(
                            "INSERT INTO kcv2_quotes(ts_ms,cycle_id,asset,cadence,series,"
                            "event_ticker,market_ticker,floor_strike,index_value,moneyness,band_pct,"
                            "yes_bid,yes_ask,no_bid,no_ask,last_price,volume,open_interest,status,"
                            "sum_to_1_ok) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (now_ms, cid, qr["asset"], qr["cadence"], qr["series"],
                             qr["event_ticker"], qr["market_ticker"], qr["floor_strike"],
                             qr["index_value"], qr["moneyness"], qr["band_pct"], qr["yes_bid"],
                             qr["yes_ask"], qr["no_bid"], qr["no_ask"], qr["last_price"],
                             qr["volume"], qr["open_interest"], qr["status"], qr["sum_to_1_ok"]))
                        n_q += 1
            # (c) SFP signal state
            for asset in ASSETS:
                for sr in self.sfp_state(conn, asset):
                    conn.execute(
                        "INSERT INTO kcv2_signals(ts_ms,cycle_id,asset,sfp_mode,bos_tf,state,"
                        "swept_swing_level,swept_low,bos_ref_high,computed_bar_ts_ms) "
                        "VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (now_ms, cid, asset, sr["sfp_mode"], sr["bos_tf"], sr["state"],
                         sr["swept_swing_level"], sr["swept_low"], sr["bos_ref_high"],
                         sr["computed_bar_ts_ms"]))
                    n_sig += 1
            # heartbeat + zero-row alarm (index or quotes empty is the real alarm)
            alarm = 1 if (n_idx == 0 or n_q == 0) else 0
            conn.execute(
                "INSERT INTO kcv2_heartbeat(ts_ms,cycle_id,rows_index,rows_quotes,rows_signals,"
                "n_markets_active,index_ws_connected,alarm,note) VALUES(?,?,?,?,?,?,?,?,?)",
                (now_ms, cid, n_idx, n_q, n_sig, n_mkt, int(self._ws_connected), alarm,
                 "ok" if not alarm else "zero-row-cycle"))
            conn.commit()
            lvl = log.warning if alarm else log.info
            lvl("kcv2 cycle %d: idx=%d quotes=%d signals=%d ws=%s alarm=%d",
                cid, n_idx, n_q, n_sig, self._ws_connected, alarm)
        finally:
            conn.close()

    async def cycle_loop(self) -> None:
        # let the WS prime a few seconds before the first sample
        await asyncio.sleep(3)
        while True:
            start = time.time()
            try:
                await asyncio.to_thread(self.cycle)
            except Exception:
                log.exception("kcv2: cycle error")
            await asyncio.sleep(max(1.0, CADENCE_SEC - (time.time() - start)))

    async def run(self) -> None:
        log.info("kcv2 observer starting: cadence=%ss band=%.3f db=%s ws=%s",
                 CADENCE_SEC, BAND_PCT, _db_path(), CF_WS_ENDPOINT)
        coro = asyncio.gather(self.ws_loop(), self.cycle_loop())
        max_s = _f(os.getenv("KCV2_MAX_SECONDS"))     # smoke/test only
        if max_s:
            try:
                await asyncio.wait_for(coro, timeout=max_s)
            except asyncio.TimeoutError:
                log.info("kcv2: KCV2_MAX_SECONDS reached, stopping")
        else:
            await coro


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        asyncio.run(Observer().run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
