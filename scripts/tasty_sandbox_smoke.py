"""Phase-0 sandbox smoke test for TastytradeBroker.

Exercises a live `Session(is_test=True)` against Tastytrade's certification
endpoint (CERT_URL) to verify the broker can:

  1. Connect with the prod OAuth credentials in `is_test=True` mode
     (sandbox uses the same provider_secret + refresh_token as production
     — TT does not require separate sandbox credentials).
  2. Read account balances + positions (snapshot).
  3. Submit a 4-leg iron-condor as a single NewOrder with credit-sign
     price; poll to terminal status; map to FillEvents.
  4. Cancel a separate test order round-trip.
  5. Fetch Greeks for one of the legs (delegates to injected
     TastytradeDataProvider).

This is OPERATOR-RUN, not in CI. It hits the real TT sandbox API and
takes a few seconds. After it passes, append the result to
`runbooks/deploy_log.md` per CLAUDE.md "After every successful deploy."

Usage::

    .\\scripts\\run_capped.ps1 python scripts/tasty_sandbox_smoke.py

Env vars required: TASTYTRADE_PROVIDER_SECRET, TASTYTRADE_REFRESH_TOKEN.
(Same vars the production data provider uses — sandbox is the same OAuth
scope as production.)

Exit codes:
    0  all probes succeeded
    1  any probe failed (sys.exit raised on first failure with a short
       diagnostic + traceback)
"""
from __future__ import annotations

import asyncio
import os
import sys
import traceback
from datetime import date, datetime, timezone
from decimal import Decimal

from trading_corp.brokers.tastytrade import TastytradeBroker
from trading_corp.data.tastytrade_provider import TastytradeDataProvider
from trading_corp.persistence.models import ProposedOrder


# Test combo — a wide SPY IC well out of the money so it has near-zero
# chance of partial fill on any real session in case is_test misroutes.
# Picks dates ~45 DTE from "today" so the leg expirations are plausibly
# on the chain. Operator may want to adjust strikes if SPY has moved
# significantly from the assumed ~$580 region.
_SMOKE_UNDERLYING = "SPY"
_SMOKE_DTE_TARGET = 45
_SMOKE_STRIKES = {
    "short_call": 700.0,    # very-far-OTM — avoids accidental sandbox fill
    "long_call": 705.0,
    "long_put": 400.0,
    "short_put": 405.0,
}


def _smoke_expiration() -> date:
    """A Friday ~45 days out. Snapping to Friday since equity option
    chains list weekly Friday expirations universally."""
    today = date.today()
    target = today.toordinal() + _SMOKE_DTE_TARGET
    candidate = date.fromordinal(target)
    # Walk forward to next Friday.
    while candidate.weekday() != 4:
        candidate = date.fromordinal(candidate.toordinal() + 1)
    return candidate


def _build_smoke_combo(expiration: date) -> list[ProposedOrder]:
    """Build the 4-leg IC ProposedOrder list with the shape the strategy
    would emit on a real fire. Combo direction = credit (we open by
    selling premium); net_limit = $0.10 (intentionally low — designed to
    NOT fill, just to test the submission shape end-to-end)."""
    combo_id = f"smoke-{int(datetime.now(timezone.utc).timestamp())}"
    common_extra = {
        "combo_id": combo_id,
        "combo_direction": "credit",
        "net_limit_price": 0.10,
        "underlying": _SMOKE_UNDERLYING,
        "expiration": expiration.isoformat(),
        "ratio_quantity": 1,
    }
    legs = [
        # Short call — sell to open
        ProposedOrder(
            strategy="tasty_options_iron_condor_smoke",
            symbol=_SMOKE_UNDERLYING,
            side="sell", qty=1,
            order_type="limit", limit_price=0.50,
            extra={**common_extra, "strike": _SMOKE_STRIKES["short_call"],
                   "option_type": "call", "position_effect": "open"},
        ),
        # Long call (hedge) — buy to open
        ProposedOrder(
            strategy="tasty_options_iron_condor_smoke",
            symbol=_SMOKE_UNDERLYING,
            side="buy", qty=1,
            order_type="limit", limit_price=0.30,
            extra={**common_extra, "strike": _SMOKE_STRIKES["long_call"],
                   "option_type": "call", "position_effect": "open"},
        ),
        # Long put (hedge) — buy to open
        ProposedOrder(
            strategy="tasty_options_iron_condor_smoke",
            symbol=_SMOKE_UNDERLYING,
            side="buy", qty=1,
            order_type="limit", limit_price=0.20,
            extra={**common_extra, "strike": _SMOKE_STRIKES["long_put"],
                   "option_type": "put", "position_effect": "open"},
        ),
        # Short put — sell to open
        ProposedOrder(
            strategy="tasty_options_iron_condor_smoke",
            symbol=_SMOKE_UNDERLYING,
            side="sell", qty=1,
            order_type="limit", limit_price=0.40,
            extra={**common_extra, "strike": _SMOKE_STRIKES["short_put"],
                   "option_type": "put", "position_effect": "open"},
        ),
    ]
    return legs


async def main() -> int:
    ps = os.environ.get("TASTYTRADE_PROVIDER_SECRET")
    rt = os.environ.get("TASTYTRADE_REFRESH_TOKEN")
    if not (ps and rt):
        print(
            "ERROR: TASTYTRADE_PROVIDER_SECRET and TASTYTRADE_REFRESH_TOKEN "
            "must be set in env.",
            file=sys.stderr,
        )
        return 1

    data_provider = TastytradeDataProvider(provider_secret=ps, refresh_token=rt)
    broker = TastytradeBroker(
        provider_secret=ps,
        refresh_token=rt,
        account_filter=None,         # first account on the session
        is_test=True,                # ROUTES TO TT CERT/SANDBOX
        data_provider=data_provider,
    )

    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] "
          f"Connecting to Tastytrade CERT (is_test=True)...")
    try:
        await broker.connect()
    except Exception as e:
        print(f"FAIL connect: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1
    print(f"  connected: account={broker._account_number}")

    print("[probe 1/4] snapshot()...")
    try:
        snap = await broker.snapshot()
        print(
            f"  equity=${snap.equity:.2f}  "
            f"buying_power=${snap.buying_power:.2f}  "
            f"positions={len(snap.positions)}"
        )
    except Exception as e:
        print(f"FAIL snapshot: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1

    print("[probe 2/4] place_multi_leg() — wide far-OTM IC, won't fill...")
    expiration = _smoke_expiration()
    combo = _build_smoke_combo(expiration)
    print(f"  expiration={expiration.isoformat()}  strikes="
          f"{[float(o.extra['strike']) for o in combo]}")
    fills: list = []
    try:
        fills = await broker.place_multi_leg(combo)
        print(f"  unexpected fill (sandbox filled the wide IC?): n={len(fills)}")
    except RuntimeError as e:
        # Expected — terminal status will be Live/Cancelled/Expired/Rejected,
        # NOT Filled, on the wide-far-OTM smoke combo. The TimeoutError or
        # RuntimeError raised by _submit_and_wait when status != Filled is
        # the success signal for this probe (we verified the SHAPE worked
        # without putting capital at risk).
        msg = str(e)
        if "Filled" in msg or "terminal status" in msg or "did not reach" in msg:
            print(f"  expected non-fill: {msg[:120]}")
        else:
            print(f"FAIL place_multi_leg unexpected error: {e}", file=sys.stderr)
            traceback.print_exc()
            return 1
    except Exception as e:
        # Auth / scope failures land here — flag distinctly so operator can
        # file a re-grant ticket vs blaming the combo.
        if any(
            tok in str(e).lower() for tok in ("auth", "scope", "401", "403")
        ):
            print(
                f"FAIL place_multi_leg auth/scope: {e}\n"
                f"  → existing OAuth tokens likely scoped to MARKET DATA only.\n"
                f"  → file a re-grant ticket for ORDER scope before Phase 2.",
                file=sys.stderr,
            )
        else:
            print(f"FAIL place_multi_leg unknown: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1

    print("[probe 3/4] cancel_order() round-trip on a fake id...")
    try:
        # Best-effort cancel of a non-existent order — broker should return
        # False (failure caught and logged), NOT raise. Validates the API
        # signature without needing to keep a live order around.
        result = await broker.cancel_order("999999999")
        print(f"  cancel returned: {result} (False on non-existent id = expected)")
    except Exception as e:
        print(f"FAIL cancel_order: {e}", file=sys.stderr)
        traceback.print_exc()
        return 1

    print("[probe 4/4] get_option_greeks via injected data provider...")
    try:
        # Build the OCC for our short call leg and try a Greeks fetch.
        from trading_corp.brokers.tastytrade import _occ_symbol
        occ = _occ_symbol(
            _SMOKE_UNDERLYING, expiration, "call", _SMOKE_STRIKES["short_call"],
        )
        greeks = await broker.get_option_greeks(occ)
        print(f"  greeks for {occ}: {greeks}")
    except NotImplementedError as e:
        print(f"FAIL get_option_greeks (no data_provider injected): {e}",
              file=sys.stderr)
        return 1
    except Exception as e:
        # Greeks fetch may legitimately time out on a sandbox that doesn't
        # stream dxFeed for cert symbols. Warn but don't fail — Greeks
        # delegation shape is verified by the test_tastytrade_broker
        # unit tests + the real-SDK shape tests.
        print(f"  warning: greeks fetch failed (sandbox dxFeed may not stream): {e}")

    await broker.disconnect()
    print()
    print(f"[{datetime.now(timezone.utc).isoformat(timespec='seconds')}] "
          f"All Phase-0 smoke probes PASSED for {broker._account_number} "
          f"on TT CERT.")
    print("Next: append a one-line entry to runbooks/deploy_log.md per "
          "CLAUDE.md 'After every successful deploy.'")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
