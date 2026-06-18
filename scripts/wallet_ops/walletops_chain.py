#!/usr/bin/env python3
"""web3 + Azure Key Vault layer for the wallet-ops toolchain.

Imports web3/eth_account/azure (the heavy deps). Pure logic lives in
walletops_core (imported here so the tested code is what runs). Targets
web3==6.11.0 + eth-account==0.13.1 on Polygon (chain 137).

Key-handling invariants:
  - The PRIVATE KEY is pulled from KV ONLY inside confirm_and_send, AFTER the
    y/n confirmation, and only on a real (non-dry-run) send. Dry-run never
    touches the key.
  - The key is passed only to Account.from_key / sign_transaction, never printed,
    logged, or written, and is dropped from the frame immediately after signing.
  - The RPC URL (embedded Alchemy key) is never printed.
"""
from __future__ import annotations

import walletops_core as core
from walletops_core import (
    POLYGON_CHAIN_ID, QUOTER_V2, SWAP_ROUTER_02, FEE_TIERS,
    from_units, wei_to_gwei, polygonscan_tx_url, format_confirmation,
    funder_secret_name,
)

VAULT = "https://kv-tc-vtwbowt3wtkpy.vault.azure.net/"

_ERC20_ABI = [
    {"name": "decimals", "inputs": [], "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"},
    {"name": "symbol", "inputs": [], "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
    {"name": "allowance", "inputs": [{"name": "o", "type": "address"}, {"name": "s", "type": "address"}],
     "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"name": "balanceOf", "inputs": [{"name": "o", "type": "address"}],
     "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
]
_QUOTER_ABI = [{
    "name": "quoteExactInputSingle",
    "inputs": [{"name": "params", "type": "tuple", "components": [
        {"name": "tokenIn", "type": "address"}, {"name": "tokenOut", "type": "address"},
        {"name": "amountIn", "type": "uint256"}, {"name": "fee", "type": "uint24"},
        {"name": "sqrtPriceLimitX96", "type": "uint160"}]}],
    "outputs": [{"name": "amountOut", "type": "uint256"}, {"name": "sqrtPriceX96After", "type": "uint160"},
                {"name": "initializedTicksCrossed", "type": "uint32"}, {"name": "gasEstimate", "type": "uint256"}],
    "stateMutability": "nonpayable", "type": "function"}]
_ROUTER_ABI = [{
    "name": "exactInputSingle",
    "inputs": [{"name": "params", "type": "tuple", "components": [
        {"name": "tokenIn", "type": "address"}, {"name": "tokenOut", "type": "address"},
        {"name": "fee", "type": "uint24"}, {"name": "recipient", "type": "address"},
        {"name": "amountIn", "type": "uint256"}, {"name": "amountOutMinimum", "type": "uint256"},
        {"name": "sqrtPriceLimitX96", "type": "uint160"}]}],
    "outputs": [{"name": "amountOut", "type": "uint256"}],
    "stateMutability": "payable", "type": "function"}]


# ── Key Vault ───────────────────────────────────────────────────────────────
def _kv_client():
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.secrets import SecretClient
    return SecretClient(vault_url=VAULT, credential=DefaultAzureCredential())


def load_rpc_and_funder(key_secret_name: str):
    """Pull the shared RPC + the funder (public) address for the wallet. No key."""
    c = _kv_client()
    rpc = c.get_secret("POLYGON-RPC-URL").value.strip()           # never printed
    funder = c.get_secret(funder_secret_name(key_secret_name)).value.strip()
    return rpc, funder


def _load_key(key_secret_name: str) -> str:
    """Pull the private key. Called ONLY at signing time, after confirmation."""
    return _kv_client().get_secret(key_secret_name).value.strip()


# ── web3 ────────────────────────────────────────────────────────────────────
def make_w3(rpc: str):
    from web3 import Web3
    from web3.middleware import geth_poa_middleware
    w3 = Web3(Web3.HTTPProvider(rpc))
    w3.middleware_onion.inject(geth_poa_middleware, layer=0)  # Polygon PoS extraData
    if not w3.is_connected():
        raise RuntimeError("Polygon RPC not reachable")
    return w3


def _erc20(w3, token):
    return w3.eth.contract(address=w3.to_checksum_address(token), abi=_ERC20_ABI)


def erc20_decimals(w3, token) -> int:
    return _erc20(w3, token).functions.decimals().call()


def erc20_symbol(w3, token) -> str:
    try:
        return _erc20(w3, token).functions.symbol().call()
    except Exception:
        return "?"


def erc20_allowance(w3, token, owner, spender) -> int:
    return _erc20(w3, token).functions.allowance(
        w3.to_checksum_address(owner), w3.to_checksum_address(spender)).call()


def erc20_balance(w3, token, owner) -> int:
    return _erc20(w3, token).functions.balanceOf(w3.to_checksum_address(owner)).call()


def native_balance(w3, owner) -> int:
    """Native POL (gas-token) balance in wei."""
    return w3.eth.get_balance(w3.to_checksum_address(owner))


def quote_best_tier(w3, token_in, token_out, amount_in, fee_tiers=FEE_TIERS) -> dict:
    """Probe each fee tier's QuoterV2.quoteExactInputSingle (read). Reverting
    tiers (no pool) map to 0. Returns {fee: amount_out}."""
    quoter = w3.eth.contract(address=w3.to_checksum_address(QUOTER_V2), abi=_QUOTER_ABI)
    ti, to = w3.to_checksum_address(token_in), w3.to_checksum_address(token_out)
    quotes = {}
    for fee in fee_tiers:
        try:
            res = quoter.functions.quoteExactInputSingle((ti, to, int(amount_in), int(fee), 0)).call()
            quotes[fee] = int(res[0])
        except Exception:
            quotes[fee] = 0
    return quotes


def swap_exactinputsingle_calldata(w3, token_in, token_out, fee, recipient, amount_in, amount_out_min) -> str:
    router = w3.eth.contract(address=w3.to_checksum_address(SWAP_ROUTER_02), abi=_ROUTER_ABI)
    params = (w3.to_checksum_address(token_in), w3.to_checksum_address(token_out), int(fee),
              w3.to_checksum_address(recipient), int(amount_in), int(amount_out_min), 0)
    try:
        return router.encodeABI(fn_name="exactInputSingle", args=[params])
    except AttributeError:                       # web3 >= 7 renamed it
        return router.encode_abi("exactInputSingle", args=[params])


def build_tx(w3, from_addr, to, value=0, data="0x", fallback_gas=None):
    """Construct an EIP-1559 tx with EXPLICIT gas (estimate*1.2) + fees. Returns
    (tx, gas_note). If estimate_gas reverts and fallback_gas is given, uses the
    fallback (expected during a pre-approval dry-run of the swap) and notes it."""
    from_addr = w3.to_checksum_address(from_addr)
    to = w3.to_checksum_address(to)
    est = {"from": from_addr, "to": to, "value": int(value), "data": data}
    gas_note = None
    try:
        gas = int(w3.eth.estimate_gas(est) * 1.2)
    except Exception as e:
        if fallback_gas is None:
            raise
        gas = int(fallback_gas)
        gas_note = (f"estimate_gas reverted ({type(e).__name__}) — using fallback {gas}; "
                    f"expected pre-approval in a dry-run, real run estimates after approve lands")
    base = w3.eth.get_block("latest")["baseFeePerGas"]
    priority = max(w3.eth.max_priority_fee, w3.to_wei(30, "gwei"))  # Polygon priority-fee floor
    max_fee = base * 2 + priority
    nonce = w3.eth.get_transaction_count(from_addr, "pending")
    tx = {"from": from_addr, "to": to, "value": int(value), "data": data, "gas": gas,
          "maxFeePerGas": max_fee, "maxPriorityFeePerGas": priority,
          "nonce": nonce, "chainId": POLYGON_CHAIN_ID, "type": 2}
    return tx, gas_note


def tx_to_details(w3, tx, action, token=None, amount=None, gas_note=None) -> dict:
    max_cost = tx["gas"] * tx["maxFeePerGas"]
    d = {
        "action": action,
        "network": f"Polygon mainnet (chainId {tx['chainId']})",
        "from": tx["from"], "to": tx["to"],
        "value": f"{from_units(tx['value'], 18)} POL",
        "data": (tx["data"][:46] + "…") if tx.get("data") and len(tx["data"]) > 46 else tx.get("data"),
        "nonce": tx["nonce"], "gas": tx["gas"],
        "maxFeePerGas": f"{wei_to_gwei(tx['maxFeePerGas'])} gwei",
        "maxPriorityFeePerGas": f"{wei_to_gwei(tx['maxPriorityFeePerGas'])} gwei",
        "est_max_cost": f"{from_units(max_cost, 18):.6f} POL (gas * maxFeePerGas)",
    }
    if token:
        d["token"] = token
    if amount:
        d["amount"] = amount
    if gas_note:
        d["data"] = f"{d.get('data')}   [!] {gas_note}"
    return d


def confirm_and_send(w3, key_secret_name, tx, details, dry_run):
    """Print the full confirmation block (ALWAYS — including on dry-run, whose
    purpose is to SHOW what would be signed), then:
      dry_run  -> print '[DRY RUN]' and return None (key never touched).
      else     -> y/n prompt; on 'y' pull the key, assert it derives tx['from']
                  (wrong-key guard), sign, broadcast, print PolygonScan URL, wait
                  for receipt, assert status==1. Key dropped immediately after.
    """
    print(format_confirmation(details, dry_run=dry_run))
    if dry_run:
        print("\n[DRY RUN] nothing signed or broadcast. Private key was NOT read.\n")
        return None
    ans = input("\nSign + broadcast this transaction? [y/N] ").strip().lower()
    if ans not in ("y", "yes"):
        print("Aborted by operator — nothing signed.")
        return None
    key = _load_key(key_secret_name)
    try:
        from eth_account import Account
        acct = Account.from_key(key)
        if acct.address.lower() != tx["from"].lower():
            raise RuntimeError(f"wrong-key guard: key derives {acct.address}, tx.from is {tx['from']}")
        signed = acct.sign_transaction(tx)
        raw = getattr(signed, "raw_transaction", None)
        if raw is None:
            raw = signed.rawTransaction  # eth-account < 0.13
        tx_hash = w3.eth.send_raw_transaction(raw)
    finally:
        key = None  # drop the secret
    h = tx_hash.hex()
    if not h.startswith("0x"):
        h = "0x" + h
    print(f"\nbroadcast: {polygonscan_tx_url(h)}")
    print("waiting for receipt (up to 300s)…")
    rcpt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=300)
    if rcpt.get("status") != 1:
        raise RuntimeError(f"tx {h} REVERTED on-chain (status={rcpt.get('status')}) — see PolygonScan")
    print(f"confirmed in block {rcpt['blockNumber']} (status=1)\n")
    return rcpt
