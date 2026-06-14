#!/usr/bin/env python3
"""Pure helpers for the wallet-ops toolchain — NO web3 / azure / network imports.

Everything here is deterministic and unit-tested in the base pytest gate
(tests/test_wallet_ops_core.py). The web3/signing layer (walletops_chain.py)
imports these so the tested code is the code that actually runs. Keep this module
import-light so the tests run without the heavy deps installed.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_DOWN

# ── Polygon (chain 137) constants ───────────────────────────────────────────
POLYGON_CHAIN_ID = 137
USDC_NATIVE = "0x3c499c542cEF5E3811e1192ce70d8cc03d5c3359"   # Circle native USDC (NOT CLOB collateral)
USDC_E = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"        # USDC.e (Polymarket CLOB collateral)
USDC_DECIMALS = 6
SWAP_ROUTER_02 = "0x68b3465833fb72A70ecDF485E0e4C7bD8665Fc45"  # Uniswap V3 SwapRouter02 (Polygon)
QUOTER_V2 = "0x61fFE014bA17989E743c5F6cB21bF9697530B21e"       # Uniswap V3 QuoterV2 (Polygon)
FEE_TIERS = (100, 500, 3000)                                   # 0.01% / 0.05% / 0.3%

# ERC-20 function selectors (keccak256(sig)[:4]) — hand-rolled like the repo's
# read calldata (trading_corp/brokers/polymarket.py); golden-vector tested.
SEL_TRANSFER = "a9059cbb"   # transfer(address,uint256)
SEL_APPROVE = "095ea7b3"    # approve(address,uint256)


# ── Key Vault naming ────────────────────────────────────────────────────────
def funder_secret_name(key_secret_name: str) -> str:
    """Map a *-PRIVATE-KEY KV secret name to its *-FUNDER-ADDRESS sibling.
    e.g. POLYMARKET-PRIVATE-KEY -> POLYMARKET-FUNDER-ADDRESS."""
    suffix = "-PRIVATE-KEY"
    if not key_secret_name.endswith(suffix):
        raise ValueError(f"expected a '{suffix}' KV secret name, got {key_secret_name!r}")
    return key_secret_name[: -len(suffix)] + "-FUNDER-ADDRESS"


# ── amount math ─────────────────────────────────────────────────────────────
def to_units(human, decimals: int) -> int:
    """Human amount -> integer base units. Rejects sub-unit precision."""
    q = Decimal(str(human)) * (Decimal(10) ** decimals)
    if q != q.to_integral_value():
        raise ValueError(f"amount {human!r} has finer precision than {decimals} decimals")
    if q < 0:
        raise ValueError(f"amount {human!r} is negative")
    return int(q)


def from_units(units: int, decimals: int) -> Decimal:
    """Integer base units -> Decimal human amount."""
    return Decimal(units) / (Decimal(10) ** decimals)


# ── ABI calldata (static encodings) ─────────────────────────────────────────
def pad_addr(addr: str) -> str:
    a = str(addr).lower().removeprefix("0x")
    if len(a) != 40:
        raise ValueError(f"expected 20-byte address, got {addr!r}")
    return ("0" * 24) + a


def pad_uint(n: int) -> str:
    if n < 0 or n >= 2 ** 256:
        raise ValueError(f"uint256 out of range: {n}")
    return format(n, "064x")


def erc20_transfer_calldata(to: str, units: int) -> str:
    return "0x" + SEL_TRANSFER + pad_addr(to) + pad_uint(units)


def erc20_approve_calldata(spender: str, units: int) -> str:
    return "0x" + SEL_APPROVE + pad_addr(spender) + pad_uint(units)


# ── slippage / quote selection ──────────────────────────────────────────────
def select_best_tier(quotes: dict[int, int], amount_in: int, tol: float):
    """Pick the fee tier with the best output. Returns (fee, amount_out) or None.

    Returns None (-> caller ABORTS) when no tier has a pool OR the best output is
    below amount_in*(1-tol) — i.e. the pool can't fill within slippage tolerance.
    Never silently picks a worse-than-tolerance tier.
    """
    valid = {f: o for f, o in quotes.items() if o and o > 0}
    if not valid:
        return None
    fee = max(valid, key=lambda k: valid[k])
    out = valid[fee]
    threshold = int(Decimal(amount_in) * (Decimal(1) - Decimal(str(tol))))
    if out < threshold:
        return None
    return (fee, out)


def min_out(best_out: int, tol: float) -> int:
    """amountOutMinimum for the swap tx: floor(best_out * (1 - tol))."""
    return int((Decimal(best_out) * (Decimal(1) - Decimal(str(tol)))).to_integral_value(rounding=ROUND_DOWN))


def effective_slippage(amount_in: int, out: int) -> float:
    """Effective slippage from par (1:1), as a fraction. (amount_in - out)/amount_in."""
    if amount_in == 0:
        return 0.0
    return float((Decimal(amount_in) - Decimal(out)) / Decimal(amount_in))


# ── display helpers ─────────────────────────────────────────────────────────
def wei_to_gwei(wei: int) -> Decimal:
    return Decimal(wei) / (Decimal(10) ** 9)


def polygonscan_tx_url(tx_hash: str) -> str:
    return f"https://polygonscan.com/tx/{tx_hash}"


def polygonscan_addr_url(addr: str) -> str:
    return f"https://polygonscan.com/address/{addr}"


_CONFIRM_ORDER = (
    "action", "network", "from", "to", "token", "amount", "value",
    "data", "nonce", "gas", "maxFeePerGas", "maxPriorityFeePerGas", "est_max_cost",
)


def format_confirmation(details: dict, dry_run: bool = False) -> str:
    """Render the human review block shown before any signing.

    Always called on BOTH paths: on a real run it precedes the y/n prompt; on
    --dry-run it is printed and then nothing is signed (dry-run's whole purpose is
    to SHOW what would be signed). `details` values must be pre-formatted strings/
    numbers. Unknown/None fields are omitted.
    """
    banner = ("DRY RUN — would sign the following (NOT broadcast)" if dry_run
              else "REVIEW — about to SIGN + BROADCAST a real transaction")
    line = "=" * 70
    out = ["", line, f"  {banner}", line]
    for k in _CONFIRM_ORDER:
        v = details.get(k)
        if v is not None:
            out.append(f"  {k:>22} : {v}")
    out.append(line)
    return "\n".join(out)
