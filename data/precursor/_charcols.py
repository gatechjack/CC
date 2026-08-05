import sqlite3
c = sqlite3.connect("file:C:/Users/AA Incorporado/cc/data/btc_scalping.db?mode=ro", uri=True)
cols = ["plot", "plot_2", "plot_3", "plot_4", "vwap_2", "vpmo", "money_flow_signal", "cvd_close"]
print("last 5 bars (close + candidate cols):")
rows = c.execute(f"SELECT datetime_utc, close, {','.join(cols)} FROM bars_3m ORDER BY ts DESC LIMIT 5").fetchall()
for r in rows:
    print("  ", r[0], "close=%.1f" % r[1], " ".join(f"{cols[i]}={('NULL' if r[i+2] is None else round(r[i+2],2))}" for i in range(len(cols))))
print("\nrange (min / max / nonnull) — is it price-scale (a zone/band) or oscillator?:")
for col in cols:
    mn, mx, nn = c.execute(f"SELECT MIN({col}), MAX({col}), COUNT({col}) FROM bars_3m").fetchone()
    print(f"  {col:<18} min={mn} max={mx} nonnull={nn}")
c.close()
