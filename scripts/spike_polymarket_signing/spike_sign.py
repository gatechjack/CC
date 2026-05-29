"""
Throwaway spike: prove Path A (py_clob_client==0.17.5) EIP-712 signing works end-to-end.
- Uses ONLY an ephemeral key generated at runtime (never logs private key).
- NEVER calls post_order or places any real order.
- Do NOT add to production or git.
"""

import sys
import traceback

# ── Step A: Ephemeral key ──────────────────────────────────────────────────────
from eth_account import Account

acct = Account.create()
print(f"[A] Ephemeral address : {acct.address}")
# Only the address is printed; private key is held in memory, never logged.

# ── Step B: ClobClient (plain EOA signer) ─────────────────────────────────────
# Note: constructor is (host, chain_id, key, ...) in 0.17.5 — use kwargs to be explicit.
from py_clob_client.client import ClobClient

HOST = "https://clob.polymarket.com"
CHAIN_ID = 137  # Polygon mainnet

client = ClobClient(
    host=HOST,
    chain_id=CHAIN_ID,
    key=acct.key.hex(),
    # No funder, no signature_type — plain EOA path
)
print(f"[B] ClobClient constructed OK — host={HOST}, chain_id={CHAIN_ID}")

# ── Step C: Fetch a real, active token_id from the public CLOB API ────────────
import requests, json

token_id = None
chosen_question = None

# Try SDK method first
try:
    resp = client.get_sampling_markets()
    # resp may be a dict with 'data' list, or a list directly
    markets = resp.get("data", resp) if isinstance(resp, dict) else resp
    if markets:
        market = markets[0]
        question = market.get("question", market.get("description", "<unknown>"))
        tokens = market.get("tokens", [])
        if tokens:
            token_id = tokens[0].get("token_id")
            chosen_question = question
            print(f"[C] Source: client.get_sampling_markets()")
except Exception as exc:
    print(f"[C] get_sampling_markets() failed: {exc} — falling back to direct HTTP")

# Fallback: direct GET
if token_id is None:
    try:
        r = requests.get(f"{HOST}/markets", timeout=15)
        r.raise_for_status()
        payload = r.json()
        markets_list = payload.get("data", payload) if isinstance(payload, dict) else payload
        for m in markets_list:
            tokens = m.get("tokens", [])
            for t in tokens:
                tid = t.get("token_id")
                if tid:
                    token_id = tid
                    chosen_question = m.get("question", m.get("description", "<unknown>"))
                    break
            if token_id:
                break
        print(f"[C] Source: direct GET {HOST}/markets")
    except Exception as exc:
        print(f"[C] Direct HTTP GET also failed: {exc}")

if token_id is None:
    print("[C] FATAL: could not obtain a token_id from any source. Aborting.")
    sys.exit(1)

print(f"[C] Market question : {chosen_question}")
print(f"[C] Token ID        : {token_id}")

# ── Step D: Build OrderArgs ───────────────────────────────────────────────────
from py_clob_client.clob_types import OrderArgs
from py_clob_client.order_builder.constants import BUY

order_args = OrderArgs(
    token_id=token_id,
    price=0.50,
    size=5,
    side=BUY,
)
print(f"[D] OrderArgs built  : token_id={token_id} price=0.50 size=5 side=BUY")

# ── Step E+F: Sign locally (create_order does NOT post) ──────────────────────
print("[E] Calling client.create_order(order_args) ...")
try:
    signed = client.create_order(order_args)
    print("[E] create_order returned without exception.")
except Exception as exc:
    print(f"[E] create_order RAISED: {type(exc).__name__}: {exc}")
    traceback.print_exc()
    sys.exit(2)

# Print the signed order structure
print("\n[F] Signed order structure:")
try:
    d = signed.dict()
    print(json.dumps(d, indent=2, default=str))
    sig_field = d.get("signature") or d.get("sig")
    if sig_field:
        print(f"\n[F] EIP-712 signature field : {sig_field}")
    else:
        print(f"\n[F] Signature field not found at top level; full structure above.")
except AttributeError:
    print(repr(signed))
    # Try to find a signature attr
    for attr in ("signature", "sig"):
        if hasattr(signed, attr):
            print(f"\n[F] EIP-712 signature ({attr}) : {getattr(signed, attr)}")

print("\n[DONE] Script completed — no order was posted.")
