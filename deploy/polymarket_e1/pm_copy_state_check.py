#!/usr/bin/env python3
"""READ-ONLY OP-track state check for the Polymarket Copy-Trading (PCT) wallet.

Reports, with NO side effects (no signing, no funds, no on-chain writes, no KV
writes):
  OP.A  KV presence of POLYMARKET-COPY-PRIVATE-KEY / -FUNDER-ADDRESS / POLYGON-RPC-URL
        (presence only via list; the PRIVATE KEY value is NEVER fetched or printed)
  OP.B  on-chain USDC.e balance of the funder EOA
  OP.C  ERC-20 allowance (USDC.e -> std/negRisk exchange) and ERC-1155
        isApprovedForAll (CTF -> std/negRisk exchange); NegRisk Adapter shown for info

Run on prod (cwd = trading_corp root so venv python + azure SDK resolve):
    cd trading_corp; venv/bin/python /tmp/pm_copy_state_check.py
"""
import json
import os
import urllib.request

os.environ.setdefault("KEY_VAULT_URI", "https://kv-tc-vtwbowt3wtkpy.vault.azure.net/")
VAULT = os.environ["KEY_VAULT_URI"]

# Polygon (chain 137) live CLOB contracts — mirrors trading_corp/brokers/polymarket_live.py
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
STD_EXCHANGE = "0x4bFb41d5B3570DeFd03C39a9A4D8dE6Bd8B8982E"
NEG_RISK_EXCHANGE = "0xC5d563A36AE78145C45a50134d48A1215220f80a"
CTF = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
NEG_RISK_ADAPTER = "0x78769D50Be1763ed1CA0D5E878D93f05aabff29e"  # carry-forward (not enforced by preflight)
SEL_BALANCE_OF = "0x70a08231"
SEL_ALLOWANCE = "0xdd62ed3e"
SEL_IS_APPROVED = "0xe985e9c5"


def mask(addr: str) -> str:
    return addr[:6] + "…" + addr[-4:] if addr and len(addr) >= 10 else "?"


def pad(addr: str) -> str:
    a = addr.lower().removeprefix("0x")
    return "0" * 24 + a


def eth_call(rpc: str, to: str, data: str) -> int:
    payload = {"jsonrpc": "2.0", "method": "eth_call",
               "params": [{"to": to, "data": data}, "latest"], "id": 1}
    req = urllib.request.Request(rpc, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as r:
        body = json.loads(r.read().decode())
    if body.get("error"):
        raise RuntimeError(body["error"])
    return int(body.get("result", "0x0") or "0x0", 16)


def yn(b: bool) -> str:
    return "SET ✓" if b else "NOT SET ✗"


print("=== Polymarket Copy-Trading (PCT) OP-track state ===\n")

# ---- OP.A : KV presence (names only; no values pulled for the private key) ----
try:
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
    client = SecretClient(vault_url=VAULT, credential=DefaultAzureCredential())
    names = {p.name for p in client.list_properties_of_secrets()}
except Exception as e:  # noqa: BLE001
    print(f"OP.A  KV access FAILED: {type(e).__name__}: {str(e)[:160]}")
    raise SystemExit(1)

pk = "POLYMARKET-COPY-PRIVATE-KEY" in names
fn = "POLYMARKET-COPY-FUNDER-ADDRESS" in names
rp = "POLYGON-RPC-URL" in names
print("OP.A  Key Vault secret presence (kv-tc-vtwbowt3wtkpy):")
print(f"        POLYMARKET-COPY-PRIVATE-KEY    : {'PRESENT' if pk else 'MISSING'}  (value never read)")
print(f"        POLYMARKET-COPY-FUNDER-ADDRESS : {'PRESENT' if fn else 'MISSING'}")
print(f"        POLYGON-RPC-URL                : {'PRESENT' if rp else 'MISSING'}  (shared; value never printed)")
print(f"        (legacy arb POLYMARKET-PRIVATE-KEY present: {'POLYMARKET-PRIVATE-KEY' in names})")

if not (fn and rp):
    print("\nOP.B/OP.C  skipped — need funder + RPC in KV to do on-chain reads.")
    raise SystemExit(0)

funder = client.get_secret("POLYMARKET-COPY-FUNDER-ADDRESS").value.strip()
rpc = client.get_secret("POLYGON-RPC-URL").value.strip()  # used for eth_call; never printed
print(f"\n        funder EOA (masked): {mask(funder)}")

# ---- OP.B : USDC.e balance ----
try:
    bal = eth_call(rpc, USDC_E, SEL_BALANCE_OF + pad(funder)) / 1e6
    print(f"\nOP.B  USDC.e balance of funder : {bal:.6f} USDC.e  ({'FUNDED' if bal > 0 else 'ZERO'})")
except Exception as e:  # noqa: BLE001
    print(f"\nOP.B  balance read FAILED: {type(e).__name__}: {str(e)[:120]}")

# ---- OP.C : approvals (the 4 the live preflight enforces) ----
print("\nOP.C  Approvals enforced by PolymarketLiveBroker preflight:")
for label, spender in (("std exchange", STD_EXCHANGE), ("negRisk exchange", NEG_RISK_EXCHANGE)):
    try:
        a = eth_call(rpc, USDC_E, SEL_ALLOWANCE + pad(funder) + pad(spender))
        print(f"        ERC-20  USDC.e allowance -> {label:<16}: {yn(a > 0)}")
    except Exception as e:  # noqa: BLE001
        print(f"        ERC-20  USDC.e allowance -> {label:<16}: ERR {str(e)[:60]}")
for label, op in (("std exchange", STD_EXCHANGE), ("negRisk exchange", NEG_RISK_EXCHANGE)):
    try:
        a = eth_call(rpc, CTF, SEL_IS_APPROVED + pad(funder) + pad(op))
        print(f"        ERC-1155 CTF approvedForAll -> {label:<16}: {yn(a == 1)}")
    except Exception as e:  # noqa: BLE001
        print(f"        ERC-1155 CTF approvedForAll -> {label:<16}: ERR {str(e)[:60]}")

# ---- carry-forward: NegRisk Adapter (NOT enforced by preflight; info only) ----
print("\n        [info] NegRisk Adapter (carry-forward; not enforced by preflight):")
try:
    a = eth_call(rpc, USDC_E, SEL_ALLOWANCE + pad(funder) + pad(NEG_RISK_ADAPTER))
    b = eth_call(rpc, CTF, SEL_IS_APPROVED + pad(funder) + pad(NEG_RISK_ADAPTER))
    print(f"        ERC-20  USDC.e allowance -> adapter : {yn(a > 0)}")
    print(f"        ERC-1155 CTF approvedForAll -> adapter : {yn(b == 1)}")
except Exception as e:  # noqa: BLE001
    print(f"        adapter read FAILED: {str(e)[:80]}")

print("\n=== end (read-only; no actions taken) ===")
