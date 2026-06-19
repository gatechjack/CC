"""Phase-0 reconnaissance (read-only): enumerate the corpus signal columns by family,
confirm Otter set + CVD + MACD/EMA/RSI presence, check for open-interest, report time
span + per-TF parity. Informs the Otter-discovery search space. Touches nothing.
"""
import sqlite3, sys
from datetime import datetime, timezone

DB = r"C:\Users\AA Incorporado\cc\data\btc_scalping.db"
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)

def tcols(t):
    return [r[1] for r in con.execute(f"PRAGMA table_info({t})").fetchall()]

def iso(ts):
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()

# --- TF parity + span ---
tables = [r[0] for r in con.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'bars_%' ORDER BY name").fetchall()]
print("=== TF tables (rows, span, #cols) ===")
for t in tables:
    n, mn, mx = con.execute(f"SELECT COUNT(*), MIN(ts), MAX(ts) FROM {t}").fetchone()
    print(f"  {t:<10} rows={n:<7} cols={len(tcols(t)):<4} {iso(mn)} -> {iso(mx)}")

cols = tcols("bars_3m")
OHLCV = {"ts","datetime_utc","open","high","low","close","volume","venue"}

# --- family classification (by name) ---
def fam(c):
    cl = c.lower()
    if cl in OHLCV: return "OHLCV"
    if cl.startswith("mc_") or cl in ("wt_wave_1","wt_wave_2","rsimfi","vwap") \
       or "sommi" in cl or "mfi_bar" in cl or "schaff" in cl or "stoch" in cl \
       or cl in ("red_diamond","blood_diamond","blue_triangle","red_cross","yellow_cross",
                 "bull_candle","long_ema_signal","short_ema_signal","trend_filter_sma",
                 "donchian_high_entry_channel","donchian_low_exit_channel") \
       or "circle" in cl or cl.startswith("wt_") or cl.startswith("plot") \
       or cl.startswith("basis") or cl.startswith("upper") or cl.startswith("lower") \
       or "over_bought" in cl or "over_sold" in cl or cl=="gold_buy_gold_circle":
        return "CYPHER(BANNED)"
    if cl.startswith("cvd"): return "CVD"
    if cl.startswith("ema") : return "EMA"
    if cl.startswith("macd") or cl in ("signal_line","histogram"): return "MACD"
    if cl.startswith("rsi"): return "RSI"
    if cl.startswith("otter") or cl.startswith("spoon") or "money_bag" in cl \
       or cl.startswith("water") or cl.startswith("bias") or "pink_box" in cl \
       or cl.startswith("ribbon") or cl.startswith("super") or cl in ("top_signal","bottom_signal") \
       or cl in ("bull_divergence","bear_divergence") or cl.startswith("vpmo") \
       or "money_flow" in cl:
        return "OTTER"
    if "atr" in cl: return "ATR"
    return "OTHER"

n = con.execute("SELECT COUNT(*) FROM bars_3m").fetchone()[0]
fams = {}
for c in cols:
    fams.setdefault(fam(c), []).append(c)

print(f"\n=== bars_3m columns by family (n={n}) ===")
for f in ("OTTER","CVD","MACD","EMA","RSI","ATR","CYPHER(BANNED)","OHLCV","OTHER"):
    cs = fams.get(f, [])
    print(f"\n[{f}]  ({len(cs)})")
    if f in ("OTTER","CVD","MACD","EMA","RSI"):
        for c in cs:
            nn, nz = con.execute(
                f'SELECT COUNT("{c}"), COUNT(CASE WHEN "{c}"<>0 THEN 1 END) FROM bars_3m').fetchone()
            print(f"    {c:<32} nonnull={nn:<7} nonzero={nz}")
    else:
        print("    " + ", ".join(cs))

# --- open interest check ---
oi = [c for c in cols if "oi" == c.lower() or "open_interest" in c.lower()
      or "openinterest" in c.lower() or "interest" in c.lower()]
print(f"\n=== OPEN INTEREST check ===\n  OI columns found: {oi if oi else 'NONE'}")

# --- ledger-Otter name coverage (do the prod ledger signal names exist as columns?) ---
LEDGER_OTTER = ["otter_buy","otter_sell","spoon_bull","spoon_bear","cvd_bull_flip","cvd_bear_flip",
                "money_bag_bottom","money_bag_top","water_buy_large","water_buy_small",
                "water_sell_large","water_sell_small","bias_bull","bias_bear","pink_box_bull","pink_box_bear"]
colset = set(c.lower() for c in cols)
# also accept cvd_flip_bullish/bearish as the cvd_bull/bear_flip analog
print(f"\n=== ledger-Otter name -> corpus column coverage ===")
for s in LEDGER_OTTER:
    present = s in colset
    alt = ""
    if not present and s == "cvd_bull_flip" and "cvd_flip_bullish" in colset: present, alt = True, " (as cvd_flip_bullish)"
    if not present and s == "cvd_bear_flip" and "cvd_flip_bearish" in colset: present, alt = True, " (as cvd_flip_bearish)"
    print(f"    {s:<20} {'PRESENT'+alt if present else 'ABSENT'}")
con.close()
