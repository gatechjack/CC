"""Piece-5 FLIP-WATCH synthetic-series proof (read-only; temp DB).

Drives a regime sequence through the SAME transition logic the observer uses
(is_regime_flip + last-regime tracking + log_flip) and asserts flip rows emit ONLY at
real label->label transitions — never on warmup (None->label), teardown (label->None),
or no-change. Confirms the -> UP flip is present and index-queryable.
"""
import os, sqlite3, sys, tempfile

DEPLOY = r"C:\Users\AA Incorporado\cc-sfp-deploy-wt"
sys.path.insert(0, DEPLOY)
from trading_corp.agents.divisions import bitunix_sfp_research_log as rl

# None,None = warmup; down->range, range->up (UP!), up->down = 3 real flips;
# down->None (teardown) + None->up (re-warmup) must NOT emit.
SEQ = [None, None, "down", "down", "range", "up", "up", "down", None, "up"]
EXPECTED = [("down", "range"), ("range", "up"), ("up", "down")]


def main():
    path = os.path.join(tempfile.mkdtemp(), "flip.db")
    url = "sqlite:///" + path.replace("\\", "/")
    rl.ensure_flip_schema(url)

    emitted = []
    last = None                                    # mirrors observer _last_regime.get(wire)
    for i, new in enumerate(SEQ):
        old = last
        last = new                                 # ALWAYS update (incl None)
        if rl.is_regime_flip(old, new):
            rl.log_flip(url, ts=f"2026-07-01 00:00:{i:02d}", coin="BTCUSDT",
                        old_regime=old, new_regime=new, ema200=100.0 + i, slope=0.001)
            emitted.append((old, new))

    con = sqlite3.connect(path)
    rows = con.execute("SELECT old_regime, new_regime FROM bitunix_sfp_regime_flip "
                       "ORDER BY id").fetchall()
    nulls = con.execute("SELECT COUNT(*) FROM bitunix_sfp_regime_flip "
                        "WHERE old_regime IS NULL OR new_regime IS NULL").fetchone()[0]
    to_up = con.execute("SELECT COUNT(*) FROM bitunix_sfp_regime_flip "
                        "WHERE new_regime='up'").fetchone()[0]     # index-queryable
    con.close()

    checks = {
        "rows == 3 real flips": len(rows) == 3,
        "rows match expected label->label": [tuple(r) for r in rows] == EXPECTED,
        "emitted-in-loop match": emitted == EXPECTED,
        "NO warmup/teardown rows (0 NULLs)": nulls == 0,
        "-> UP flip present + queryable": to_up == 1,
    }
    allok = all(checks.values())
    for k, v in checks.items():
        print(f"  {'OK  ' if v else 'FAIL'} {k}")
    print(f"  sequence: {SEQ}")
    print(f"  emitted flips: {emitted}")
    print(f"FLIP-WATCH: {'ALL PASS' if allok else '*** FAIL ***'}")


if __name__ == "__main__":
    main()
