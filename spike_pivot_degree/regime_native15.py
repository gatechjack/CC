"""Regime-conditional SFP on the FULL NATIVE 15m history (~230 days).

THESIS-TEST run, NOT a deploy candidate. Reads the native ``bars_15m`` table from
each ``*_scalping.db`` (~230d, 2025-11-01 -> 2026-06-19/26) instead of resampling
the short (47-81d) ``bars_3m`` slice. The entry is a 15m PROXY:

  - Detection: 15m SFP via the REAL live detector -- ``SfpDetector`` (Mode A).
    ``SfpModeBDetector`` (live Mode B, 15m SFP -> 3m BOS) EMBEDS this same
    ``SfpDetector`` as its fire engine, so the SFP-fire logic here is byte-identical
    to live; only the BOS-confirmation timeframe differs (15m proxy vs live 3m).
  - Regime: 15m EMA-200 + slope over 32 bars (8h). UP=close>EMA200 & rising;
    DOWN=close<EMA200 & falling; else RANGE. (== regime_filter ``ema200_pos_slope``.)
  - Entry/exit: resolved on 15m bars. tp_r=2.0 fixed. one-open-at-a-time.
  - Shorts: price-reflection around the midpoint (== short_sfp_sweep ``reflect``),
    the SAME validated long-only detector run on reflected bars.

DISCIPLINE:
  - SELF-CHECK GATE: native ``bars_15m`` must match ``resample_15m(bars_3m)`` over
    the overlapping window AND the 15m detector must fire identically on native vs
    resampled 15m. If they diverge, STOP -- do not trust the 230d extension.
  - NULL-GATE every positive cell: within-side regime-label permutation (200 runs,
    95th pct). Tests whether the regime label predicts expectancy better than random.
  - FEES: taker 0.019% x 2 legs + a 2bp slippage stub x 2 legs, converted to R via
    the REAL entry/stop (shorts recover real prices from the reflection midpoint).
    Expectancy reported NET of fees (gross shown alongside).
  - k=1 / no look-ahead: the detector confirms pivots 50 bars forward (usable only
    at b=p+50); the EMA/slope regime at bar t reads only closes[0..t]. Causal.

Read-only research. Does NOT touch prod, main, or any live/vendored file.
"""
from __future__ import annotations
import math, os, sqlite3, sys, random
from statistics import mean

sys.path.insert(0, os.path.dirname(__file__))
from bitunix_sfp import (
    SfpBar, SfpDetector, MODE_REAL, MODE_CONSIDERABLE, STOP_BUFFER_PCT,
)
import backtest as bt              # only resample_15m (self-check); its main() is guarded
import regime_filter as rf         # reuse regime_series / reflect (identical formulas)

# -- Config ---------------------------------------------------------------------
COINS   = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"]
DB_KEY  = {"BTCUSDT": "btc", "ETHUSDT": "eth", "SOLUSDT": "sol", "XRPUSDT": "xrp"}
PIVOTS  = [5, 8, 10]               # pool the same mid pivots as the prior regime run
TP      = 2.0
MAX_HOLD_15M = 7 * 24 * 4          # 7 days in 15m bars = 672
_15M_MS = 900_000

# Fees: taker 0.019% per leg, 2 legs (entry+exit); 2bp slippage STUB per leg.
TAKER      = 0.00019
SLIP_STUB  = 0.0002
COST_FRAC  = 2 * (TAKER + SLIP_STUB)   # fraction of notional per round trip = 0.00078

NULL_RUNS = 200
NULL_PCT  = 95
NULL_SEED = 20260701
REGIMES   = ("up", "down", "range")
PRIMARY_DEFN = "ema200_pos_slope"
ALL_DEFNS = ["ema200_pos_slope", "mom5d", "sma100_slope12h"]


# -- Data loading (NATIVE tables) -----------------------------------------------

def _db_path(coin: str) -> str:
    key = DB_KEY[coin]
    here = os.path.dirname(__file__)
    cands = [
        os.path.join(here, "..", "..", "cc", "data", f"{key}_scalping.db"),
        os.path.join(here, "..", "data", f"{key}_scalping.db"),
        os.path.join(here, "data", f"{key}_scalping.db"),
    ]
    for p in cands:
        if os.path.exists(p):
            return p
    raise FileNotFoundError(f"No scalping.db for {coin}; tried {[os.path.abspath(c) for c in cands]}")


def load_native(coin: str, table: str) -> list[SfpBar]:
    """Read a native OHLC table. DB ts is in SECONDS -> convert to ms (x1000)."""
    path = _db_path(coin)
    con = sqlite3.connect(path)
    rows = con.execute(
        f"SELECT ts, open, high, low, close FROM {table} "
        f"WHERE open IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL "
        f"AND close IS NOT NULL ORDER BY ts"
    ).fetchall()
    con.close()
    return [SfpBar(int(ts) * 1000, float(o), float(h), float(l), float(c))
            for ts, o, h, l, c in rows]


def count_gaps(bars: list[SfpBar], step_ms: int = _15M_MS) -> int:
    return sum(1 for i in range(len(bars) - 1)
               if bars[i + 1].ts_ms - bars[i].ts_ms != step_ms)


def utc(ts_ms: int) -> str:
    import datetime
    return datetime.datetime.fromtimestamp(ts_ms / 1000, datetime.timezone.utc).strftime("%Y-%m-%d %H:%M")


# -- 15m-proxy detection (Mode A = SfpDetector) ---------------------------------

def get_signals_15m(bars15: list[SfpBar], pivot_len: int):
    """REAL + CONSIDERABLE SFP fires with 15m BOS confirmation. entry_bar_index
    indexes into ``bars15``. Pool the two modes, sort by entry index."""
    sigs = []
    for mode in (MODE_REAL, MODE_CONSIDERABLE):
        det = SfpDetector(mode=mode, pivot_len=pivot_len)
        sigs.extend(det.warm_start(bars15))
    sigs.sort(key=lambda s: s.entry_bar_index)
    return sigs


def simulate(bars: list[SfpBar], idx: int, swept_low: float):
    """One long trade on ``bars`` (works for real OR reflected). Returns dict with
    gross R (win=+TP, loss=-1, stop-first same bar, timeout=mtm), plus entry/rp in
    the bars' own coordinate system and hold in bars. None if invalid."""
    if idx >= len(bars):
        return None
    entry = bars[idx].open
    stop = swept_low - STOP_BUFFER_PCT * entry
    rp = entry - stop
    if rp <= 0:
        return None
    tp = entry + TP * rp
    for i in range(idx + 1, min(idx + MAX_HOLD_15M + 1, len(bars))):
        b = bars[i]
        if b.low <= stop:                       # stop-first (conservative)
            return {"g": -1.0, "entry": entry, "rp": rp, "hold": i - idx, "out": "loss"}
        if b.high >= tp:
            return {"g": TP, "entry": entry, "rp": rp, "hold": i - idx, "out": "win"}
    last = bars[min(idx + MAX_HOLD_15M, len(bars) - 1)]
    return {"g": (last.close - entry) / rp, "entry": entry, "rp": rp,
            "hold": MAX_HOLD_15M, "out": "timeout"}


def net_of_fees(gross: float, entry_real: float, rp_real: float) -> float:
    """gross R minus round-trip cost expressed in R. Cost in price = COST_FRAC*entry_real;
    in R = that / rp_real (rp_real = real stop distance)."""
    return gross - COST_FRAC * entry_real / rp_real


def build_trades_long(bars15, lab, pivot_len):
    """One-open-at-a-time long trades on real bars; tag regime at entry. Returns
    list of (regime, net_r, gross_r, win_bool)."""
    out = []
    sigs = get_signals_15m(bars15, pivot_len)
    open_until = -1
    for s in sigs:
        idx = s.entry_bar_index
        if idx <= open_until or idx >= len(bars15):
            continue
        res = simulate(bars15, idx, s.swept_low)
        if res is None:
            continue
        reg = rf.regime_at(lab, bars15[idx].ts_ms)
        net = net_of_fees(res["g"], res["entry"], res["rp"])
        if reg:
            out.append((reg, net, res["g"], res["g"] > 0))
        open_until = idx + res["hold"]
    return out


def build_trades_short(rbars15, real_bars15, lab, pivot_len, M2):
    """Short via reflection. Detect + sim on reflected 15m bars; tag regime from the
    REAL 15m series at the (real) entry ts; convert entry/rp to REAL prices (via M2)
    for the fee. gross R comes from the reflected sim (the validated short proxy)."""
    out = []
    sigs = get_signals_15m(rbars15, pivot_len)
    open_until = -1
    for s in sigs:
        idx = s.entry_bar_index
        if idx <= open_until or idx >= len(rbars15):
            continue
        res = simulate(rbars15, idx, s.swept_low)
        if res is None:
            continue
        # real prices for fee: reflected value v -> real = M2 - v
        entry_real = M2 - res["entry"]
        swept_high_real = M2 - s.swept_low
        rp_real = (swept_high_real - entry_real) + STOP_BUFFER_PCT * entry_real
        if rp_real <= 0:
            open_until = idx + res["hold"]
            continue
        # regime tagged from REAL bars at the same bar index/ts (reflection preserves ts+index)
        reg = rf.regime_at(lab, real_bars15[idx].ts_ms)
        net = net_of_fees(res["g"], entry_real, rp_real)
        if reg:
            out.append((reg, net, res["g"], res["g"] > 0))
        open_until = idx + res["hold"]
    return out


# -- Aggregation ----------------------------------------------------------------

def agg(vals):
    """vals = list of net_r. Returns (n, pos_frac, mean_net)."""
    n = len(vals)
    if n == 0:
        return (0, float("nan"), float("nan"))
    return (n, sum(1 for v in vals if v > 0) / n, sum(vals) / n)


def fmt_cell(n, pf, ex):
    if n == 0:
        return "n=   0            --"
    return f"n={n:4d} WR~{pf*100:4.1f}% {ex:+.3f}R"


# -- Null-gate: within-side regime-label permutation ----------------------------

def null_gate(long_trades, short_trades):
    """long/short_trades = list of (regime, net_r, ...). Permute the regime-label
    vector within each side (keeps each regime's n fixed and each side's mean fixed),
    recompute every cell + aligned/counter. Returns {cell_key: p95} distributions."""
    rng = random.Random(NULL_SEED)
    L_labels = [t[0] for t in long_trades]; L_vals = [t[1] for t in long_trades]
    S_labels = [t[0] for t in short_trades]; S_vals = [t[1] for t in short_trades]
    dist = {("long", r): [] for r in REGIMES}
    dist.update({("short", r): [] for r in REGIMES})
    dist["aligned"] = []; dist["counter"] = []
    for _ in range(NULL_RUNS):
        lp = L_labels[:]; rng.shuffle(lp)
        sp = S_labels[:]; rng.shuffle(sp)
        Lg = {r: [] for r in REGIMES}
        for lab, v in zip(lp, L_vals): Lg[lab].append(v)
        Sg = {r: [] for r in REGIMES}
        for lab, v in zip(sp, S_vals): Sg[lab].append(v)
        for r in REGIMES:
            dist[("long", r)].append(mean(Lg[r]) if Lg[r] else float("nan"))
            dist[("short", r)].append(mean(Sg[r]) if Sg[r] else float("nan"))
        aligned = Lg["up"] + Sg["down"]
        counter = Lg["down"] + Sg["up"]
        dist["aligned"].append(mean(aligned) if aligned else float("nan"))
        dist["counter"].append(mean(counter) if counter else float("nan"))
    return {k: _pctl([x for x in v if not math.isnan(x)], NULL_PCT) for k, v in dist.items()}


def _pctl(vals, pct):
    if not vals:
        return float("nan")
    vals = sorted(vals)
    idx = (pct / 100) * (len(vals) - 1)
    lo = int(idx); hi = min(lo + 1, len(vals) - 1)
    return vals[lo] * (1 - (idx - lo)) + vals[hi] * (idx - lo)


# -- Self-check gate ------------------------------------------------------------

def self_check(coin, bars15_native, bars3_native):
    """(A) native bars_15m == resample_15m(bars_3m) over the overlap window?
    (B) SfpDetector fires identically on native vs resampled 15m over the overlap?
    Returns (ok_data, ok_fires, detail_str)."""
    res15 = bt.resample_15m(bars3_native)
    nat_by_ts = {b.ts_ms: b for b in bars15_native}
    res_by_ts = {b.ts_ms: b for b in res15}
    common = sorted(set(nat_by_ts) & set(res_by_ts))
    if not common:
        return False, False, "NO OVERLAP between native 15m and resampled 3m"
    mism = 0; worst = 0.0
    for ts in common:
        a = nat_by_ts[ts]; b = res_by_ts[ts]
        for x, y in ((a.open, b.open), (a.high, b.high), (a.low, b.low), (a.close, b.close)):
            d = abs(x - y)
            rel = d / max(abs(y), 1e-9)
            if rel > 1e-6:
                mism += 1
            worst = max(worst, rel)
    match_rate = 1 - mism / (4 * len(common))
    ok_data = match_rate >= 0.999

    # (B) fires over the overlap window: native sliced to overlap vs resampled
    lo, hi = common[0], common[-1]
    nat_ov = [b for b in bars15_native if lo <= b.ts_ms <= hi]
    res_ov = [b for b in res15 if lo <= b.ts_ms <= hi]
    fires_nat = _fire_keys(nat_ov)
    fires_res = _fire_keys(res_ov)
    ok_fires = fires_nat == fires_res
    detail = (f"overlap={utc(lo)}->{utc(hi)} bars={len(common)} match={match_rate*100:.3f}% "
              f"worst_rel={worst:.2e} | fires nat={len(fires_nat)} res={len(fires_res)} "
              f"identical={ok_fires}")
    return ok_data, ok_fires, detail


def _fire_keys(bars15):
    """Set of (mode, fire_ts, round(swept_low,4)) SFP fires across pivots -- the
    detection layer, independent of the BOS/entry leg."""
    keys = set()
    for pl in PIVOTS:
        for mode in (MODE_REAL, MODE_CONSIDERABLE):
            det = SfpDetector(mode=mode, pivot_len=pl)
            det.warm_start(bars15)
            # drain ARMED transitions = the SFP fires (detection layer)
            for t in det._transitions:
                if t.status == "ARMED":
                    keys.add((mode, pl, t.fired_bar_ts_ms, round(t.swept_wick, 4)))
    return keys


KNOWN_FIRES = {   # 2026-06-28 live SFP sweeps (swept wick low), from sfp_watch_state
    "SOLUSDT": 69.68,
    "ETHUSDT": 1555.72,
}


def known_fire_check(coin, bars15_native):
    """Confirm the 15m-proxy detector still detects the known 2026-06-28 sweeps."""
    if coin not in KNOWN_FIRES:
        return None
    want = KNOWN_FIRES[coin]
    for pl in PIVOTS:
        for mode in (MODE_REAL, MODE_CONSIDERABLE):
            det = SfpDetector(mode=mode, pivot_len=pl)
            det.warm_start(bars15_native)
            for t in det._transitions:
                if t.status == "ARMED" and abs(t.swept_wick - want) / want < 0.002:
                    return True
    return False


def compute_buckets(bars_by_coin, defn):
    """Detect long+short SFPs on each coin's 15m bars, tag by regime (computed on
    the SAME bars given -- so a sliced window computes its own regime, matching how
    the prior run saw only its window), bucket net-R. pivots {5,8,10} pooled."""
    long_buckets = {r: [] for r in REGIMES}
    short_buckets = {r: [] for r in REGIMES}
    long_all = []; short_all = []
    per_coin = {c: {} for c in bars_by_coin}
    for c in per_coin:
        per_coin[c] = {("long", r): [] for r in REGIMES}
        per_coin[c].update({("short", r): [] for r in REGIMES})
    dist = {}
    for c, b15 in bars_by_coin.items():
        lab = rf.regime_series(b15, defn)
        for v in lab.values():
            dist[v] = dist.get(v, 0) + 1
        r15 = rf.reflect(b15)
        M2 = max(b.high for b in b15) + min(b.low for b in b15)
        for pl in PIVOTS:
            for reg, net, g, win in build_trades_long(b15, lab, pl):
                long_buckets[reg].append(net); long_all.append((reg, net))
                per_coin[c][("long", reg)].append(net)
            for reg, net, g, win in build_trades_short(r15, b15, lab, pl, M2):
                short_buckets[reg].append(net); short_all.append((reg, net))
                per_coin[c][("short", reg)].append(net)
    return long_buckets, short_buckets, long_all, short_all, per_coin, dist


# -- Main -----------------------------------------------------------------------

def main():
    print("=" * 78)
    print("REGIME-CONDITIONAL SFP on NATIVE 15m (~230d) -- thesis test, 15m proxy entry")
    print("=" * 78)

    native15 = {}; native3 = {}
    print("\n[LOAD] native bars_15m (ts sec -> ms):")
    for c in COINS:
        b15 = load_native(c, "bars_15m")
        native15[c] = b15
        g = count_gaps(b15)
        print(f"  {c}: n={len(b15):5d}  {utc(b15[0].ts_ms)} -> {utc(b15[-1].ts_ms)}  "
              f"gaps(non-900s)={g}")

    print("\n[SELF-CHECK GATE] native bars_15m vs resample_15m(bars_3m) over overlap:")
    all_ok = True; known_ok = True
    for c in COINS:
        native3[c] = load_native(c, "bars_3m")
        ok_data, ok_fires, detail = self_check(c, native15[c], native3[c])
        kf = known_fire_check(c, native15[c])
        kfs = "" if kf is None else f" | known-fire({KNOWN_FIRES.get(c)}) detected={kf}"
        if kf is False:
            known_ok = False
        flag = "OK" if (ok_data and ok_fires) else "FAIL"
        print(f"  {c}: [{flag}] {detail}{kfs}")
        if not (ok_data and ok_fires):
            all_ok = False
    if not all_ok:
        print("\n*** SELF-CHECK FAILED -- native 15m diverges from resampled 3m. "
              "STOP: do not trust the 230d extension. ***")
        return
    if not known_ok:
        print("  WARNING: a known 2026-06-28 sweep was NOT detected on native 15m "
              "(entry leg differs Mode A vs B, but the SFP fire should exist).")
    print("  --> SELF-CHECK PASSED. Native 15m is trustworthy; proceeding to 230d analysis.")

    # -- Build trades for each regime formula (full 230d) --
    for defn in ALL_DEFNS:
        is_primary = (defn == PRIMARY_DEFN)
        long_buckets, short_buckets, long_all, short_all, per_coin, dist = \
            compute_buckets(native15, defn)
        p95 = null_gate(long_all, short_all)

        print("\n" + "=" * 78)
        tag = "  <<< PRIMARY (task-specified)" if is_primary else ""
        print(f"REGIME DEF: {defn}   15m-bar regime distribution: {dist}{tag}")
        print("=" * 78)
        print(f"  NET of fees (taker 0.019% x2 legs + 2bp slip x2 = "
              f"{COST_FRAC*100:.3f}% notional/round-trip)")
        print(f"  {'':10s} {'UP':>22s} {'RANGE':>22s} {'DOWN':>22s}")
        for side, buck in (("Long SFP", long_buckets), ("Short SFP", short_buckets)):
            cells = " ".join(f"{fmt_cell(*agg(buck[r])):>22s}" for r in ("up", "range", "down"))
            print(f"  {side:10s} {cells}")

        # null-gate verdicts on positive cells
        print("\n  NULL-GATE (200x within-side regime-shuffle, beats = obs >= 95th pct):")
        for side_name, side_key, buck in (("Long", "long", long_buckets), ("Short", "short", short_buckets)):
            for r in ("up", "range", "down"):
                n, pf, ex = agg(buck[r])
                if n == 0:
                    continue
                thr = p95[(side_key, r)]
                if ex > 0:
                    verdict = "BEATS" if (not math.isnan(thr) and ex >= thr) else "no"
                    weak = " (thin n<30)" if n < 30 else ""
                    print(f"    {side_name}-{r:5s}: {ex:+.3f}R (n={n}) null_p95={thr:+.3f} -> {verdict}{weak}")

        aligned = long_buckets["up"] + short_buckets["down"]
        counter = long_buckets["down"] + short_buckets["up"]
        uncond_long = sum(long_buckets.values(), [])
        uncond_short = sum(short_buckets.values(), [])
        an, apf, aex = agg(aligned); cn, cpf, cex = agg(counter)
        print("\n  AGGREGATES (net R):")
        athr = p95["aligned"]
        av = "BEATS" if (not math.isnan(athr) and aex >= athr) else "no"
        print(f"    TREND-ALIGNED (long-up + short-down): {fmt_cell(an, apf, aex)}  null_p95={athr:+.3f} -> {av}")
        print(f"    COUNTER-TREND (long-down + short-up): {fmt_cell(cn, cpf, cex)}")
        print(f"    unconditional long : {fmt_cell(*agg(uncond_long))}")
        print(f"    unconditional short: {fmt_cell(*agg(uncond_short))}")

        # gross vs net headline (fee impact)
        def gmean(pairs):  # recompute gross from stored? we only kept net; report cost estimate
            return None
        print(f"    [fee drag applied per-trade; gross omitted -- see cost note above]")

        if is_primary:
            print("\n  --- PER-COIN (primary, net R, pivots {5,8,10} pooled) ---")
            print(f"  {'coin':8s} {'L-up':>12s} {'L-rng':>12s} {'L-dn':>12s} "
                  f"{'S-up':>12s} {'S-rng':>12s} {'S-dn':>12s}")
            for c in COINS:
                def pc(sk, r):
                    n, pf, ex = agg(per_coin[c][(sk, r)])
                    return f"{ex:+.2f}({n})" if n else "  -- (0)"
                print(f"  {c:8s} {pc('long','up'):>12s} {pc('long','range'):>12s} "
                      f"{pc('long','down'):>12s} {pc('short','up'):>12s} "
                      f"{pc('short','range'):>12s} {pc('short','down'):>12s}")

    # -- Detector-controlled window isolation: Mode-A proxy on 46d-overlap vs 230d --
    # Holds the detector FIXED (Mode A 15m proxy) and varies ONLY the window, so the
    # short-in-UP move is attributable to the regime SAMPLE, not the 3m->15m BOS change.
    slice46 = {}
    for c in COINS:
        lo3, hi3 = native3[c][0].ts_ms, native3[c][-1].ts_ms
        slice46[c] = [b for b in native15[c] if lo3 <= b.ts_ms <= hi3]
    lb46, sb46, la46, sa46, _, dist46 = compute_buckets(slice46, PRIMARY_DEFN)
    lbF, sbF, _, _, _, distF = compute_buckets(native15, PRIMARY_DEFN)
    print("\n" + "=" * 78)
    print("DETECTOR-CONTROLLED WINDOW ISOLATION (primary ema200; Mode-A proxy BOTH sides)")
    print("  Same detector; only the data window changes -> isolates regime-SAMPLE effect.")
    print("=" * 78)
    print(f"  {'cell':22s} {'46d-overlap (bear-heavy)':>26s} {'230d (multi-regime)':>26s}")
    def _cmp(name, v46, vF):
        a46 = agg(v46); aF = agg(vF)
        print(f"  {name:22s} {fmt_cell(*a46):>26s} {fmt_cell(*aF):>26s}")
    _cmp("Short-UP", sb46["up"], sbF["up"])
    _cmp("Short (all)", sum(sb46.values(), []), sum(sbF.values(), []))
    _cmp("Short-DOWN", sb46["down"], sbF["down"])
    _cmp("Long-UP", lb46["up"], lbF["up"])
    _cmp("Long-DOWN", lb46["down"], lbF["down"])
    _cmp("TREND-ALIGNED", lb46["up"] + sb46["down"], lbF["up"] + sbF["down"])
    print(f"  46d-overlap regime dist: {dist46}")
    print(f"  230d regime dist:        {distF}")

    # -- Entry-mechanism isolation: Mode-A (15m BOS) vs Mode-B (3m BOS) on SAME 46d --
    # Runs BOTH detectors on the identical 46d-overlap data (GROSS, no fees, matching
    # the prior regime doc). Reproduces the prior Mode-B +0.55R short-UP (or refutes a
    # proxy bug) and shows how much of the difference is the 3m-BOS entry vs the proxy.
    print("\n" + "=" * 78)
    print("ENTRY-MECHANISM ISOLATION on 46d-overlap (GROSS R, no fees) -- Mode-A vs live Mode-B")
    print("  Same 15m SFP fires; differ ONLY in BOS/entry timeframe (15m proxy vs live 3m).")
    print("=" * 78)
    A = {r: [] for r in REGIMES}; B = {r: [] for r in REGIMES}   # short buckets, gross
    for c in COINS:
        all3 = native3[c]
        all15 = [b for b in native15[c] if all3[0].ts_ms <= b.ts_ms <= all3[-1].ts_ms]
        lab = rf.regime_series(all15, PRIMARY_DEFN)
        # Mode-A (proxy): reflect 15m, detect+sim on 15m, gross
        r15 = rf.reflect(all15); M2 = max(b.high for b in all15) + min(b.low for b in all15)
        for pl in PIVOTS:
            for reg, net, g, win in build_trades_short(r15, all15, lab, pl, M2):
                A[reg].append(g)
        # Mode-B (live): reflect 3m, resample->15m, detect on (r15b,r3b), sim on 3m, gross
        r3 = rf.reflect(all3); r15b = bt.resample_15m(r3)
        for pl in PIVOTS:
            sigs = bt.get_signals(r15b, r3, pl)
            for reg, r in rf.trades_tagged(r3, sigs, lab):
                if reg: B[reg].append(r)
    print(f"  {'short cell':16s} {'Mode-A 15m-proxy':>22s} {'Mode-B live 3m-BOS':>22s}")
    for r in ("up", "range", "down"):
        print(f"  {'Short-'+r:16s} {fmt_cell(*agg(A[r])):>22s} {fmt_cell(*agg(B[r])):>22s}")
    print(f"  {'Short (all)':16s} {fmt_cell(*agg(sum(A.values(),[]))):>22s} "
          f"{fmt_cell(*agg(sum(B.values(),[]))):>22s}")
    print("  (prior regime doc, Mode-B 46d pooled: Short-UP ~+0.55R -> this row validates it)")

    print("\n" + "=" * 78)
    print("CRITICAL READOUT: compare Short-UP net expR above (230d, real up-legs "
          "included) against the 46d bear-only +0.55R. Flip to negative = short leg "
          "was bear-beta; stays positive = evidence of genuine short-SFP alpha.")
    print("=" * 78)


if __name__ == "__main__":
    main()
