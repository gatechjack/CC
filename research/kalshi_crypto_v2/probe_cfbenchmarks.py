"""Phase-1 first authenticated task: PROBE the Kalshi cfbenchmarks_value WS feed.

Pattern 1 (operator-run one-shot): reads KALSHI_KAREN_API_KEY_ID and
KALSHI_KAREN_PRIVATE_KEY_PEM from PROCESS ENV ONLY (no file fallback; fails
loudly if absent). The operator fetches Karen creds from Azure Key Vault and
runs this; creds never touch repo, disk, or agent session env.

Output is self-contained and VERDICT-FIRST on stdout:
  line 1  = VERDICT: GO | STOP - <reason>
  then    = per-asset detail (index_id resolved y/n, tick rate, trailing-60s y/n)
  then    = diagnostics (indexlist, raw samples, server errors, endpoint/sign path)
Progress goes to stderr so pasted stdout is the clean report.

Operator directive: prove the channel with a real subscribe. STOP if the base
is wrong, auth fails, zero ticks arrive, or ANY of BTC/ETH/SOL/XRP has no index
(SOL/XRP unconfirmed in docs). The synthetic-composite fallback is the
operator's decision, not the agent's.

Spec (docs.kalshi.com/websockets/cfbenchmarks-value, verified 2026-08-01):
  base    = wss://external-api-ws.kalshi.com/cfbenchmarks_value  (DEDICATED base,
            NOT trade-api/ws/v2; pykalshi AsyncFeed cannot reach it)
  channel = cfbenchmarks_value ; subscribe param = index_ids
  msg     = {type, sid, seq, msg:{index_id, received_at, data,
            avg_60s_data:{value,window_size,...}, last_60s_windowed_average_15min?}}
  auth    = signed headers KALSHI-ACCESS-KEY/SIGNATURE/TIMESTAMP,
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
ASSETS = ["BTC", "ETH", "SOL", "XRP"]
RUN_SECONDS = 45


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


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


def report(verdict: str, per_asset: dict, diagnostics: list[str]) -> None:
    """Print the self-contained, verdict-first report to stdout."""
    print(f"VERDICT: {verdict}")
    print("\n=== per-asset detail ===")
    hdr = f"{'asset':5} {'index_id':14} {'resolved':9} {'rate/s':>7} {'trailing60s':>11}"
    print(hdr + "\n" + "-" * len(hdr))
    for a in ASSETS:
        d = per_asset.get(a, {})
        print(f"{a:5} {d.get('index_id', '-'):14} {('yes' if d.get('resolved') else 'no'):9} "
              f"{d.get('rate', 0.0):>7.2f} {('yes' if d.get('trailing60s') else 'no'):>11}")
    if diagnostics:
        print("\n=== diagnostics ===")
        for line in diagnostics:
            print(line)
    print(f"\nendpoint={ENDPOINT}  sign_path={SIGN_PATH}  run_seconds={RUN_SECONDS}")


async def run() -> int:
    kid = os.getenv("KALSHI_KAREN_API_KEY_ID")
    pem = os.getenv("KALSHI_KAREN_PRIVATE_KEY_PEM")
    if not kid or not pem:
        report("STOP - creds absent (KALSHI_KAREN_API_KEY_ID / "
               "KALSHI_KAREN_PRIVATE_KEY_PEM not in process env; no file fallback by design)",
               {}, [])
        return 2
    pem = pem.replace("\\n", "\n")

    try:
        import websockets
    except ImportError:
        report("STOP - missing dep 'websockets' (pip install websockets)", {}, [])
        return 3

    ts, sig = make_signer(pem)("GET", SIGN_PATH)
    headers = {"KALSHI-ACCESS-KEY": kid, "KALSHI-ACCESS-SIGNATURE": sig,
               "KALSHI-ACCESS-TIMESTAMP": ts}
    _log(f"connecting {ENDPOINT} (key_id={kid[:6]}..., sign_path={SIGN_PATH})")

    try:
        try:
            ws = await websockets.connect(ENDPOINT, additional_headers=headers,
                                          ping_interval=20, ping_timeout=10)
        except TypeError:
            ws = await websockets.connect(ENDPOINT, extra_headers=headers,
                                          ping_interval=20, ping_timeout=10)
    except Exception as e:
        report(f"STOP - handshake/auth failed ({type(e).__name__}: {e}). "
               "Check SIGN_PATH / auth scheme before any T2 design.", {}, [])
        return 2

    _log("connected; sending indexlist + subscribe (best-guess envelope)")
    await ws.send(json.dumps({"id": 1, "cmd": "indexlist"}))
    await ws.send(json.dumps({"id": 2, "cmd": "subscribe",
                              "params": {"channels": ["cfbenchmarks_value"],
                                         "index_ids": DEFAULT_INDEX_IDS}}))

    counts: dict[str, int] = defaultdict(int)
    first_ts: dict[str, int] = {}
    last_ts: dict[str, int] = {}
    has_avg60: dict[str, bool] = defaultdict(bool)
    avail_index_ids: list[str] = []
    raw_samples: list[str] = []
    errors: list[str] = []

    deadline = time.time() + RUN_SECONDS
    while time.time() < deadline:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=max(0.1, deadline - time.time()))
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
        elif mtype and ("error" in str(mtype) or mtype in ("error", "subscribed", "unsubscribed")):
            errors.append(raw if isinstance(raw, str) else str(raw))

    try:
        await ws.close()
    except Exception:
        pass

    per_asset: dict[str, dict] = {}
    for iid, asset in ASSET_OF.items():
        n = counts.get(iid, 0)
        span = (last_ts.get(iid, 0) - first_ts.get(iid, 0)) / 1000 if n > 1 else 0
        per_asset[asset] = {
            "index_id": iid, "resolved": n > 0,
            "rate": (n - 1) / span if span > 0 else 0.0,
            "trailing60s": has_avg60.get(iid, False),
        }
    resolved = {a for a in ASSETS if per_asset[a]["resolved"]}
    missing = [a for a in ASSETS if a not in resolved]

    if not counts:
        verdict = ("STOP - connected + authed but ZERO cfbenchmarks_value ticks in "
                   f"{RUN_SECONDS}s. Command envelope / subscribe params likely wrong "
                   "(see raw samples + errors below).")
        rc = 2
    elif missing:
        verdict = (f"STOP - no ticks for {missing}. Confirm these have a CF index via "
                   "indexlist; missing-asset fallback is an operator decision.")
        rc = 1
    else:
        verdict = ("GO - all four assets stream cfbenchmarks_value with trailing-60s data. "
                   "T2 logger can build on this base (confirm ~1/s rate below).")
        rc = 0

    diags = [f"indexlist available_index_ids: {avail_index_ids or '(none received)'}"]
    if errors:
        diags.append("server errors / control frames:")
        diags += ["  " + e[:300] for e in errors[:8]]
    diags.append("raw sample messages (first few):")
    diags += ["  " + (s[:280] + ("..." if len(s) > 280 else "")) for s in raw_samples] or ["  (none)"]
    report(verdict, per_asset, diags)
    return rc


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
