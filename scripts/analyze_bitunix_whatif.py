"""BitUnix score what-if analyzer.

Decomposes each historical trade's contributions by signal timeframe
(3m vs 4h Cypher vs 1D Cypher vs price-action), then replays under
several alternative scoring policies to compare WR/R outcomes vs the
actual outcome under Phase 3.2 today.

Run on prod (has the full audit + paper_trade_record history).
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
from pathlib import Path

DB = "/home/azureuser/trading_corp/data/trading_corp.db"

# Signal taxonomy
TF_3M = {
    "otter_buy", "otter_sell", "spoon_bull", "spoon_bear",
    "water_buy_small", "water_buy_large", "water_sell_small", "water_sell_large",
    "money_bag_bottom", "money_bag_top",
    "cvd_bull_flip", "cvd_bear_flip",
    "pink_box_bull", "pink_box_bear",
    "bias_bull", "bias_bear",
}
TF_4H = {
    "mc_b_gold_buy", "mc_b_buy_circle_div", "mc_b_buy_circle",
    "mc_b_sell_circle_div", "mc_b_sell_circle",
    "mc_b_buy_dot", "mc_b_sell_dot",
}
TF_1D = {
    "mc_a_bluetriangle", "mc_a_longema", "mc_a_yellow_x",
    "mc_a_red_diamond", "mc_a_blood_diamond", "mc_a_redx",
}
PA = {
    "above_session_vwap", "below_session_vwap",
    "higher_highs_4h", "lower_lows_4h",
    "volume_above_20bar_avg",
}


def classify(contribs):
    """Sum weights of contributions by timeframe bucket."""
    out = defaultdict(int)
    for entry in contribs or []:
        if not (isinstance(entry, (list, tuple)) and len(entry) >= 2):
            continue
        name = entry[0].lower()
        w = int(entry[1])
        if name in TF_3M:
            out["3m"] += w
        elif name in TF_4H:
            out["4h"] += w
        elif name in TF_1D:
            out["1d"] += w
        elif name in PA:
            out["pa"] += w
        else:
            out["other"] += w
    return out


def load_decoded():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row

    # match paper_trade by order_id via would_have_placed audit
    trades = list(db.execute("""
      SELECT pt.ts, pt.side, pt.tier as tier, pt.source_signal,
             pt.actual_r_multiple, pt.result, pt.entry_reference_price,
             pt.order_id
      FROM paper_trade_record pt
      WHERE pt.division='bitunix_futures'
      ORDER BY pt.ts ASC
    """))
    decoded = []
    for t in trades:
        # find the placing bitunix_score_decided audit (order_id match)
        row = db.execute("""
          SELECT payload_json FROM audit_event
          WHERE kind='bitunix_score_decided'
            AND json_extract(payload_json,'$.order_id') = ?
          LIMIT 1
        """, (t["order_id"],)).fetchone()
        if not row:
            continue
        a = json.loads(row["payload_json"])
        b = classify(a.get("buy_contributions") or [])
        s = classify(a.get("sell_contributions") or [])
        bias_4h = "bull" if b["4h"] > s["4h"] else ("bear" if s["4h"] > b["4h"] else "neutral")
        bias_1d = "bull" if b["1d"] > s["1d"] else ("bear" if s["1d"] > b["1d"] else "neutral")
        decoded.append({
            "ts": t["ts"][:19],
            "side": t["side"],
            "tier_actual": t["tier"],
            "actual_r": t["actual_r_multiple"],
            "result": t["result"],
            "trigger": t["source_signal"],
            "entry": t["entry_reference_price"],
            "b_3m": b["3m"], "s_3m": s["3m"],
            "b_4h": b["4h"], "s_4h": s["4h"],
            "b_1d": b["1d"], "s_1d": s["1d"],
            "b_pa": b["pa"], "s_pa": s["pa"],
            "bias_4h": bias_4h,
            "bias_1d": bias_1d,
            "final_buy_orig": a.get("final_buy_score"),
            "final_sell_orig": a.get("final_sell_score"),
        })
    return decoded


def tier_from_score(net, min_fire, prem, std, weak):
    if net < min_fire:
        return "SKIP"
    if net >= prem:
        return "PREMIUM"
    if net >= std:
        return "STANDARD"
    if net >= weak:
        return "WEAK"
    return "SKIP"


def htf_align(side, bias_4h, bias_1d):
    """Returns 'agree' (both bias align), 'partial' (one), 'neutral', 'contra'."""
    want = "bull" if side == "buy" else "bear"
    other = "bear" if want == "bull" else "bull"
    contra = (bias_4h == other) or (bias_1d == other)
    agree = sum(1 for b in (bias_4h, bias_1d) if b == want)
    if contra and agree == 0:
        return "contra"
    if agree == 2:
        return "agree"
    if agree == 1:
        return "partial"
    return "neutral"


def replay(decoded, name, score_fn, tier_fn, fire_filter):
    """Replay each trade under a policy.

    score_fn(d) -> (buy_score, sell_score)
    tier_fn(net, alignment) -> tier
    fire_filter(d, tier, alignment, side) -> bool (true = trade fires)

    Returns a list of (decision, outcome_R) where decision is the tier,
    plus None for trades that would have skipped under this policy.
    """
    results = []
    for d in decoded:
        b, s = score_fn(d)
        if b > s:
            side, net = "buy", b - s
        elif s > b:
            side, net = "sell", s - b
        else:
            side, net = "flat", 0
        if side != d["side"]:
            # different side decision than actually fired — would not have placed THIS trade
            results.append({"d": d, "tier": "DIFFERENT_SIDE", "fired": False, "side_new": side, "net": net})
            continue
        align = htf_align(side, d["bias_4h"], d["bias_1d"])
        tier = tier_fn(net, align)
        fired = fire_filter(d, tier, align, side)
        results.append({"d": d, "tier": tier, "fired": fired, "side_new": side, "net": net, "align": align})
    return results


def summarize(results, label):
    resolved = [r for r in results if r["fired"] and r["d"]["actual_r"] is not None]
    skipped = [r for r in results if not r["fired"]]
    fired_open = [r for r in results if r["fired"] and r["d"]["actual_r"] is None]
    if not resolved:
        print(f"  {label}: 0 trades fired (would skip everything)")
        return
    rs = [r["d"]["actual_r"] for r in resolved]
    wins = sum(1 for r in rs if r > 0)
    avg = sum(rs) / len(rs)
    tot = sum(rs)
    skipped_winning_R = sum(r["d"]["actual_r"] for r in skipped
                            if r["d"]["actual_r"] is not None and r["d"]["actual_r"] > 0)
    skipped_losing_R = sum(r["d"]["actual_r"] for r in skipped
                           if r["d"]["actual_r"] is not None and r["d"]["actual_r"] < 0)
    print(f"  {label}")
    print(f"    fired_resolved={len(resolved):3d}  fired_open={len(fired_open):2d}  skipped={len(skipped):3d}")
    print(f"    WR={wins/len(resolved)*100:5.1f}%  avgR={avg:+.3f}  totalR={tot:+.2f}  (vs current totalR={sum(d['actual_r'] for d in [r['d'] for r in results] if d['actual_r'] is not None):+.2f})")
    print(f"    avoided losing R: {-skipped_losing_R:+.2f}    missed winning R: {-skipped_winning_R:+.2f}")
    # break down by tier
    by_tier = defaultdict(list)
    for r in resolved:
        by_tier[r["tier"]].append(r)
    for t in ("PREMIUM", "STANDARD", "WEAK", "COUNTER"):
        grp = by_tier.get(t, [])
        if grp:
            rs = [r["d"]["actual_r"] for r in grp]
            w = sum(1 for r in rs if r > 0)
            print(f"      {t:8} n={len(grp):2d} wins={w:2d} ({w/len(grp)*100:5.1f}%) avgR={sum(rs)/len(rs):+.3f} totalR={sum(rs):+.2f}")
    # break down by alignment
    by_align = defaultdict(list)
    for r in resolved:
        by_align[r.get("align", "?")].append(r)
    for a in ("agree", "partial", "neutral", "contra"):
        grp = by_align.get(a, [])
        if grp:
            rs = [r["d"]["actual_r"] for r in grp]
            w = sum(1 for r in rs if r > 0)
            print(f"      htf={a:8} n={len(grp):2d} wins={w:2d} ({w/len(grp)*100:5.1f}%) avgR={sum(rs)/len(rs):+.3f}")


def main():
    decoded = load_decoded()
    print(f"=== DECODED {len(decoded)} TRADES ===\n")
    # Per-trade dump
    hdr = f'{"ts":19} {"side":4} {"tier":8} {"trigger":24} {"3m b/s":>7} {"4h b/s":>7} {"1d b/s":>7} {"pa b/s":>7} {"bias 4h/1d":>14} {"R":>5} {"res":>5}'
    print(hdr)
    print("-" * len(hdr))
    for d in decoded:
        r = f'{d["actual_r"]:+.1f}' if d["actual_r"] is not None else "open"
        b3s3 = f'{d["b_3m"]}/{d["s_3m"]}'
        b4s4 = f'{d["b_4h"]}/{d["s_4h"]}'
        b1s1 = f'{d["b_1d"]}/{d["s_1d"]}'
        bpas = f'{d["b_pa"]}/{d["s_pa"]}'
        ba = f'{d["bias_4h"][:4]}/{d["bias_1d"][:4]}'
        print(f'{d["ts"]:19} {d["side"]:4} {d["tier_actual"]:8} {d["trigger"]:24} {b3s3:>7} {b4s4:>7} {b1s1:>7} {bpas:>7} {ba:>14} {r:>5} {(d["result"] or "")[:5]:>5}')

    # ── ACTUAL BASELINE ──
    print("\n=== ACTUAL BASELINE (Phase 3.2 as deployed) ===")
    resolved = [d for d in decoded if d["actual_r"] is not None]
    rs = [d["actual_r"] for d in resolved]
    wins = sum(1 for r in rs if r > 0)
    print(f"  resolved={len(resolved)} WR={wins/len(resolved)*100:.1f}% avgR={sum(rs)/len(rs):+.3f} totalR={sum(rs):+.2f}")

    # ── OPTION A: 3m + PA only score; bias = info but no gate ──
    def score_3m_pa(d):
        return d["b_3m"] + d["b_pa"], d["s_3m"] + d["s_pa"]

    def tier_unchanged(net, align):
        return tier_from_score(net, min_fire=8, prem=12, std=8, weak=5)

    def fire_anything(d, tier, align, side):
        return tier != "SKIP"

    print("\n=== OPTION A — 3m+PA only score, HTF bias not used as gate ===")
    res = replay(decoded, "A", score_3m_pa, tier_unchanged, fire_anything)
    summarize(res, "A. 3m+PA score, no HTF gate, thresholds 8/12 unchanged")

    # ── OPTION A2: same but lower thresholds (3m signals carry less weight than mc_a) ──
    print("\n=== OPTION A2 — 3m+PA score, lower thresholds (min_fire=4, std=4, prem=7) ===")
    def tier_lower(net, align):
        return tier_from_score(net, min_fire=4, prem=7, std=4, weak=2)
    res = replay(decoded, "A2", score_3m_pa, tier_lower, fire_anything)
    summarize(res, "A2. 3m+PA score, thresholds 4/7")

    # ── OPTION B: A2 + skip countertrend (contra HTF bias) ──
    print("\n=== OPTION B — A2 + skip countertrend (contra bias) ===")
    def fire_skip_contra(d, tier, align, side):
        if tier == "SKIP": return False
        if align == "contra": return False
        return True
    res = replay(decoded, "B", score_3m_pa, tier_lower, fire_skip_contra)
    summarize(res, "B. A2 + skip when HTF contradicts")

    # ── OPTION C: A2 + countertrend allowed but only at COUNTER tier (lower size) ──
    print("\n=== OPTION C — A2 + countertrend allowed only at COUNTER tier ===")
    def tier_with_counter(net, align):
        t = tier_from_score(net, min_fire=4, prem=7, std=4, weak=2)
        if t == "SKIP": return t
        if align == "contra":
            return "COUNTER"
        return t
    def fire_with_counter(d, tier, align, side):
        return tier in ("PREMIUM", "STANDARD", "WEAK", "COUNTER")
    res = replay(decoded, "C", score_3m_pa, tier_with_counter, fire_with_counter)
    summarize(res, "C. A2 + COUNTER tier when contra (lower size)")

    # ── OPTION D: A2 + require HTF agreement for PREMIUM, partial OK for STANDARD ──
    print("\n=== OPTION D — A2 + tier-gated by alignment ===")
    def tier_gated(net, align):
        t = tier_from_score(net, min_fire=4, prem=7, std=4, weak=2)
        if t == "PREMIUM" and align != "agree":
            t = "STANDARD"
        if t == "STANDARD" and align == "contra":
            t = "SKIP"
        if t == "WEAK" and align == "contra":
            t = "SKIP"
        return t
    res = replay(decoded, "D", score_3m_pa, tier_gated, fire_anything)
    summarize(res, "D. A2 + alignment downgrades tier")

    # ── OPTION E: A2 + buy-side requires bull-leaning bias (asymmetric) ──
    print("\n=== OPTION E — A2 + buys require bull-leaning bias (sells = symmetric) ===")
    def fire_asym(d, tier, align, side):
        if tier == "SKIP": return False
        if side == "buy" and align in ("contra", "neutral"):
            return False
        if side == "buy" and align == "partial":
            return False  # buys need full HTF agree
        if side == "sell" and align == "contra":
            return False
        return True
    res = replay(decoded, "E", score_3m_pa, tier_lower, fire_asym)
    summarize(res, "E. A2 + buys need HTF agree, sells need not-contra")

    # ── OPTION F: Same as D but ALSO add HTF-derived score multiplier (countertrend = 0.5x size) ──
    # (size impact only — fires same trades as D but at lower size; same WR/R since R is normalized)

    # ── OPTION G: 3m+PA + DECAYED 4h Cypher contributing (weight halved, TTL halved) ──
    print("\n=== OPTION G — 3m+PA + 4h Cypher at HALF weight (sanity check) ===")
    def score_with_half_4h(d):
        return d["b_3m"] + d["b_pa"] + d["b_4h"] // 2, d["s_3m"] + d["s_pa"] + d["s_4h"] // 2
    res = replay(decoded, "G", score_with_half_4h, tier_lower, fire_skip_contra)
    summarize(res, "G. 3m+PA + half-weight 4h, skip contra")

    # ── OPTION H — Phase 3.1-style: HTF gates, 3m score is the trigger threshold ──
    # HTF bias derived from {4h, 1D} contributions. Trigger fires only when:
    #   - 3m+PA score in trigger direction >= MIN_TRIGGER (sparse-data tolerant)
    #   - HTF bias agrees or is partial
    # PREMIUM if both HTFs agree; STANDARD if one agrees, other neutral;
    # COUNTER (if enabled) if any HTF contradicts; SKIP otherwise.
    print("\n=== OPTION H — Phase 3.1-style HTF gate + 3m trigger threshold ===")
    for min_trigger in (2, 3, 4):
        print(f"\n  -- min_trigger_3m={min_trigger} --")
        for counter_enabled in (False, True):
            results = []
            for d in decoded:
                side = d["side"]
                trig_score = (d["b_3m"] + d["b_pa"]) if side == "buy" else (d["s_3m"] + d["s_pa"])
                opp_score = (d["s_3m"] + d["s_pa"]) if side == "buy" else (d["b_3m"] + d["b_pa"])
                net_trigger = trig_score - opp_score
                align = htf_align(side, d["bias_4h"], d["bias_1d"])
                if net_trigger < min_trigger:
                    results.append({"d": d, "tier": "SKIP", "fired": False, "align": align})
                    continue
                if align == "agree":
                    tier = "PREMIUM"
                elif align == "partial":
                    tier = "STANDARD"
                elif align == "neutral":
                    tier = "WEAK"
                else:
                    tier = "COUNTER" if counter_enabled else "SKIP"
                fired = tier != "SKIP"
                results.append({"d": d, "tier": tier, "fired": fired, "align": align, "net": net_trigger})
            label = f"H: trigger>={min_trigger}, counter={'on' if counter_enabled else 'off'}"
            summarize(results, label)

    # ── OPTION I — H but trigger is direction confluence (count of distinct 3m sigs same side) ──
    # Use raw count >= K instead of weight sum. The single-w1 sigs (water_buy_small)
    # shouldn't count as much; need K=2 distinct sigs.
    # NB: we don't have the raw signal list here, only their summed weight. The closest
    # proxy is: trigger requires at least one 3m signal at weight>=2 AND HTF agree.
    # This is what 'min_trigger=2' on Option H already approximates.


if __name__ == "__main__":
    main()
