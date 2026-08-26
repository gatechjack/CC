echo "START pead_openrows_ro"; date -u +%FT%TZ
DB="$HOME/trading_corp/data/trading_corp.db"
python3 - "$DB" <<'PY'
import sqlite3,sys,json
db=sys.argv[1]
con=sqlite3.connect("file:%s?mode=ro"%db,uri=True); con.row_factory=sqlite3.Row; c=con.cursor()
rows=c.execute("SELECT order_id,symbol,extra_json FROM paper_trade_record "
               "WHERE (division='robinhood_pead' OR strategy='robinhood_pead') AND result IS NULL "
               "ORDER BY symbol").fetchall()
print("OPEN_COUNT=%d"%len(rows))
have=0; syms=[]
for r in rows:
    try: iid=json.loads(r['extra_json'] or '{}').get('instrument_id')
    except Exception: iid=None
    if iid: have+=1
    syms.append(r['symbol'])
    print("ROW|sym=%s|instrument_id=%s|oid=%s"%(r['symbol'],iid,r['order_id']))
print("ALREADY_HAVE_INSTRUMENT_ID=%d"%have)
print("SYMBOLS="+",".join(syms))
con.close()
PY
echo "DONE pead_openrows_ro"
