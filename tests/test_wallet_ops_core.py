"""Unit tests for scripts/wallet_ops/walletops_core.py — pure functions only.

Loaded via importlib (Pattern B, like tests/test_backtest_polymarket_arbitrage.py)
since scripts/wallet_ops/ is not on sys.path. web3-free -> runs in the base gate.
The signing/broadcast/quote paths (walletops_chain.py) are integration-only and
deliberately NOT tested here (no network, no keys in CI).
"""
import importlib.util
from pathlib import Path

import pytest

_CORE = Path(__file__).resolve().parent.parent / "scripts" / "wallet_ops" / "walletops_core.py"
_spec = importlib.util.spec_from_file_location("walletops_core", _CORE)
core = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(core)


class TestAmountMath:
    def test_usdc_6dp(self):
        assert core.to_units("119.4", 6) == 119_400_000

    def test_pol_18dp(self):
        assert core.to_units(5, 18) == 5 * 10 ** 18

    def test_from_units_roundtrip(self):
        assert core.from_units(119_400_000, 6) == __import__("decimal").Decimal("119.4")

    def test_rejects_subunit_precision(self):
        with pytest.raises(ValueError):
            core.to_units("0.0000005", 6)   # 0.5 base units -> not integral

    def test_rejects_negative(self):
        with pytest.raises(ValueError):
            core.to_units("-1", 6)


class TestFunderSecretName:
    def test_arb(self):
        assert core.funder_secret_name("POLYMARKET-PRIVATE-KEY") == "POLYMARKET-FUNDER-ADDRESS"

    def test_pct(self):
        assert core.funder_secret_name("POLYMARKET-COPY-PRIVATE-KEY") == "POLYMARKET-COPY-FUNDER-ADDRESS"

    def test_rejects_non_key_name(self):
        with pytest.raises(ValueError):
            core.funder_secret_name("POLYGON-RPC-URL")


class TestCalldata:
    ADDR = "0x000000000000000000000000000000000000dEaD"
    ADDR_WORD = "0" * 60 + "dead"  # checksum-case is lowercased + left-padded to 32 bytes

    def test_transfer_calldata_golden(self):
        data = core.erc20_transfer_calldata(self.ADDR, 1)
        assert data == "0x" + "a9059cbb" + self.ADDR_WORD + ("0" * 63 + "1")
        assert len(data) == 2 + 8 + 64 + 64

    def test_approve_calldata_golden(self):
        data = core.erc20_approve_calldata(self.ADDR, 119_400_000)
        assert data == "0x" + "095ea7b3" + self.ADDR_WORD + core.pad_uint(119_400_000)

    def test_pad_addr_rejects_bad_length(self):
        with pytest.raises(ValueError):
            core.pad_addr("0x1234")

    def test_pad_uint_overflow(self):
        with pytest.raises(ValueError):
            core.pad_uint(2 ** 256)


class TestSlippageGate:
    AMOUNT_IN = 120_000_000  # 120 USDC, 6dp

    def test_picks_best_tier_within_tolerance(self):
        quotes = {100: 0, 500: 119_400_000, 3000: 119_000_000}
        assert core.select_best_tier(quotes, self.AMOUNT_IN, 0.005) == (500, 119_400_000)

    def test_aborts_when_best_below_tolerance(self):
        # the "too-large amount / thin pool" case -> None -> caller ABORTS
        quotes = {500: 119_000_000}  # 0.83% slippage > 0.5%
        assert core.select_best_tier(quotes, self.AMOUNT_IN, 0.005) is None

    def test_aborts_when_no_pool(self):
        assert core.select_best_tier({100: 0, 500: 0, 3000: 0}, self.AMOUNT_IN, 0.005) is None

    def test_min_out_floors(self):
        assert core.min_out(119_400_000, 0.005) == 118_803_000

    def test_effective_slippage(self):
        assert abs(core.effective_slippage(120_000_000, 119_400_000) - 0.005) < 1e-9


class TestPolToUsdcMarketSlippage:
    # POL -> USDC is a MARKET swap (18dp in / 6dp out, non-1:1 price) so it uses
    # the price-impact gate, not the par gate above. Probe = amount_in // 1000.
    AMOUNT_IN = 1_000_000_000_000_000_000   # 1 POL, 18dp
    PROBE = 1_000_000_000_000_000           # 0.001 POL

    def test_zero_impact_when_full_fill_scales_linearly(self):
        # full fill is exactly 1000x the probe out -> ~0 price impact
        assert abs(core.price_impact_probe(400_000_000, self.AMOUNT_IN, 400_000, self.PROBE)) < 1e-9

    def test_half_percent_impact(self):
        # full fill 0.5% worse per-unit than the near-spot probe
        imp = core.price_impact_probe(398_000_000, self.AMOUNT_IN, 400_000, self.PROBE)
        assert abs(imp - 0.005) < 1e-9

    def test_probe_revert_is_max_impact(self):
        assert core.price_impact_probe(398_000_000, self.AMOUNT_IN, 0, self.PROBE) == 1.0

    def test_zero_full_out_is_max_impact(self):
        assert core.price_impact_probe(0, self.AMOUNT_IN, 400_000, self.PROBE) == 1.0

    def test_selects_best_output_within_impact(self):
        quotes = {100: 0, 500: 398_000_000, 3000: 397_000_000}
        impacts = {100: 1.0, 500: 0.005, 3000: 0.004}
        assert core.select_best_tier_by_impact(quotes, impacts, 0.005) == (500, 398_000_000)

    def test_skips_best_output_when_its_impact_exceeds_tol(self):
        # best-output tier (500) fails impact; a deeper lower-output tier (3000) passes
        quotes = {500: 400_000_000, 3000: 397_000_000}
        impacts = {500: 0.010, 3000: 0.003}
        assert core.select_best_tier_by_impact(quotes, impacts, 0.005) == (3000, 397_000_000)

    def test_aborts_when_all_impacts_exceed_tol(self):
        quotes = {500: 400_000_000, 3000: 397_000_000}
        impacts = {500: 0.010, 3000: 0.008}
        assert core.select_best_tier_by_impact(quotes, impacts, 0.005) is None

    def test_aborts_when_no_pool(self):
        assert core.select_best_tier_by_impact({100: 0, 500: 0}, {100: 1.0, 500: 1.0}, 0.005) is None

    def test_negative_impact_clamped_to_zero(self):
        # fork #2: the real 5-POL artifact — probe out floors 387.6 -> 387, making
        # the full fill look ~0.16% "better than spot". Clamp that noise to 0.
        assert core.price_impact_probe(387_616, self.AMOUNT_IN, 387, self.PROBE) == 0.0

    def test_clamp_does_not_mask_real_positive_impact(self):
        # a genuine 0.5% impact is untouched by the clamp
        assert abs(core.price_impact_probe(398_000_000, self.AMOUNT_IN, 400_000, self.PROBE) - 0.005) < 1e-9


class TestFairPriceFloor:
    # fork #3: --min-usdc-out oracle floor. units are USDC base (6dp).
    def test_floor_required_only_on_live(self):
        assert core.floor_required(dry_run=False, min_usdc_out=None) is True    # live + no floor -> required
        assert core.floor_required(dry_run=True, min_usdc_out=None) is False    # dry-run exempt
        assert core.floor_required(dry_run=False, min_usdc_out=0.77) is False   # live + floor -> ok
        assert core.floor_required(dry_run=True, min_usdc_out=0.77) is False

    def test_floor_satisfied(self):
        assert core.floor_satisfied(800_000, None) is True          # no floor -> always ok
        assert core.floor_satisfied(800_000, 770_000) is True       # 0.80 >= 0.77
        assert core.floor_satisfied(770_000, 770_000) is True       # exactly meets
        assert core.floor_satisfied(760_000, 770_000) is False      # below floor -> abort

    def test_effective_min_out_takes_more_protective(self):
        # slippage floor = 1_000_000*(1-0.005) = 995_000
        assert core.effective_min_out(1_000_000, 0.005, None) == 995_000          # no oracle floor
        assert core.effective_min_out(1_000_000, 0.005, 990_000) == 995_000       # slippage stricter
        assert core.effective_min_out(1_000_000, 0.005, 998_000) == 998_000       # oracle floor stricter

    def test_implied_price_per_pol(self):
        # 5 POL (5e18) -> 0.387616 USDC (387616) => 0.0775232 USDC/POL
        import decimal
        got = core.implied_price_per_pol(387_616, 5 * 10 ** 18)
        assert abs(got - decimal.Decimal("0.0775232")) < decimal.Decimal("1e-9")


class TestDisplay:
    def test_polygonscan_urls(self):
        assert core.polygonscan_tx_url("0xabc") == "https://polygonscan.com/tx/0xabc"
        assert core.polygonscan_addr_url("0xdef") == "https://polygonscan.com/address/0xdef"

    def test_confirmation_dry_run_banner_and_fields(self):
        d = {"action": "Transfer native POL", "from": "0xAAA", "to": "0xBBB", "gas": 21000, "value": None}
        s = core.format_confirmation(d, dry_run=True)
        assert "DRY RUN" in s
        assert "Transfer native POL" in s and "0xAAA" in s and "21000" in s
        assert "\n" in s and " value " not in s  # None field omitted

    def test_confirmation_live_banner(self):
        s = core.format_confirmation({"action": "x"}, dry_run=False)
        assert "SIGN + BROADCAST" in s
