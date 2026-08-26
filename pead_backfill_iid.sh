echo "START pead_backfill_iid (PURE DATA-ADD: extra_json.instrument_id only)"; date -u +%FT%TZ
DB="$HOME/trading_corp/data/trading_corp.db"
python3 - "$DB" <<'PY'
import sqlite3,sys,json
db=sys.argv[1]
MAP={
 "ADP":"a6f14684-8867-4a17-b352-c99b1d5b36dd","ALM":"95c7499e-25a5-4e08-8aae-43c2f6e8afab",
 "APA":"c334e530-a933-4120-8bc4-e986698e88e7","ATRC":"744711ac-01c2-466f-8c93-190a4af6572e",
 "AXTI":"0c0bf051-7468-4a31-a727-a1565892c011","BCPC":"7f068db8-cedb-482f-983a-df908703ab4c",
 "CAKE":"a28685be-7b83-4f51-8078-82f38f8b7f01","CHEF":"fc123b17-885d-441e-a418-74a84ae2cb2b",
 "CHRD":"57880803-2f49-4a24-8b64-ea3cb5bb751e","DXCM":"94ba5ec4-5f31-4640-b0a4-a8e93fbd2a31",
 "ENTG":"b59a191c-8db4-4482-b0ba-77532fa395fc","ETON":"06ed71e7-82e1-448e-ab53-24d66882b7b6",
 "FNKO":"2d1c7288-9c18-400c-afe0-e910d0e4589c","FORM":"9f8a2453-4dd9-4cc7-b4bf-5570acfb5396",
 "FOX":"3d37f834-a7f0-45d8-a503-0d67b19e3fd8","FOXA":"2f136530-6c8b-4ba5-b07f-a15df0d46518",
 "HURN":"5f786f4d-a3c9-4fda-b755-232accf0a58b","IOVA":"6fefbdd1-c296-401d-85f5-8754389d0b92",
 "JKHY":"e0e0ec5b-487d-4c3a-bf11-53e5980f69fe","LCUT":"a965454c-6a03-40d1-a0ab-6c35a75a0bee",
 "LRCX":"5c7b07af-182c-485e-be08-a0e903adbeeb","MMSI":"46d2c7c3-562b-40fa-8648-7076ee86ed10",
 "NSIT":"2cc0866e-4a87-4d6a-b239-8df576dcf2b5","PAYS":"dea0e19b-080d-4f25-bb62-d1bba30931af",
 "PLTR":"f90de184-4f73-4aad-9a5f-407858013eb1","PRLD":"ead8ebd4-3804-40f1-95ac-7c1b98818f9d",
 "ROKU":"279d787b-515b-4f9c-a684-45f92afb557f","SMCI":"50846aee-ce5f-4bd4-bfbb-cef4414f69bd",
 "SOPH":"4865ceff-97aa-4344-8923-936082fa79c8","TILE":"3906a42f-2fd8-4d80-b35b-84daf41a1851",
 "TSEM":"cbd02cf6-25e2-470a-bbb0-e9fd76d6707f","URGN":"338049e2-33c9-400f-a045-e9dca5035543",
 "VOR":"ab1d848e-98ed-480f-91af-7242d5b34501","WLDN":"96b5af12-7c93-4cf6-acb9-cd579d6a2508"}
con=sqlite3.connect(db, timeout=10); con.row_factory=sqlite3.Row; c=con.cursor(); c.execute("PRAGMA busy_timeout=8000")
WHERE="(division='robinhood_pead' OR strategy='robinhood_pead') AND result IS NULL"
def snap(sym):
    r=c.execute("SELECT symbol,qty,entry_reference_price,stop_price,result,execution_mode,extra_json "
                "FROM paper_trade_record WHERE symbol=? AND "+WHERE,(sym,)).fetchone()
    return dict(r) if r else None
pre=snap("ADP"); print("BEFORE_ADP:", json.dumps(pre))
rows=c.execute("SELECT order_id,symbol FROM paper_trade_record WHERE "+WHERE+" ORDER BY symbol").fetchall()
aff=0; skipped=[]; wrote=[]
for r in rows:
    sym=r["symbol"]; iid=MAP.get(sym)
    if not iid: skipped.append(sym); continue
    c.execute("UPDATE paper_trade_record SET extra_json=json_set(extra_json,'$.instrument_id',?) "
              "WHERE order_id=? AND symbol=? AND "+WHERE+" AND json_extract(extra_json,'$.instrument_id') IS NULL",
              (iid, r["order_id"], sym))
    if c.rowcount==1: aff+=1; wrote.append((sym,iid))
con.commit()
post=snap("ADP"); print("AFTER_ADP:", json.dumps(post))
# prove pure data-add on ADP: only extra_json.instrument_id changed
pj=json.loads(pre["extra_json"]); qj=json.loads(post["extra_json"])
col_changed=[k for k in pre if k!="extra_json" and pre[k]!=post[k]]
json_changed={k:[pj.get(k),qj.get(k)] for k in set(pj)|set(qj) if pj.get(k)!=qj.get(k)}
print("ADP_COLUMNS_CHANGED=", col_changed)
print("ADP_EXTRA_JSON_KEYS_CHANGED=", json.dumps(json_changed))
have=c.execute("SELECT COUNT(*) FROM paper_trade_record WHERE "+WHERE+" AND json_extract(extra_json,'$.instrument_id') IS NOT NULL").fetchone()[0]
total=c.execute("SELECT COUNT(*) FROM paper_trade_record WHERE "+WHERE).fetchone()[0]
print("ROWS_WRITTEN=%d  OPEN_TOTAL=%d  NOW_HAVE_INSTRUMENT_ID=%d  SKIPPED=%s"%(aff,total,have,skipped or "NONE"))
print("RECONCILE (symbol -> instrument_id written):")
for s,i in wrote: print("  %-6s %s"%(s,i))
con.close()
PY
echo "DONE pead_backfill_iid"
