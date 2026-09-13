set -u
ROOT=/home/azureuser/trading_corp
PY="$ROOT/venv/bin/python3"
LEGACY="$ROOT/data/trading_corp.db"
echo "### SOURCE kcv2_quotes SPOT ROWS (READ-ONLY, for archive verification) $(date -u +%FT%TZ) ###"
"$PY" - "$LEGACY" <<'PYEOF'
import sqlite3,sys
c=sqlite3.connect("file:%s?mode=ro"%sys.argv[1],uri=True)
cols=[r[1] for r in c.execute("PRAGMA table_info(kcv2_quotes)")]
print("cols:", cols)
for rid in (1, 6000000, 12032778, 12034120):
    row=c.execute("SELECT rowid,* FROM kcv2_quotes WHERE rowid=?", (rid,)).fetchone()
    print("SRC rowid=%s -> %s" % (rid, row))
c.close()
PYEOF
echo "### DONE ###"
