"""pead_earnings_watcher.py — ISOLATED side-process for the PEAD 'Upcoming Earnings' panel.

Mirrors sfp-card-watcher / market-context-recorder: a SEPARATE process that writes ONLY its own
`earnings_watch.db`, reads the engine DB strictly `mode=ro` (only to learn which PEAD names are
already held), NEVER imports the engine trade path and NEVER restarts the engine. Driven by
`pead-earnings-watcher.timer` (2x/day). A crash/hang here has ZERO effect on the trading engine.

What it does each refresh:
  * pulls the EODHD earnings calendar for [today-BACK_DAYS, today+FWD_DAYS] in ONE call,
  * keeps US reporters that are in the SAME universe file the engine scan reads (IDENTICAL parse),
  * for each in-universe reporter builds the EXACT engine ScreenInputs (yfinance daily bars for
    price+30d volume, EODHD facts for market-cap+sector, next-earnings-date for drift-room) and runs
    `pead_signal.passes_screen` -> screen_ok + the machine-readable failing-gate tag,
  * computes the SUE PLAUSIBILITY profile pre-report (own-noise stdev of UE + latest realized SUE +
    a trailing hit-rate) and the EXACT computed SUE post-report (from the printed actual),
  * flags names we already hold (engine DB mode=ro),
  * upserts one row per (code, report_date) into earnings_watch.db for the dashboard to read mode=ro.

Reuses the engine's OWN pure modules (imported via PYTHONPATH=/home/azureuser/trading_corp) so the
screen + SUE are BYTE-IDENTICAL to prod:
  trading_corp.agents.strategies.pead_signal   (ScreenInputs, passes_screen, *_from_config, SUE math)
  trading_corp.data.earnings_provider          (EarningsProvider: EODHD fundamentals/EPS/facts/next-earn)
The cross-symbol /calendar/earnings endpoint (which the provider deliberately lacks) is fetched here
directly with stdlib urllib. The universe parse + business_days are replicated VERBATIM from the engine
(pead_strategy._universe / pead_view.business_days) — see the asserts in --check.

Modes:
  --check     init schema + open engine RO + count held PEAD names + load the EODHD key. NO calendar
              fetch, NO screen. Proves plumbing + isolation without external per-name calls.
  --once      (default) run ONE full refresh and exit (the systemd oneshot entrypoint).
  --dry-run   run a full refresh against a TEMP db (PEAD_WATCH_DB override) and print the summary.

Env (all optional): PEAD_WATCH_DB, PEAD_WATCH_ENGINE_DB, PEAD_STRATEGIES_YAML, KEY_VAULT_URI,
EODHD_API_KEY, PEAD_FWD_DAYS(7), PEAD_BACK_DAYS(3), PEAD_PRUNE_TAIL_DAYS(10), PEAD_MAX_NAMES(0=all),
PEAD_HTTP_TIMEOUT(15), PEAD_WATCH_SHARDS(2), PEAD_WATCH_SHARD(auto=by-UTC-hour: 11:00->0 / 21:00->1).

Shard split (seasonal load relief): the 2x/day timer runs process COMPLEMENTARY halves of the
in-universe reporter list — the 11:00 UTC (AM) run does shard 0, the 21:00 UTC (PM) run does shard 1 —
so each name refreshes once/day and the serial per-name fetch loop fits the timeout budget even at the
earnings-season peak. AM u PM = the full set (stable crc32 partition; no name dropped/double-counted).
"""
import json
import logging
import math
import os
import statistics
import sys
import urllib.parse
import urllib.request
import zlib
from collections import namedtuple
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import earnings_watch_db as wdb

# PROD pure modules (PYTHONPATH=/home/azureuser/trading_corp) — identical screen + SUE as the engine.
from trading_corp.agents.strategies.pead_signal import (  # noqa: E402
    ScreenInputs,
    passes_screen,
    screen_params_from_config,
    standardized_ue,
    unexpected_earnings,
)
from trading_corp.data.earnings_provider import EarningsProvider  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# keep the journal readable: the KeyVault pull + HTTP libs are chatty at INFO.
for _n in ("azure", "azure.identity", "azure.core.pipeline.policies.http_logging_policy", "urllib3"):
    logging.getLogger(_n).setLevel(logging.WARNING)
log = logging.getLogger("pead_earnings_watcher")

_EODHD_BASE = "https://eodhd.com/api"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


FWD_DAYS = _int_env("PEAD_FWD_DAYS", 7)
BACK_DAYS = _int_env("PEAD_BACK_DAYS", 3)
PRUNE_TAIL_DAYS = _int_env("PEAD_PRUNE_TAIL_DAYS", 10)
MAX_NAMES = _int_env("PEAD_MAX_NAMES", 0)          # 0 = no cap (bounded smoke: set e.g. 15)
HTTP_TIMEOUT = _int_env("PEAD_HTTP_TIMEOUT", 15)
# Shard split: how many complementary halves the 2x/day timer runs partition the reporter list into,
# and which shard THIS run does. <=1 => no split (single full run = legacy behavior).
WATCH_SHARDS = _int_env("PEAD_WATCH_SHARDS", 2)
WATCH_SHARD = os.environ.get("PEAD_WATCH_SHARD", "auto")  # 'auto'(by UTC hour) | 'am'|'pm' | '0'|'1'|..

_Bar = namedtuple("_Bar", "date open high low close volume")


def strategies_yaml_path() -> Path:
    return Path(os.environ.get(
        "PEAD_STRATEGIES_YAML",
        str(Path.home() / "trading_corp" / "config" / "strategies.yaml"))).expanduser()


# ── EODHD key (env, else KeyVault via managed identity) ──────────────────────
def load_eodhd_key() -> str:
    k = os.environ.get("EODHD_API_KEY")
    if k:
        return k
    uri = os.environ.get("KEY_VAULT_URI")
    if not uri:
        raise RuntimeError("no EODHD_API_KEY in env and no KEY_VAULT_URI to pull it from")
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
    client = SecretClient(vault_url=uri, credential=DefaultAzureCredential())
    val = client.get_secret("EODHD-API-KEY").value
    os.environ["EODHD_API_KEY"] = val  # so EarningsProvider() also picks it up
    return val


# ── EODHD earnings calendar (the cross-symbol endpoint the provider lacks) ───
def eodhd_calendar(key: str, dfrom: str, dto: str) -> list:
    q = urllib.parse.urlencode({"api_token": key, "fmt": "json", "from": dfrom, "to": dto})
    url = f"{_EODHD_BASE}/calendar/earnings?{q}"
    req = urllib.request.Request(url, headers={"User-Agent": "pead-earnings-watcher/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        log.error("eodhd_calendar(%s..%s) failed: %s", dfrom, dto, e)
        return []
    return data.get("earnings", []) if isinstance(data, dict) else []


# ── config + universe (IDENTICAL to the engine) ──────────────────────────────
def load_cfg() -> dict:
    import yaml
    try:
        with strategies_yaml_path().open(encoding="utf-8") as f:
            return (yaml.safe_load(f) or {}).get("robinhood_pead", {}) or {}
    except Exception as e:  # noqa: BLE001
        log.warning("load_cfg failed: %s", e)
        return {}


def load_universe(cfg: dict) -> set:
    """Replicates trading_corp.agents.strategies.pead_strategy._universe (file branch) VERBATIM:
    `[ln.strip().upper() for ln in f if ln.strip() and not ln.startswith('#')]`."""
    spec = cfg.get("universe") or cfg.get("universe_file")
    if isinstance(spec, list):
        return {str(s).strip().upper() for s in spec if str(s).strip()}
    if isinstance(spec, str) and spec:
        p = spec[1:] if spec.startswith("@") else spec
        # engine CWD is /home/azureuser/trading_corp; resolve the anchored path there.
        cand = Path(p)
        if not cand.is_absolute() and not cand.exists():
            cand = Path.home() / "trading_corp" / p
        try:
            with open(cand, encoding="utf-8") as f:
                return {ln.strip().upper() for ln in f
                        if ln.strip() and not ln.startswith("#")}
        except Exception as e:  # noqa: BLE001
            log.error("load_universe(%s) failed: %s", cand, e)
    return set()


def business_days(d1: date, d2: date) -> int:
    """VERBATIM copy of trading_corp.web.pead_view.business_days — weekday count in [d1, d2)."""
    if d2 <= d1:
        return 0
    total = (d2 - d1).days
    weeks, extra = divmod(total, 7)
    count = weeks * 5
    for i in range(extra):
        if (d1 + timedelta(days=weeks * 7 + i)).weekday() < 5:
            count += 1
    return count


# ── daily bars (yfinance) — replicates pead_strategy._fetch_daily_bars ────────
def fetch_daily_bars(symbol: str, lookback_days: int = 180) -> list:
    try:
        import yfinance as yf  # type: ignore
        end = date.today()
        start = end - timedelta(days=lookback_days)
        dfr = yf.download(symbol, start=start.isoformat(), end=end.isoformat(),
                          progress=False, auto_adjust=False)
    except Exception as e:  # noqa: BLE001
        log.debug("fetch_daily_bars(%s) failed: %s", symbol, e)
        return []
    if dfr is None or getattr(dfr, "empty", True):
        return []

    def _cell(row, col):
        v = row[col]
        return float(v.iloc[0]) if hasattr(v, "iloc") else float(v)
    bars = []
    for idx, row in dfr.iterrows():
        try:
            d = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
            bars.append(_Bar(d, _cell(row, "Open"), _cell(row, "High"),
                             _cell(row, "Low"), _cell(row, "Close"), _cell(row, "Volume")))
        except Exception:  # noqa: BLE001
            continue
    return bars


# ── SUE plausibility (pre-report) + exact SUE (post-report) ──────────────────
def sue_profile(actuals: list, lookback: int, threshold: float) -> dict:
    """From the chronological realized-EPS series compute the own-history SUE PLAUSIBILITY:
      sue_latest  — SUE of the most-recent PRINTED quarter (realized; standardized_ue of the series).
      sue_stdev   — stdev(UE, trailing `lookback`) — the own-noise denominator.
      sue_hitrate — fraction of the trailing rolling SUEs that exceeded `threshold` (positive surprises).
      sue_plausible — 1 if the name plausibly prints SUE>threshold (hit-rate >=20% OR a recent |SUE|>=thr).
    This is NOT a prediction of the upcoming SUE (we don't have the forthcoming actual) — it is the
    name's tendency to throw surprises large relative to its own noise. Labelled 'SUE plausibility'.
    """
    prof = {"n_quarters": len(actuals), "sue_latest": None, "sue_stdev": None,
            "sue_hitrate": None, "sue_plausible": None}
    ue = unexpected_earnings(actuals)
    if len(ue) >= lookback + 1:
        window = ue[-(lookback + 1):-1]
        try:
            sd = statistics.stdev(window)
            if math.isfinite(sd) and sd != 0.0:
                prof["sue_stdev"] = sd
        except statistics.StatisticsError:
            pass
    prof["sue_latest"] = standardized_ue(actuals, lookback=lookback)
    sues = []
    for k in range(lookback + 5, len(actuals) + 1):
        s = standardized_ue(actuals[:k], lookback=lookback)
        if s is not None:
            sues.append(s)
    if sues:
        prof["sue_hitrate"] = sum(1 for s in sues if s > threshold) / len(sues)
        max_recent = max((abs(s) for s in sues[-8:]), default=0.0)
        prof["sue_plausible"] = 1 if (prof["sue_hitrate"] >= 0.20 or max_recent >= threshold) else 0
    return prof


def exact_sue_post_report(eps_rows: list, actuals: list, rd_date: date,
                          cal_actual, lookback: int):
    """The EXACT SUE for the just-printed quarter. If EODHD fundamentals already carry this report
    (latest history report_date >= rd_date) the series is current -> standardized_ue(actuals). If the
    fundamentals feed lags but the CALENDAR gave us the actual, append it so post-announcement SUE is
    available immediately."""
    latest_rd = eps_rows[-1].report_date if eps_rows else None
    if latest_rd and rd_date and latest_rd >= rd_date:
        return standardized_ue(actuals, lookback=lookback)
    if cal_actual is not None and actuals:
        try:
            return standardized_ue(actuals + [float(cal_actual)], lookback=lookback)
        except Exception:  # noqa: BLE001
            return None
    return None


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ── shard split (seasonal load relief) ───────────────────────────────────────
def _shard_of(code: str, shards: int) -> int:
    """STABLE partition of a ticker into [0, shards). Uses crc32 (deterministic ACROSS processes) —
    builtin hash() is PYTHONHASHSEED-salted per process and would silently break the AM/PM union."""
    if shards <= 1:
        return 0
    return zlib.crc32(code.encode("utf-8")) % shards


def resolve_shard(spec: str, shards: int) -> int:
    """Which shard THIS run processes. 'auto' => derive from the UTC hour (the 11:00 AM timer run -> 0,
    the 21:00 PM run -> 1); 'am'/'pm' => 0/1; a numeric string => that index (mod shards)."""
    s = (spec or "auto").strip().lower()
    if shards <= 1:
        return 0
    if s in ("am", "a"):
        return 0
    if s in ("pm", "p"):
        return 1 % shards
    if s.isdigit():
        return int(s) % shards
    # auto: split the day at 16:00 UTC so the 11:00 run is shard 0 and the 21:00 run is shard 1.
    return 0 if datetime.now(timezone.utc).hour < 16 else (1 % shards)


# ── one full refresh ─────────────────────────────────────────────────────────
def refresh() -> dict:
    cfg = load_cfg()
    universe = load_universe(cfg)
    signal_cfg = cfg.get("signal") or {}
    lookback = int(signal_cfg.get("lookback", 8))
    threshold = float(signal_cfg.get("sue_threshold", 1.5))
    screen_params = screen_params_from_config(cfg.get("screen", {}) or {})
    if not universe:
        log.error("refresh: empty universe — aborting (screen would be meaningless)")
        return {"error": "empty_universe"}

    key = load_eodhd_key()
    provider = EarningsProvider(api_key=key, db_url=None)  # db_url=None => NO engine-DB writes
    today = date.today()
    dfrom = (today - timedelta(days=BACK_DAYS)).isoformat()
    dto = (today + timedelta(days=FWD_DAYS)).isoformat()
    cal = eodhd_calendar(key, dfrom, dto)
    log.info("calendar %s..%s -> %d rows (all exchanges)", dfrom, dto, len(cal))

    with wdb.connect_engine_ro() as ro:
        held = wdb.held_pead_symbols(ro)
    log.info("already-held PEAD names: %d", len(held))

    # in-universe US reporters in-window (dedup by code+report_date)
    targets = []
    seen = set()
    for e in cal:
        code_full = str(e.get("code", ""))
        if not code_full.endswith(".US"):
            continue
        code = code_full[:-3].upper()
        if code not in universe:
            continue
        rd = e.get("report_date")
        if not rd:
            continue
        keyt = (code, rd[:10])
        if keyt in seen:
            continue
        seen.add(keyt)
        targets.append((code, rd[:10], e))
    targets.sort(key=lambda t: (t[1], t[0]))
    # Shard split: keep only this run's half of the in-universe list (crc32(code)%shards). The upsert
    # is INSERT OR REPLACE by (code, report_date), so the OTHER shard's rows written by the complementary
    # timer run stay intact — the table always holds BOTH halves; only this half is refreshed now.
    full_in_universe = len(targets)
    shard = resolve_shard(WATCH_SHARD, WATCH_SHARDS)
    if WATCH_SHARDS > 1:
        targets = [t for t in targets if _shard_of(t[0], WATCH_SHARDS) == shard]
        log.info("shard %d/%d (spec=%s): processing %d of %d in-universe reporters this run",
                 shard, WATCH_SHARDS, WATCH_SHARD, len(targets), full_in_universe)
    if MAX_NAMES and len(targets) > MAX_NAMES:
        log.info("PEAD_MAX_NAMES=%d -> processing first %d of %d in-universe reporters",
                 MAX_NAMES, MAX_NAMES, len(targets))
        targets = targets[:MAX_NAMES]

    stats = {"in_universe_reporters": len(targets), "full_in_universe": full_in_universe,
             "shard": shard, "shards": WATCH_SHARDS, "upcoming": 0, "reported": 0,
             "screen_pass": 0, "screen_fail": 0, "held": 0, "no_bars": 0}
    rows_out = []
    for i, (code, rd, e) in enumerate(targets, 1):
        if i % 25 == 0:
            log.info("  ...%d/%d processed", i, len(targets))
        rd_date = date.fromisoformat(rd)
        cal_actual = _f(e.get("actual"))
        phase = "reported" if cal_actual is not None else "upcoming"
        try:
            eps_rows = provider.get_quarterly_eps(code) or []
        except Exception as ex:  # noqa: BLE001
            log.debug("get_quarterly_eps(%s): %s", code, ex)
            eps_rows = []
        actuals = [float(q.actual_eps) for q in eps_rows]
        try:
            facts = provider.get_company_facts(code) or {}
        except Exception:  # noqa: BLE001
            facts = {}
        try:
            nxt = provider.get_next_earnings_date(code, asof=rd_date)
        except Exception:  # noqa: BLE001
            nxt = None
        d2n = business_days(rd_date, nxt) if nxt else None
        bars = fetch_daily_bars(code)
        last_close = bars[-1].close if bars else None
        avg_vol = (sum(b.volume for b in bars[-30:]) / min(30, len(bars))) if bars else None
        inp = ScreenInputs(symbol=code, price=last_close, avg_daily_volume_30d=avg_vol,
                           market_cap=facts.get("market_cap"), sector=facts.get("sector"),
                           days_to_next_earnings=d2n)
        screen_ok, reason = passes_screen(inp, screen_params)
        prof = sue_profile(actuals, lookback, threshold)
        computed_sue = (exact_sue_post_report(eps_rows, actuals, rd_date, cal_actual, lookback)
                        if phase == "reported" else None)
        note = None
        if not bars:
            note = "no_bars"
            stats["no_bars"] += 1
        elif prof["n_quarters"] < lookback + 5:
            note = "insufficient_eps_history"
        held_flag = 1 if code in held else 0
        stats["held"] += held_flag
        stats["upcoming" if phase == "upcoming" else "reported"] += 1
        stats["screen_pass" if screen_ok else "screen_fail"] += 1
        rows_out.append({
            "code": code, "report_date": rd, "report_time": e.get("before_after_market"),
            "fiscal_period_end": e.get("date"), "estimate": _f(e.get("estimate")),
            "actual": cal_actual, "difference": _f(e.get("difference")),
            "surprise_pct": _f(e.get("percent")), "in_universe": 1, "already_held": held_flag,
            "screen_ok": 1 if screen_ok else 0, "screen_reason": reason,
            "price": last_close, "avg_vol_30d": avg_vol, "market_cap": _f(facts.get("market_cap")),
            "sector": facts.get("sector"), "days_to_next_earnings": d2n,
            "n_quarters": prof["n_quarters"], "sue_latest": prof["sue_latest"],
            "sue_stdev": prof["sue_stdev"], "sue_hitrate": prof["sue_hitrate"],
            "sue_plausible": prof["sue_plausible"], "computed_sue": computed_sue,
            "phase": phase, "note": note, "fetched_ts": wdb.now_iso(),
        })

    cutoff = (today - timedelta(days=PRUNE_TAIL_DAYS)).isoformat()
    with wdb.connect_rw() as conn:
        n = wdb.upsert_rows(conn, rows_out)
        pruned = wdb.prune_before(conn, cutoff)
        wdb.set_meta(conn, "last_refresh_ts", wdb.now_iso())
        wdb.set_meta(conn, "last_window", f"{dfrom}..{dto}")
        wdb.set_meta(conn, "last_stats", json.dumps(stats))
        wdb.set_meta(conn, "last_shard", f"{shard}/{WATCH_SHARDS}")
    stats["upserted"] = n
    stats["pruned"] = pruned
    log.info("refresh done: %s", json.dumps(stats))
    return stats


# ── modes ─────────────────────────────────────────────────────────────────────
def do_check() -> int:
    wdb.init_schema()
    # prove the engine RO handle + held read, and that the universe parse matches the engine's.
    with wdb.connect_engine_ro() as ro:
        held = wdb.held_pead_symbols(ro)
    cfg = load_cfg()
    uni = load_universe(cfg)
    key_ok = False
    try:
        load_eodhd_key()
        key_ok = True
    except Exception as e:  # noqa: BLE001
        log.error("EODHD key load failed: %s", e)
    print(f"OK: own_db={wdb.watch_db_path()} (schema ready); "
          f"engine_db={wdb.engine_db_path()} opened mode=ro; held PEAD names={len(held)}; "
          f"universe={len(uni)} names; eodhd_key_loaded={key_ok}; NO calendar/screen calls made.")
    return 0 if (uni and key_ok) else 1


def main(argv) -> int:
    mode = argv[1] if len(argv) >= 2 else "--once"
    if mode == "--check":
        return do_check()
    if mode == "--dry-run":
        import tempfile
        os.environ["PEAD_WATCH_DB"] = str(Path(tempfile.gettempdir()) / "pead_earnings_dryrun.db")
        print(f"[dry-run] writing to {os.environ['PEAD_WATCH_DB']}")
    wdb.init_schema()
    stats = refresh()
    print(json.dumps(stats, indent=2))
    return 1 if stats.get("error") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
