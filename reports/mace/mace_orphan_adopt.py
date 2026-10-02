#!/usr/bin/env python3
# ORPHAN ADOPT: insert the untracked live XLE 2026-10-30 59.5/58/70/71.5 x2 condor (opened
# 2026-09-16 19:46:38Z by MACE, order 6aaaf21e; DB row lost) into mace_rung as status=open so MACE
# manages it (PT/stop/time-exit). RESERVED prod-DB write; run ONLY under Jack authorization, AND
# ONLY AFTER the leg-sanity guard is deployed + boot-verified (else MACE may immediately false-PT-
# close it on the same XLE-10-30 dead-wing mark). Idempotent (aborts if the rung already exists).
# Values reconstructed from the RH opening fill 6aaaf21e (net credit 0.45), verified against live
# RH positions 2026-10-02 (short 59.5P / long 58P / short 70C / long 71.5C, qty 2).
import sqlite3, json
from datetime import datetime, timezone

DB = "/home/azureuser/trading_corp/data/trading_corp.db"
RID = "mace-XLE-2026-10-30-59.5-58-70-71.5-20260916"
LEGS = json.dumps([
    {"type": "put",  "strike": 59.5, "side": "sell", "effect": "open", "option_id": None, "fill_price": None},
    {"type": "put",  "strike": 58.0, "side": "buy",  "effect": "open", "option_id": None, "fill_price": None},
    {"type": "call", "strike": 70.0, "side": "sell", "effect": "open", "option_id": None, "fill_price": None},
    {"type": "call", "strike": 71.5, "side": "buy",  "effect": "open", "option_id": None, "fill_price": None},
])
ROW = dict(
    rung_id=RID, symbol="XLE", status="open", expiry="2026-10-30", legs_json=LEGS,
    width_dollars=1.5, contracts=2, credit_actual=0.45, max_risk_usd=210.0,
    entry_ts="2026-09-16T19:46:38+00:00", entry_order_id="6aaaf21e-910c-429e-8ef5-1213ad4ca920",
    pt_order_id=None, pt_debit=0.23, exit_ts=None, exit_reason=None, exit_debit=None,
    realized_pnl=None, entry_iso_week="2026-W38", extra_json=None, entry_atm_iv=None,
)
TS = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
BK = "/home/azureuser/mace_orphan_adopt_pre_%s.json" % TS


def rowdict(r):
    return {k: r[k] for k in r.keys()} if r is not None else None


def main():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    pre = c.execute("SELECT rung_id,status FROM mace_rung WHERE rung_id=?", (RID,)).fetchone()
    print("PRE existing row:", rowdict(pre))
    before = [rowdict(r) for r in c.execute(
        "SELECT rung_id,status,contracts FROM mace_rung WHERE symbol='XLE' AND expiry='2026-10-30' "
        "ORDER BY entry_ts")]
    with open(BK, "w") as fh:
        fh.write(json.dumps({"pre_exists": bool(pre), "xle_1030_before": before}, default=str))
    print("BACKUP (pre-state) written:", BK)
    if pre is not None:
        print("ABORT: rung already present (status=%s) -> nothing to adopt (idempotent)" % pre["status"])
        c.close(); return 3

    cols = ",".join(ROW.keys()); ph = ",".join(["?"] * len(ROW))
    cur = c.execute("INSERT INTO mace_rung (%s) VALUES (%s)" % (cols, ph), tuple(ROW.values()))
    c.commit()
    print("INSERT rows affected:", cur.rowcount)

    post = rowdict(c.execute("SELECT * FROM mace_rung WHERE rung_id=?", (RID,)).fetchone())
    print("POST row:", post)
    ok = bool(post and post["status"] == "open" and post["contracts"] == 2
              and abs((post["credit_actual"] or 0) - 0.45) < 1e-9 and post["exit_ts"] is None
              and post["entry_order_id"] == "6aaaf21e-910c-429e-8ef5-1213ad4ca920"
              and abs((post["max_risk_usd"] or 0) - 210.0) < 1e-9)
    print("XLE 10-30 census AFTER (expect 3 rungs: A closing, B open, ORPHAN open):")
    for r in c.execute("SELECT rung_id,status,contracts FROM mace_rung WHERE symbol='XLE' "
                       "AND expiry='2026-10-30' ORDER BY entry_ts"):
        print("  ", rowdict(r))
    print("VERDICT:", "ADOPT OK - rung open + will be managed next manage tick (PT/stop/time-exit)"
          if ok else "*** POST-STATE UNEXPECTED - inspect (pre-state backup %s) ***" % BK)
    c.close()
    return 0 if ok else 4


raise SystemExit(main())
