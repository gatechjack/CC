$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$cmd = @'
import sqlite3
DB='/home/azureuser/trading_corp/data/trading_corp.db'
con=sqlite3.connect('file:%s?mode=ro'%DB, uri=True)
con.row_factory=sqlite3.Row
c=con.cursor()
EPO='2026-07-07T16:40:00+00:00'; L5='2026-08-05T00:00:00+00:00'
ACT=('kalshi_temporal_bucket_arb','kalshi_tail_price_arb','kalshi_llm_arbitrage')
def sec(t): print('\n=== %s ==='%t)
def run(t,fn):
    sec(t)
    try: fn()
    except Exception as e: print('SECTION_ERROR:',repr(e))

def s_sample():
    for a in ACT:
        r=c.execute("SELECT ts,payload_json FROM audit_event WHERE actor=? AND kind='would_have_placed' ORDER BY ts DESC LIMIT 1",(a,)).fetchone()
        print(a, r['ts'] if r else None, (r['payload_json'][:320] if r else 'NONE'))
run('WHP_SAMPLE latest payload per actor',s_sample)

def s_byday():
    for r in c.execute("SELECT actor,substr(ts,1,10) d,COUNT(*) n FROM audit_event WHERE kind='would_have_placed' AND actor IN ('kalshi_temporal_bucket_arb','kalshi_tail_price_arb','kalshi_llm_arbitrage') AND ts>='2026-08-01' GROUP BY actor,d ORDER BY d,actor"):
        print(dict(r))
run('WHP_BYDAY (placement signals) since 08-01',s_byday)

def s_distinct():
    for lbl,cut in [('since_0707',EPO),('since_0805',L5)]:
        for r in c.execute("SELECT actor,COUNT(*) whp,COUNT(DISTINCT json_extract(payload_json,'$.ticker')) dtk FROM audit_event WHERE kind='would_have_placed' AND actor IN ('kalshi_temporal_bucket_arb','kalshi_tail_price_arb','kalshi_llm_arbitrage') AND ts>=? GROUP BY actor",(cut,)):
            print(lbl,dict(r))
run('WHP_DISTINCT_TICKERS emitted',s_distinct)

def s_pending():
    for a,dv,cut in [('kalshi_llm_arbitrage','kalshi_llm_arbitrage',EPO),('kalshi_temporal_bucket_arb','kalshi_arbitrage',EPO),('kalshi_tail_price_arb','kalshi_arbitrage',EPO)]:
        em=set(x[0] for x in c.execute("SELECT DISTINCT json_extract(payload_json,'$.ticker') FROM audit_event WHERE actor=? AND kind='would_have_placed' AND ts>=?",(a,cut)))
        em.discard(None)
        bk=set(x[0] for x in c.execute("SELECT DISTINCT ticker FROM kalshi_round_trips WHERE strategy=? AND entry_ts>=?",(a,cut)))
        print(a,'emitted_distinct=',len(em),'booked_distinct=',len(bk),'pending_distinct(emitted-booked)=',len(em-bk))
run('PENDING_PROXY distinct forward tickers emitted vs booked',s_pending)

print('\n=== DONE2 ===')
'@
$cmd | ssh $h "tr -d '\r\357\273\277' | python3 -"
