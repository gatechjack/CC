"""Phase-1 first authenticated task: PROBE the Kalshi cfbenchmarks_value WS feed.

Creds (house pattern, reuses trading_corp/utils/secrets.py mechanism): fetched
at runtime from Azure Key Vault via azure-identity DefaultAzureCredential (the
machine's existing Azure context — Managed Identity on prod, `az login` locally)
+ azure-keyvault-secrets SecretClient against $KEY_VAULT_URI. Secret names use
the underscore->hyphen convention: KALSHI-KAREN-API-KEY-ID /
KALSHI-KAREN-PRIVATE-KEY-PEM. In-process memory ONLY; nothing written to disk,
env files, or gitignore. The env vars KALSHI_KAREN_API_KEY_ID /
KALSHI_KAREN_PRIVATE_KEY_PEM are honored as an override (prod/systemd). Fails
LOUDLY if the vault fetch fails; NEVER falls back to files. Secret values are
never logged or printed (only key ids/values redacted).

Output is self-contained and VERDICT-FIRST on stdout:
  line 1  = VERDICT: GO | STOP - <reason>
  then    = per-asset detail (index_id resolved y/n, tick rate, trailing-60s y/n)
  then    = diagnostics (indexlist, raw samples, server errors, endpoint/sign path)
Progress goes to stderr so pasted stdout is the clean report.

Operator directive: prove the channel with a real subscribe. STOP if the base
is wrong, auth fails, zero ticks arrive, or ANY of BTC/ETH/SOL/XRP has no index
(SOL/XRP unconfirmed in docs). The synthetic-composite fallback is the
operator's decision, not the agent's.

RESOLVED PROTOCOL (empirically verified 2026-08-01, verdict GO on 4/4 assets):
  endpoint  = wss://external-api-ws.kalshi.com/trade-api/ws/v2   (the docs'
              "/cfbenchmarks_value" base 404s on the ELB; the feed is a CHANNEL
              on the standard trade-api ws path, also served on this host).
  auth      = RSA-PSS signed KALSHI-ACCESS-KEY/SIGNATURE/TIMESTAMP over
              f"{ts}GET/trade-api/ws/v2" (pykalshi _base scheme). NOT "apiKey in
              user field" (a docs red herring).
  subscribe = {"id":N,"cmd":"subscribe","params":{"channels":["cfbenchmarks_value"],
              "index_ids":[...]}}  ->  {"type":"subscribed","msg":{"channel":...,"sid":1}}
              ("indexlist" is rejected code=5 Unknown command; unused since the 4
              index_ids are known: BRTI, ETHUSD_RTI, SOLUSD_RTI, XRPUSD_RTI).
  msg       = {type:"cfbenchmarks_value", sid, seq, msg:{index_id, received_at(ms),
              data:"<raw CF JSON frame str>", avg_60s_data:{value,window_size,
              window_start_ts_ms,window_end_ts_exclusive},
              last_60s_windowed_average_15min? (only at :00/:15/:30/:45)}}.

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

# RESOLVED empirically 2026-08-01 (verdict GO on BTC/ETH/SOL/XRP). The docs'
# dedicated "/cfbenchmarks_value" base 404s (AWS ELB); the feed is the
# cfbenchmarks_value CHANNEL on the standard trade-api ws PATH, served on the
# external host, with ordinary RSA-PSS signed headers (NOT "apiKey in user
# field"). Env overrides retained for future iteration.
ENDPOINT = os.getenv("CF_WS_ENDPOINT", "wss://external-api-ws.kalshi.com/trade-api/ws/v2")
SIGN_PATH = os.getenv("CF_WS_SIGN_PATH", "/trade-api/ws/v2")
USE_USERINFO = os.getenv("CF_WS_USERINFO", "0") not in ("0", "false", "")
DEFAULT_INDEX_IDS = ["BRTI", "ETHUSD_RTI", "SOLUSD_RTI", "XRPUSD_RTI"]
ASSET_OF = {"BRTI": "BTC", "ETHUSD_RTI": "ETH", "SOLUSD_RTI": "SOL", "XRPUSD_RTI": "XRP"}
ASSETS = ["BTC", "ETH", "SOL", "XRP"]
RUN_SECONDS = 45


_SECRETS: list[str] = []


def _scrub(s: str) -> str:
    """Remove any known secret substrings (api key id, pem) from output. The
    redaction guardrail: secret values never reach stdout/stderr."""
    for sec in _SECRETS:
        if sec and len(sec) >= 6:
            s = s.replace(sec, "<redacted>")
    return s


def _log(msg: str) -> None:
    print(_scrub(msg), file=sys.stderr, flush=True)


class CredError(Exception):
    """Vault/env credential failure — surfaced as a verdict-first STOP, redacted."""


def load_creds() -> tuple[str, str, str]:
    """Return (api_key_id, private_key_pem, source). In-process only; never
    written to disk. Order: (1) env override (prod/systemd), (2) Azure Key Vault
    via DefaultAzureCredential + SecretClient (house pattern, secrets.py:245).
    Fails LOUDLY (CredError) — no file fallback. Never returns/logs values."""
    kid = os.getenv("KALSHI_KAREN_API_KEY_ID")
    pem = os.getenv("KALSHI_KAREN_PRIVATE_KEY_PEM")
    if kid and pem:
        return kid, pem.replace("\\n", "\n"), "env-override"
    vault_uri = os.getenv("KEY_VAULT_URI")
    if not vault_uri:
        raise CredError("KEY_VAULT_URI not set and KALSHI_KAREN_* not in env")
    try:
        from azure.identity import DefaultAzureCredential
        from azure.keyvault.secrets import SecretClient
    except ImportError as e:
        raise CredError(f"azure SDK missing: {e}")
    try:
        client = SecretClient(vault_url=vault_uri, credential=DefaultAzureCredential())
        kid = client.get_secret("KALSHI-KAREN-API-KEY-ID").value
        pem = client.get_secret("KALSHI-KAREN-PRIVATE-KEY-PEM").value
    except Exception as e:  # auth / forbidden / not-found — message has no secret value
        raise CredError(f"Key Vault fetch failed ({type(e).__name__}: {str(e)[:160]})")
    if not kid or not pem:
        raise CredError("Key Vault returned empty KALSHI-KAREN secret(s)")
    return kid, pem.replace("\\n", "\n"), "keyvault"


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
    print(f"VERDICT: {_scrub(verdict)}")
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
            print(_scrub(line))
    print(f"\nendpoint={ENDPOINT}  sign_path={SIGN_PATH}  run_seconds={RUN_SECONDS}")


async def run() -> int:
    try:
        kid, pem, source = load_creds()
    except CredError as e:
        report(f"STOP - creds unavailable ({e}). Never falls back to files.", {}, [])
        return 2
    _SECRETS.extend([kid, pem])  # scrub these from all subsequent output

    try:
        import websockets
    except ImportError:
        report("STOP - missing dep 'websockets' (pip install websockets)", {}, [])
        return 3

    ts, sig = make_signer(pem)("GET", SIGN_PATH)
    headers = {"KALSHI-ACCESS-KEY": kid, "KALSHI-ACCESS-SIGNATURE": sig,
               "KALSHI-ACCESS-TIMESTAMP": ts}
    # apiKey-in-user (AsyncAPI apiKey/in:user): Basic auth, username=api_key_id,
    # empty password (websockets rejects bare userinfo without a password).
    if USE_USERINFO:
        headers["Authorization"] = "Basic " + base64.b64encode(f"{kid}:".encode()).decode()
    _log(f"creds source={source}; connecting {ENDPOINT} "
         f"(sign_path={SIGN_PATH}, basic_apikey={'on' if USE_USERINFO else 'off'})")

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

    _log("connected; subscribing to cfbenchmarks_value for BTC/ETH/SOL/XRP")
    await ws.send(json.dumps({"id": 1, "cmd": "subscribe",
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
