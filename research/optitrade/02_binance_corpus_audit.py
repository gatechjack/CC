"""
02_binance_corpus_audit.py -- Read-only audit + provenance proof for the large
Binance-perp corpus at Desktop/backtest_corpus/binance_perp_corpus.db.

Proves provenance FROM DATA (not filename):
  (a) source-column uniformity + row counts
  (b) per (symbol,timeframe) coverage, date range, and internal-gap check
  (c) cross-venue divergence vs the Bybit scalping DBs at matching timestamps
      -- if Binance != Bybit on the same bar, it is an independent feed, not a
      relabeled copy.

Run:  python 02_binance_corpus_audit.py
"""
import sqlite3, os, json

BIN = r"C:\Users\AA Incorporado\Desktop\backtest_corpus\binance_perp_corpus.db"
BYBIT = {
    "BTCUSDT": r"C:\Users\AA Incorporado\cc\data\btc_scalping.db",
    "ETHUSDT": r"C:\Users\AA Incorporado\cc\data\eth_scalping.db",
    "SOLUSDT": r"C:\Users\AA Incorporado\cc\data\sol_scalping.db",
    "XRPUSDT": r"C:\Users\AA Incorporado\cc\data\xrp_scalping.db",
}
TF_SECS = {"1m":60,"3m":180,"5m":300,"15m":900,"30m":1800,"1h":3600,"4h":14400,"1d":86400}

def ro(p): return sqlite3.connect(f"file:{p}?mode=ro", uri=True)

def section(t): print("\n" + "="*70 + f"\n{t}\n" + "="*70)

def main():
    c = ro(BIN); cur = c.cursor()

    section("(a) SOURCE-COLUMN PROVENANCE (from data)")
    for r in cur.execute("select source, count(*) from bars group by source"):
        print(f"  source={r[0]!r}  rows={r[1]:,}")
    # is 'source' defaulted or explicitly populated? show DDL
    ddl = cur.execute("select sql from sqlite_master where name='bars'").fetchone()[0]
    print("  bars DDL:", " ".join(ddl.split()))

    section("(b) PER (symbol,timeframe) COVERAGE + GAP CHECK")
    print(f"  {'symbol':9s} {'tf':4s} {'rows':>10s} {'first_utc':20s} {'last_utc':20s} "
          f"{'exp':>9s} {'miss':>7s} {'cov%':>6s}")
    rows = cur.execute("""select symbol,timeframe,count(*) n,min(ts_ms) a,max(ts_ms) b
                          from bars group by symbol,timeframe
                          order by symbol,
                          case timeframe when '1m' then 1 when '3m' then 2 when '15m' then 3
                          when '1h' then 4 when '4h' then 5 when '1d' then 6 else 9 end""").fetchall()
    import datetime as dt
    def u(ms): return dt.datetime.utcfromtimestamp(ms/1000).strftime("%Y-%m-%d %H:%M")
    cov = {}
    for sym,tf,n,a,b in rows:
        step = TF_SECS.get(tf)
        exp = round((b-a)/1000/step)+1 if step else None
        miss = (exp-n) if exp else None
        pct = round(100*n/exp,2) if exp else None
        cov[(sym,tf)] = dict(rows=n, first=u(a), last=u(b), exp=exp, miss=miss, pct=pct)
        print(f"  {sym:9s} {tf:4s} {n:>10,} {u(a):20s} {u(b):20s} "
              f"{(exp or 0):>9,} {(miss if miss is not None else 0):>7,} {pct!s:>6}")

    section("(b2) gaps table (ETL-recorded)")
    gcols=[r[1] for r in cur.execute("pragma table_info(gaps)")]
    gn=cur.execute("select count(*) from gaps").fetchone()[0]
    print(f"  gaps cols={gcols} rows={gn}")

    section("(c) CROSS-VENUE DIVERGENCE vs Bybit (proves independent feed)")
    # Compare BTC/ETH 1h closes at matching timestamps. Bybit ts is in SECONDS.
    for sym in ("BTCUSDT","ETHUSDT"):
        b = ro(BYBIT[sym]); bc = b.cursor()
        # bybit 1h overlap window
        bmin,bmax = bc.execute("select min(ts),max(ts) from bars_1h").fetchone()
        # pull binance 1h in that window
        binrows = dict(cur.execute(
            "select ts_ms/1000, close from bars where symbol=? and timeframe='1h' "
            "and ts_ms/1000 between ? and ?", (sym,bmin,bmax)).fetchall())
        byrows = dict(bc.execute(
            "select ts, close from bars_1h where ts between ? and ?", (bmin,bmax)).fetchall())
        common = sorted(set(binrows)&set(byrows))
        if not common:
            print(f"  {sym}: no overlapping 1h timestamps"); b.close(); continue
        diffs=[abs(binrows[t]-byrows[t]) for t in common]
        rel=[abs(binrows[t]-byrows[t])/byrows[t] for t in common if byrows[t]]
        ident=sum(1 for d in diffs if d==0)
        import statistics as st
        print(f"  {sym}: {len(common):,} overlapping 1h bars | identical closes={ident} "
              f"({100*ident/len(common):.2f}%) | mean|dClose|={st.mean(diffs):.4f} "
              f"| mean rel diff={100*st.mean(rel):.4f}%")
        # show 3 example rows
        for t in common[:3]:
            print(f"      {u(t*1000)}  binance={binrows[t]}  bybit={byrows[t]}")
        b.close()

    c.close()

if __name__ == "__main__":
    main()
