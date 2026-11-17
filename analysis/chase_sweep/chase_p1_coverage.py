"""Chase-rule section-3 probe 1 (READ-ONLY, stdlib): dataset coverage.
- entries_enriched.csv: fills, wpx coverage, date span vs the 2026-10-16 holdout split
- positions-level (wallet,cid,oidx) with/without whale price  -> what v2 must recover
- real wallet list for the v2 fetch
"""
import csv, sqlite3, collections, datetime

SCR = r"C:\Users\AA Incorporado\cc-pm-backtest-2026-10-22-wt\scratch"
DB = (SCR + r"\prediction_markets.db").replace("\\", "/")

rows = list(csv.DictReader(open(SCR + r"\entries_enriched.csv", newline="", encoding="utf-8")))
print("entries_enriched rows:", len(rows))
filled = [r for r in rows if r["status"] == "filled"]
print("filled:", len(filled))

def f(r, k):
    v = r.get(k, "")
    try:
        return float(v)
    except (TypeError, ValueError):
        return None

wpx_have = [r for r in filled if f(r, "wpx") is not None]
print("filled with wpx:", len(wpx_have))
cmid_have = [r for r in filled if f(r, "cmid") is not None]
print("filled with cmid (CLV source):", len(cmid_have))
net_have = [r for r in filled if f(r, "net_cf") is not None]
print("filled with net_cf (outcome known):", len(net_have))

# per account-position identity = (acct, wallet, cid?, ...) -- entries csv has no cid; use (acct,cat,ticker,leg,wallet)
# position identity for whale-price purposes = (wallet, ticker, leg) is wrong; whale key = (wallet, condition_id, oidx)
# entries_enriched lacks condition_id -> re-pull from the DB by id.
con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
con.row_factory = sqlite3.Row
dbrows = {r["id"]: (r["wallet"], r["condition_id"], r["outcome_index"]) for r in con.execute(
    "SELECT id, wallet, condition_id, outcome_index FROM pm_subdivision_order WHERE dry_run=0 AND is_exit=0")}
print("db entry rows:", len(dbrows))

# whale-position identities among FILLED copies
pos = collections.OrderedDict()
for r in filled:
    w, cid, oidx = dbrows[int(r["id"])]
    pos.setdefault((w, cid, oidx), []).append(r)
print("distinct whale-positions behind filled copies:", len(pos))
no_wpx_pos = [k for k, v in pos.items() if all(f(r, "wpx") is None for r in v)]
print("whale-positions with NO wpx (v2 must recover):", len(no_wpx_pos))

# account-position = (acct, wallet, cid, oidx)
acct_pos = set()
for r in filled:
    w, cid, oidx = dbrows[int(r["id"])]
    acct_pos.add((r["acct"], w, cid, oidx))
print("account-positions (filled):", len(acct_pos))

# ALL entry attempts (incl no_fill) account-positions -- the 3,067 of the task?
acct_pos_all = set()
for r in rows:
    w, cid, oidx = dbrows[int(r["id"])]
    acct_pos_all.add((r["acct"], w, cid, oidx))
print("account-positions (all attempts):", len(acct_pos_all))

# date span + holdout split (2026-10-16 UTC)
SPLIT = int(datetime.datetime(2026, 10, 16, tzinfo=datetime.timezone.utc).timestamp())
ts = sorted(int(float(r["submitted_ts"])) for r in filled if r["submitted_ts"])
print("filled span:", datetime.datetime.utcfromtimestamp(ts[0]), "->", datetime.datetime.utcfromtimestamp(ts[-1]))
pre = [t for t in ts if t < SPLIT]; post = [t for t in ts if t >= SPLIT]
print("filled pre-2026-10-16:", len(pre), " post:", len(post))

# category mix of filled
cats = collections.Counter(r["cat"] for r in filled)
print("category mix:", dict(cats.most_common()))

# distinct wallets needed for v2 fetch
wallets = sorted({dbrows[int(r["id"])][0] for r in rows})
print("distinct wallets in dataset:", len(wallets))
for w in wallets[:3]:
    print("  sample wallet:", w)

# pm_paper_trade whale-price coverage context (what the backtest used)
n_pt = con.execute("SELECT COUNT(*) FROM pm_paper_trade").fetchone()[0]
print("pm_paper_trade rows:", n_pt)
con.close()
with open(r"C:\Users\AAINCO~1\AppData\Local\Temp\claude\758809bcb234d7b1\43e4cf7d-4856-418a-a247-7081cd0839d4\scratchpad\chase_wallets.txt", "w") as fh:
    fh.write("\n".join(wallets))
print("wallet list written.")
