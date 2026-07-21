"""PMCC LEAP-leg re-pull (STEP 3) - READ ONLY. Single SELECT; NO writes to the
prod DB. Dumps every robinhood_pmcc proposed_order leg (with parsed
action/pair_id/price) to /tmp/pmcc_legs.csv for LOCAL classification of the B4
close-without-recover sub-types + a data-backed cost_ignorant_leap_roll count.
Opens the DB mode=ro; the only SQL is the SELECT below."""
import sqlite3
import os
import json
import glob
import csv
import subprocess

SELECT_SQL = (
    "SELECT id, ts, symbol, side, limit_price, extra_json "
    "FROM proposed_order WHERE strategy='robinhood_pmcc' ORDER BY ts"
)


def find_db():
    cands = []
    try:
        wd = subprocess.check_output(
            ["systemctl", "show", "-p", "WorkingDirectory", "--value", "trading-corp"],
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        if wd and wd != "/":
            cands.append(os.path.join(wd, "data", "trading_corp.db"))
    except Exception:
        pass
    try:
        out = subprocess.check_output(["pgrep", "-f", "trading_corp"], stderr=subprocess.DEVNULL).split()
        for pid in out[:5]:
            try:
                cands.append(os.path.join(os.readlink("/proc/%s/cwd" % pid.decode()), "data", "trading_corp.db"))
            except Exception:
                pass
    except Exception:
        pass
    for root in ["/home", "/opt", "/srv", "/root"]:
        try:
            cands += glob.glob(root + "/**/data/trading_corp.db", recursive=True)
        except Exception:
            pass
    out = []
    for c in cands:
        if c and c not in out and os.path.exists(c):
            out.append(c)
    return out


def main():
    dbs = find_db()
    if not dbs:
        print("NO DB FOUND")
        return
    db = dbs[0]
    print("USING DB:", db)
    con = sqlite3.connect("file:%s?mode=ro" % db, uri=True)
    cur = con.cursor()
    cur.execute(SELECT_SQL)
    rows = cur.fetchall()
    con.close()

    out = "/tmp/pmcc_legs.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["id", "ts", "symbol", "side", "limit_price", "action",
                    "pair_id", "mark_per_share", "strike", "expiration"])
        for r in rows:
            try:
                ex = json.loads(r[5] or "{}")
            except Exception:
                ex = {}
            w.writerow([r[0], r[1], r[2], r[3], r[4],
                        ex.get("action"), ex.get("pmcc_pair_id"),
                        ex.get("mark_per_share"), ex.get("strike"),
                        ex.get("expiration")])
    print("WROTE", out, "legs:", len(rows))


if __name__ == "__main__":
    main()
