$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$cmd = @'
import sqlite3, math
DB='/home/azureuser/trading_corp/data/trading_corp.db'
con=sqlite3.connect('file:%s?mode=ro'%DB, uri=True)
con.row_factory=sqlite3.Row
c=con.cursor()
EPO='2026-07-07T16:40:00+00:00'
FIX='2026-07-07T00:00:00'
DIVS=('kalshi_arbitrage','kalshi_llm_arbitrage')
S={}
def fee(qty,p):
    try: v=0.07*float(qty)*float(p)*(1.0-float(p))
    except: return 0.0
    return math.ceil(v*100.0)/100.0 if v>0 else 0.0
def sec(t): print('\n=== %s ==='%t)
def agg(rows):
    n=len(rows); w=sum(int(x['won']) for x in rows)
    v=sum(1 for x in rows if (x['market_result'] or '')=='void')
    g=sum(float(x['realized_pnl']) for x in rows)
    f=sum(fee(x['qty'],x['entry_price']) for x in rows)
    return {'n':n,'w':w,'v':v,'gross':round(g,4),'fees':round(f,4),'net':round(g-f,4)}
def run(title,fn):
    sec(title)
    try: fn()
    except Exception as e: print('SECTION_ERROR:',repr(e))

def s_schema():
    print('audit_event cols:',[r[1] for r in c.execute("PRAGMA table_info(audit_event)")])
    print('kalshi_round_trips cols:',[r[1] for r in c.execute("PRAGMA table_info(kalshi_round_trips)")])
run('SCHEMA',s_schema)

def s_resolved():
    for r in c.execute("SELECT division,substr(resolved_ts,1,10) d,COUNT(*) n FROM kalshi_round_trips WHERE resolved_ts>='2026-07-01' AND division IN ('kalshi_arbitrage','kalshi_llm_arbitrage') GROUP BY division,d ORDER BY division,d"):
        print(dict(r))
run('RESOLVED_BYDAY since 07-01',s_resolved)

def s_load():
    for d in DIVS:
        S[d]=[dict(r) for r in c.execute("SELECT won,market_result,realized_pnl,qty,entry_price,entry_ts,resolved_ts,ticker,event_ticker,category,outcome_bet,divergence_pct,llm_prob FROM kalshi_round_trips WHERE division=?",(d,))]
        print('loaded',d,len(S[d]),'rows')
run('LOAD',s_load)

def s_step1():
    print('boundary FIX=%s ; llm dashboard EPO=%s'%(FIX,EPO))
    for d in DIVS:
        rows=S[d]
        print(d,'a_backlog(entry<FIX)', agg([x for x in rows if x['entry_ts']<FIX]))
        print(d,'b_forward(entry>=FIX)', agg([x for x in rows if x['entry_ts']>=FIX]))
    llm=S['kalshi_llm_arbitrage']
    print('LLM rows in [FIX,EPO) window:',len([x for x in llm if FIX<=x['entry_ts']<EPO]))
    print('LLM b_forward(entry>=EPO dashboard)', agg([x for x in llm if x['entry_ts']>=EPO]))
run('STEP1 BACKLOG_VS_FORWARD (gross,fees,net)',s_step1)

def s_optA():
    llm=S['kalshi_llm_arbitrage']
    emis=[x for x in llm if x['entry_ts']>=EPO]
    print('OptA per-emission >=EPO', agg(emis))
run('STEP2 LLM OptionA',s_optA)

def s_optB():
    by={}
    for r in c.execute("SELECT id,ticker,won,market_result,realized_pnl,qty,entry_price,entry_ts,event_ticker,category,divergence_pct,llm_prob,resolved_ts FROM kalshi_round_trips WHERE division='kalshi_llm_arbitrage' AND entry_ts>=?",(EPO,)):
        r=dict(r); k=r['ticker']
        if k not in by or (r['entry_ts'],r['id'])<(by[k]['entry_ts'],by[k]['id']): by[k]=r
    canon=list(by.values()); S['canon']=canon
    print('RAW OptB(python dedup)', agg(canon))
    row=c.execute("WITH ranked AS (SELECT ticker,won,market_result,realized_pnl,ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY entry_ts ASC,id ASC) rn FROM kalshi_round_trips WHERE division='kalshi_llm_arbitrage' AND entry_ts>=?) SELECT COUNT(*) n,COALESCE(SUM(won),0) w,COALESCE(SUM(CASE WHEN market_result='void' THEN 1 ELSE 0 END),0) v,ROUND(COALESCE(SUM(realized_pnl),0),4) gross FROM ranked WHERE rn=1",(EPO,)).fetchone()
    print('DASHBOARD CTE(gross only,no fee)', {'n':row['n'],'w':row['w'],'v':row['v'],'gross':row['gross']})
    print('CROSSCHECK n=%s w=%s gross=%s'%(len(canon)==row['n'], sum(int(x['won']) for x in canon)==row['w'], abs(sum(float(x['realized_pnl']) for x in canon)-float(row['gross']))<1e-6))
run('STEP2 LLM OptionB RAW-vs-DASHBOARD',s_optB)

def s_optB_rows():
    for x in sorted(S.get('canon',[]),key=lambda z:(z['category'] or '',z['ticker'])):
        print(x['category'],'|',x['ticker'],'|',x['event_ticker'],'|ep=',x['entry_price'],'|q=',round(float(x['qty']),3),'|res=',x['market_result'],'|won=',x['won'],'|pnl=',round(float(x['realized_pnl']),4),'|fee=',fee(x['qty'],x['entry_price']),'|div=',x['divergence_pct'],'|llm_p=',x['llm_prob'])
run('STEP2 LLM OptionB canonical rows',s_optB_rows)

def s_cat():
    cats={}
    for x in S.get('canon',[]): cats.setdefault(x['category'] or 'NULL',[]).append(x)
    for k,rows in sorted(cats.items(),key=lambda z:-len(z[1])): print('cat=',k, agg(rows))
run('STEP2 LLM by CATEGORY',s_cat)

def s_div():
    dv=[x for x in S.get('canon',[]) if x['divergence_pct'] is not None]
    dv.sort(key=lambda z:abs(float(z['divergence_pct'])))
    if not dv: print('no divergence data'); return
    half=len(dv)//2
    lo=dv[:half]; hi=dv[half:]
    print('low-div half', agg(lo),'avg|div|=',round(sum(abs(float(x['divergence_pct'])) for x in lo)/max(1,len(lo)),3))
    print('high-div half', agg(hi),'avg|div|=',round(sum(abs(float(x['divergence_pct'])) for x in hi)/max(1,len(hi)),3))
run('STEP2 LLM divergence-inversion',s_div)

def s_arb():
    arb=S['kalshi_arbitrage']; afw=[x for x in arb if x['entry_ts']>=FIX]; S['afw']=afw
    print('ARB b_forward agg', agg(afw))
    for x in sorted(afw,key=lambda z:z['entry_ts']):
        print(x['entry_ts'][:16],'|res=',x['resolved_ts'][:10],'|',x['event_ticker'],'|',x['ticker'],'|',x['outcome_bet'],'|ep=',x['entry_price'],'|q=',round(float(x['qty']),3),'|r=',x['market_result'],'|won=',x['won'],'|pnl=',round(float(x['realized_pnl']),4),'|fee=',fee(x['qty'],x['entry_price']))
    arow=c.execute("WITH ranked AS (SELECT ticker,won,realized_pnl,ROW_NUMBER() OVER (PARTITION BY ticker ORDER BY entry_ts ASC,id ASC) rn FROM kalshi_round_trips WHERE division='kalshi_arbitrage' AND entry_ts>=?) SELECT COUNT(*) n,COALESCE(SUM(won),0) w,ROUND(COALESCE(SUM(realized_pnl),0),4) gross FROM ranked WHERE rn=1",(FIX,)).fetchone()
    print('ARB OptB distinct', {'n':arow['n'],'w':arow['w'],'gross':arow['gross']})
run('STEP2 ARB forward rows',s_arb)

def s_entry():
    for r in c.execute("SELECT division,substr(entry_ts,1,10) d,COUNT(*) n FROM kalshi_round_trips WHERE entry_ts>=? AND division IN ('kalshi_arbitrage','kalshi_llm_arbitrage') GROUP BY division,d ORDER BY division,d",(FIX,)):
        print(dict(r))
run('STEP2 ENTRY_RATE_BYDAY forward(entry_ts>=FIX)',s_entry)

def conc(rows,label):
    ev={}
    for x in rows:
        k=x['event_ticker'] or x['ticker']; ev.setdefault(k,[0,0.0])
        ev[k][0]+=1; ev[k][1]+=float(x['realized_pnl'])-fee(x['qty'],x['entry_price'])
    tot=sum(v[1] for v in ev.values()); absum=sum(abs(v[1]) for v in ev.values())
    print(label,'total_net=',round(tot,4),'sum|net|=',round(absum,4),'n_events=',len(ev))
    for k,v in sorted(ev.items(),key=lambda z:-abs(z[1][1]))[:5]:
        print('  ',k,'n=',v[0],'net=',round(v[1],4),'pct_of_|net|=',round((abs(v[1])/absum*100) if absum else 0,1))
def s_conc():
    conc(S.get('canon',[]),'LLM_OptB'); conc(S.get('afw',[]),'ARB_forward')
run('STEP3 CONCENTRATION',s_conc)

def s_ae_recency():
    for r in c.execute("SELECT actor,COUNT(*) c,MIN(ts) first_ts,MAX(ts) last_ts FROM audit_event WHERE actor IN ('kalshi_temporal_bucket_arb','kalshi_tail_price_arb','kalshi_llm_arbitrage') GROUP BY actor ORDER BY last_ts DESC"):
        print(dict(r))
run('AE_RECENCY (scanners)',s_ae_recency)

def s_ae_type():
    aecols=[r[1] for r in c.execute("PRAGMA table_info(audit_event)")]
    tc=None
    for cand in ('event_type','event','kind','type','name','action','label','tag','event_name','subtype'):
        if cand in aecols: tc=cand; break
    print('TYPECOL=',tc)
    if not tc: print('no type-like column found; cols=',aecols); return
    for r in c.execute("SELECT actor,%s tv,COUNT(*) c,MAX(ts) last_ts FROM audit_event WHERE actor IN ('kalshi_temporal_bucket_arb','kalshi_tail_price_arb','kalshi_llm_arbitrage') GROUP BY actor,%s ORDER BY actor,c DESC"%(tc,tc)):
        print(dict(r))
    S['tc']=tc
run('AE_TYPES per actor',s_ae_type)

def s_ae_byday():
    for r in c.execute("SELECT actor,substr(ts,1,10) d,COUNT(*) n FROM audit_event WHERE actor IN ('kalshi_temporal_bucket_arb','kalshi_tail_price_arb','kalshi_llm_arbitrage') AND ts>='2026-08-01' GROUP BY actor,d ORDER BY d,actor"):
        print(dict(r))
run('AE_ATTEMPTS_BYDAY since 08-01',s_ae_byday)

print('\n=== DONE_DATA ===')
'@
$cmd | ssh $h "tr -d '\r\357\273\277' | python3 -"
