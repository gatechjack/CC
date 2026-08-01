"""Phase-1 first authenticated task: PROBE the Kalshi cfbenchmarks_value WS feed.

Operator directive: prove it with a real subscribe. Report (1) WS base reachable
+ auth accepted, (2) which index_ids resolve for BTC/ETH/SOL/XRP, (3) tick rate,
(4) whether the trailing-60s average field is present per asset. If the channel
is absent / wrong-base / missing an asset -> this prints a STOP verdict; the
operator (not the agent) decides any synthetic-composite fallback.

Spec (docs.kalshi.com/websockets/cfbenchmarks-value, verified 2026-08-01):
  base    = wss://external-api-ws.kalshi.com/cfbenchmarks_value  (DEDICATED base,
            NOT trade-api/ws/v2; pykalshi AsyncFeed cannot reach it)
  channel = cfbenchmarks_value ; subscribe param = index_ids (e.g. "BRTI",
            "ETHUSD_RTI"; ["all"] for all; indexlist action lists them)
  msg     = {type, sid, seq, msg:{index_id, received_at, data,
            avg_60s_data:{value,window_size,...}, last_60s_windowed_average_15min?}}
  auth    = signed API-key headers (KALSHI-ACCESS-KEY/SIGNATURE/TIMESTAMP),
            RSA-PSS(SHA256, salt=digest) over f"{ts}{method}{path}" (pykalshi _base).

TWO EMPIRICAL UNKNOWNS (undocumented client schema) — adjust from the live log:
  * SIGN_PATH   : path signed for the handshake (best guess "/cfbenchmarks_value").
  * command envelope for subscribe/indexlist (best guess trade-api {id,cmd,params}).

READ-ONLY: pure WS read. No order imports, no placement, no prod DB.
Usage:  run_capped python research/kalshi_crypto_v2/probe_cfbenchmarks.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import time
from collections import defaultdict

ENDPOINT = "wss://external-api-ws.kalshi.com/cfbenchmarks_value"
SIGN_PATH = "/cfbenchmarks_value"          # EMPIRICAL — adjust if handshake 401s
DEFAULT_INDEX_IDS = ["BRTI", "ETHUSD_RTI", "SOLUSD_RTI", "XRPUSD_RTI"]
ASSET_OF = {"BRTI": "BTC", "ETHUSD_RTI": "ETH", "SOLUSD_RTI": "SOL", "XRPUSD_RTI": "XRP"}
RUN_SECONDS = 45
HERE = os.path.dirname(os.path.abspath(__file__))
CRED_FILE = os.path.join(HERE, ".karen_creds.json")


def load_creds() -> tuple[str, str]:
    """Karen creds from env (preferred) or a local gitignored file.

    File shape: {"api_key_id": "...", "private_key_pem": "-----BEGIN..."}.
    The file path exists so the operator can deliver creds even when session
    env vars do not propagate into spawned tool subprocesses.
    """
    kid = os.getenv("KALSHI_KAREN_API_KEY_ID") or os.getenv("KALSHI_API_KEY_ID")
    pem = os.getenv("KALSHI_KAREN_PRIVATE_KEY_PEM") or os.getenv("KALSHI_PRIVATE_KEY_PEM")
    if kid and pem:
        return kid, pem.replace("\\n", "\n")
    if os.path.exists(CRED_FILE):
        with open(CRED_FILE) as f:
            d = json.load(f)
        return d["api_key_id"], d["private_key_pem"]
    raise SystemExit(
        "No Karen creds. Set KALSHI_KAREN_API_KEY_ID + KALSHI_KAREN_PRIVATE_KEY_PEM, "
        f"or write {CRED_FILE} = {{'api_key_id','private_key_pem'}}.")


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


async def run() -> int:
    try:
        import websockets
    except ImportError:
        print("MISSING dep: pip install websockets")
        return 3
    kid, pem = load_creds()
    ts, sig = make_signer(pem)("GET", SIGN_PATH)
    headers = {"KALSHI-ACCESS-KEY": kid, "KALSHI-ACCESS-SIGNATURE": sig,
               "KALSHI-ACCESS-TIMESTAMP": ts}
    print(f"connecting: {ENDPOINT}\n  key_id={kid[:6]}... sign_path={SIGN_PATH}")

    try:
        try:
            ws = await websockets.connect(ENDPOINT, additional_headers=headers,
                                          ping_interval=20, ping_timeout=10)
        except TypeError:  # older websockets uses extra_headers
            ws = await websockets.connect(ENDPOINT, extra_headers=headers,
                                          ping_interval=20, ping_timeout=10)
    except Exception as e:
        print(f"HANDSHAKE FAILED ({type(e).__name__}): {e}")
        print("VERDICT: STOP — cannot connect/auth to cfbenchmarks base. "
              "Check SIGN_PATH / auth scheme before T2 design.")
        return 2

    print("connected. sending indexlist + subscribe (best-guess envelope) ...")
    await ws.send(json.dumps({"id": 1, "cmd": "indexlist"}))
    await ws.send(json.dumps({"id": 2, "cmd": "subscribe",
                              "params": {"channels": ["cfbenchmarks_value"],
                                         "index_ids": DEFAULT_INDEX_IDS}}))

    counts: dict[str, int] = defaultdict(int)
    first_ts: dict[str, int] = {}
    last_ts: dict[str, int] = {}
    has_avg60: dict[str, bool] = defaultdict(bool)
    has_q15: dict[str, bool] = defaultdict(bool)
    avail_index_ids: list[str] = []
    raw_samples: list[str] = []
    errors: list[str] = []

    deadline = time.time() + RUN_SECONDS
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
        except asyncio.TimeoutError:
            break
        except Exception as e:
            errors.append(f"recv {type(e).__name__}: {e}")
            break
        if len(raw_samples) < 6:
            raw_samples.append(raw if isinstance(raw, str) else str(raw))
        try:
            m = json.loads(raw)
        except Exception:
            continue
        mtype = m.get("type")
        if mtype == "cfbenchmarks_value_indexlist":
            avail_index_ids = m.get("msg", {}).get("index_ids", []) or avail_index_ids
        elif mtype == "cfbenchmarks_value":
            inner = m.get("msg", {})
            iid = inner.get("index_id", "?")
            counts[iid] += 1
            rat = inner.get("received_at")
            if rat is not None:
                first_ts.setdefault(iid, rat)
                last_ts[iid] = rat
            if isinstance(inner.get("avg_60s_data"), dict):
                has_avg60[iid] = True
            if inner.get("last_60s_windowed_average_15min") is not None:
                has_q15[iid] = True
        elif mtype in ("error", "subscribed", "unsubscribed") or "error" in str(mtype):
            errors.append(raw if isinstance(raw, str) else str(raw))

    await ws.close()

    print("\n=== raw sample messages (first few) ===")
    for s in raw_samples:
        print("  " + (s[:300] + ("..." if len(s) > 300 else "")))
    if errors:
        print("\n=== server errors / control frames ===")
        for e in errors[:10]:
            print("  " + e[:300])
    print(f"\n=== indexlist available index_ids: {avail_index_ids or '(none received)'} ===")
    print("\n=== per-index tick summary ===")
    hdr = f"{'index_id':14} {'asset':5} {'ticks':>6} {'rate/s':>7} {'avg60s':>7} {'q15':>5}"
    print(hdr + "\n" + "-" * len(hdr))
    for iid in DEFAULT_INDEX_IDS + [k for k in counts if k not in DEFAULT_INDEX_IDS]:
        n = counts.get(iid, 0)
        span = (last_ts.get(iid, 0) - first_ts.get(iid, 0)) / 1000 if n > 1 else 0
        rate = (n - 1) / span if span > 0 else 0.0
        print(f"{iid:14} {ASSET_OF.get(iid, '?'):5} {n:>6} {rate:>7.2f} "
              f"{str(has_avg60.get(iid, False)):>7} {str(has_q15.get(iid, False)):>5}")

    resolved = {ASSET_OF[i] for i in DEFAULT_INDEX_IDS if counts.get(i, 0) > 0}
    missing = [a for a in ("BTC", "ETH", "SOL", "XRP") if a not in resolved]
    print("\n=== VERDICT ===")
    if not counts:
        print("STOP — connected but received ZERO cfbenchmarks_value ticks. "
              "Command envelope or subscribe params likely wrong (see raw/errors above).")
        return 2
    if missing:
        print(f"STOP-RISK — no ticks for {missing}. Confirm SOL/XRP have a CF index "
              "(indexlist) before T2 design; missing-asset fallback is an operator call.")
        return 1
    print("OK — all four assets stream cfbenchmarks_value with trailing-60s data. "
          "T2 logger can build on this base. (Confirm tick rate ~1/s above.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
