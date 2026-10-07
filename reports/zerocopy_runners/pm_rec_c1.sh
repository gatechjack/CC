ROOT=/home/azureuser/trading_corp
PY=$ROOT/venv/bin/python
echo "===== C1-A who reads close_source (py + html, context) ====="
grep -rnE "close_source" "$ROOT/trading_corp" --include=*.py --include=*.html 2>/dev/null | grep -v '__pycache__' | grep -vE '/tests?/' | grep -viE 'settlement.py' | head -80
echo "===== C1-B who reads won (word-boundary; py + html) ====="
grep -rnwE "won" "$ROOT/trading_corp" --include=*.py --include=*.html 2>/dev/null | grep -v '__pycache__' | grep -vE '/tests?/' | grep -viE 'shown|grown|known|unknown' | head -80
echo "===== C1-C settlement_void precedent (how an existing won=NULL close_source is handled) ====="
grep -rnE "settlement_void|settlement_scalar" "$ROOT/trading_corp" --include=*.py --include=*.html 2>/dev/null | grep -v '__pycache__' | head -40
echo "===== C1-D DB: close_source distribution + won NULL/0/1 (precedent counts) ====="
"$PY" - <<'PY'
import sqlite3
pm=sqlite3.connect('file:/home/azureuser/trading_corp/data/prediction_markets.db?mode=ro',uri=True)
print("close_source distribution (all time):")
for r in pm.execute("select coalesce(close_source,'NULL'),count(*) from pm_subdivision_order group by 1 order by 2 desc"):
    print("   %-20s %s"%(r[0],r[1]))
print("won distribution (all rows):")
for r in pm.execute("select case when won is null then 'NULL' else cast(won as text) end,count(*) from pm_subdivision_order group by 1"):
    print("   won=%-5s %s"%(r[0],r[1]))
print("won by is_exit:")
for r in pm.execute("select is_exit, case when won is null then 'NULL' else cast(won as text) end, count(*) from pm_subdivision_order group by 1,2 order by 1,2"):
    print("   is_exit=%s won=%-5s %s"%(r[0],r[1],r[2]))
print("existing settlement_void rows (won should be NULL): ")
for r in pm.execute("select close_source,count(*),sum(case when won is null then 1 else 0 end) null_won from pm_subdivision_order where close_source like 'settlement%' group by 1"):
    print("   %-20s n=%s null_won=%s"%(r[0],r[1],r[2]))
PY
echo "===== DONE c1 ====="
