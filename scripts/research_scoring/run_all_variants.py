"""Step 4+5 — run baseline + H1–H7 candidates on IS (first 70%) + OOS (30%).

Writes JSON results to data/backtest_runs/scoring_research_<ts>/ for the
report generator to consume.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts" / "research_scoring"))

from edge_inventory import BarIndex  # noqa: E402
from replay import (  # noqa: E402
    VariantConfig,
    load_baseline_config,
    load_bars_3m_for_resolution,
    load_synth_ledger,
    run_replay,
)


def build_variants(baseline) -> list[VariantConfig]:
    variants: list[VariantConfig] = [
        VariantConfig(name="baseline", base=baseline),

        # H1: cap weights at 3
        VariantConfig(
            name="H1_cap_weights_at_3", base=baseline,
            factor_weight_overrides={
                "mc_a_blood_diamond": 3,
                "mc_a_red_diamond": 3,
                "mc_b_gold_buy": 3,
                "mc_b_buy_circle_div": 3,
                "mc_b_sell_circle_div": 3,
            },
        ),

        # H2: H1 + up-weight Otter precision
        VariantConfig(
            name="H2_h1_plus_otter_precision_up", base=baseline,
            factor_weight_overrides={
                "mc_a_blood_diamond": 3,
                "mc_a_red_diamond": 3,
                "mc_b_gold_buy": 3,
                "mc_b_buy_circle_div": 3,
                "mc_b_sell_circle_div": 3,
                "water_buy_large": 3,
                "water_sell_large": 3,
                "spoon_bull": 3,
                "spoon_bear": 3,
                "money_bag_top": 3,
                "money_bag_bottom": 3,
            },
        ),

        # H3: asymmetric α=1.5
        VariantConfig(name="H3_asymmetric_alpha_1.5", base=baseline, asymmetric_alpha=1.5),
        VariantConfig(name="H3b_asymmetric_alpha_2.0", base=baseline, asymmetric_alpha=2.0),

        # H4: conviction ratio
        VariantConfig(name="H4_conviction_ratio_0.70", base=baseline,
                      conviction_ratio_threshold=0.70),
        VariantConfig(name="H4b_conviction_ratio_0.80", base=baseline,
                      conviction_ratio_threshold=0.80),

        # H5: family confluence
        VariantConfig(name="H5_premium_3_families", base=baseline,
                      families_required_premium=3),
        VariantConfig(name="H5b_premium_2_standard_2_families", base=baseline,
                      families_required_premium=2,
                      families_required_standard=2),

        # H6: higher min_score
        VariantConfig(name="H6_min_score_7", base=baseline, min_score_override=7),
        VariantConfig(name="H6b_min_score_8_premium_12", base=baseline,
                      min_score_override=8, premium_override=12, standard_override=8),

        # H7: H2 + unified cooldown
        VariantConfig(
            name="H7_h2_plus_unified_cooldown", base=baseline,
            factor_weight_overrides={
                "mc_a_blood_diamond": 3,
                "mc_a_red_diamond": 3,
                "mc_b_gold_buy": 3,
                "mc_b_buy_circle_div": 3,
                "mc_b_sell_circle_div": 3,
                "water_buy_large": 3,
                "water_sell_large": 3,
                "spoon_bull": 3,
                "spoon_bear": 3,
                "money_bag_top": 3,
                "money_bag_bottom": 3,
            },
            unified_cooldown=True,
        ),

        # Bonus: combine the strongest filters (H2 + H4 + family confluence)
        VariantConfig(
            name="combo_h2_h4_h5_unified", base=baseline,
            factor_weight_overrides={
                "mc_a_blood_diamond": 3,
                "mc_a_red_diamond": 3,
                "mc_b_gold_buy": 3,
                "mc_b_buy_circle_div": 3,
                "mc_b_sell_circle_div": 3,
                "water_buy_large": 3,
                "water_sell_large": 3,
                "spoon_bull": 3,
                "spoon_bear": 3,
                "money_bag_top": 3,
                "money_bag_bottom": 3,
            },
            conviction_ratio_threshold=0.70,
            families_required_premium=3,
            families_required_standard=2,
            unified_cooldown=True,
        ),
    ]
    return variants


def main() -> None:
    print("loading alerts + bars...")
    alerts = load_synth_ledger()
    bars = load_bars_3m_for_resolution()
    idx = BarIndex.build(bars)
    baseline = load_baseline_config()

    # IS / OOS split is 70/30 chronological on the bars window.
    win_start = datetime.fromtimestamp(idx.ts[0], tz=timezone.utc)
    win_end = datetime.fromtimestamp(idx.ts[-1], tz=timezone.utc)
    total_secs = (win_end - win_start).total_seconds()
    split_ts = win_start + timedelta(seconds=total_secs * 0.7)
    print(f"window: {win_start} - {win_end}")
    print(f"IS:  {win_start} - {split_ts}")
    print(f"OOS: {split_ts} - {win_end}")

    variants = build_variants(baseline)
    results = {"IS": {}, "OOS": {}, "ALL": {}}
    for v in variants:
        print(f"\n=== {v.name} ===")
        for label, start, end in (
            ("IS", win_start, split_ts),
            ("OOS", split_ts, win_end),
            ("ALL", win_start, win_end),
        ):
            r = run_replay(alerts, idx, v, start=start, end=end, label=v.name)
            results[label][v.name] = r.to_dict()
            d = r.to_dict()
            print(f"  {label:4s}  fires={d['n_fires']:5d}  win={d['win_rate']:.2%}  meanR={d['mean_r']:+.3f}  sumR={d['sum_r']:+.1f}  Sharpe={d['sharpe_r']:+.2f}  PF={d.get('profit_factor')}  tpd={d['trades_per_day']:.2f}")

    out_dir = REPO_ROOT / "data" / "backtest_runs" / f"scoring_research_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nwrote {out_dir / 'results.json'}")
    # Always also write a stable copy
    stable = REPO_ROOT / "data" / "backtest_runs" / "scoring_research_latest.json"
    stable.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"wrote {stable}")


if __name__ == "__main__":
    main()
