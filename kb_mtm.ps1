$OutputEncoding = New-Object System.Text.UTF8Encoding $false
$h = 'azureuser@trading.jacksumner.com'
$cmd = @'
import sqlite3, json, math, asyncio, os, stat, tempfile, sys
sys.path.insert(0,'/home/azureuser/trading_corp')
DB='/home/azureuser/trading_corp/data/trading_corp.db'
FIX='2026-07-07T00:00:00'
def _f(x):
    try: return float(x)
    except: return None
def fee(q,p):
    try: v=0.07*float(q)*float(p)*(1-float(p))
    except: return 0.0
    return math.ceil(v*100)/100 if v>0 else 0.0
con=sqlite3.connect('file:%s?mode=ro'%DB,uri=True); con.row_factory=sqlite3.Row; c=con.cursor()
booked=set(x[0] for x in c.execute("SELECT DISTINCT ticker FROM kalshi_round_trips WHERE division='kalshi_arbitrage'"))
whp=[]
for a in ('kalshi_temporal_bucket_arb','kalshi_tail_price_arb'):
    for r in c.execute("SELECT ts,payload_json FROM audit_event WHERE actor=? AND kind='would_have_placed' AND ts>=? ORDER BY ts",(a,FIX)):
        try: p=json.loads(r['payload_json'])
        except: p={}
        whp.append((a,r['ts'],p))
def oside(p):
    leg=(p.get('leg') or '').lower()
    if leg.startswith('no') or 'no_' in leg: return 'no'
    if 'yes' in leg: return 'yes'
    return 'yes'
pos={}
for a,ts,p in whp:
    tk=p.get('ticker')
    if not tk: continue
    sd=oside(p); k=(tk,sd); d=pos.get(k)
    if d is None:
        pos[k]={'actor':a,'ticker':tk,'side':sd,'first_ts':ts,'last_ts':ts,'p':p,'n':1,'lps':[_f(p.get('limit_price'))] if _f(p.get('limit_price')) is not None else []}
    else:
        d['n']+=1; d['last_ts']=ts
        if ts<d['first_ts']: d['first_ts']=ts; d['p']=p
        lp=_f(p.get('limit_price'))
        if lp is not None: d['lps'].append(lp)
print('=== STEP1 PENDING ENUM (kalshi_arbitrage forward, entry>=07-07) ===')
print('booked_distinct_tickers(resolved)=',len(booked))
pend=[]
for k,d in sorted(pos.items(), key=lambda z:z[1]['first_ts']):
    tk,sd=k; is_booked = tk in booked
    if not is_booked: pend.append(d)
    p=d['p']
    print(('BOOKED' if is_booked else 'PENDING'),'|',tk,'|side=',sd,'|entry_lp=',p.get('limit_price'),'|qty=',p.get('qty'),'|lp_range=',((min(d['lps']),max(d['lps'])) if d['lps'] else None),'|n_emis=',d['n'],'|first=',d['first_ts'][:16],'|last=',d['last_ts'][:16],'|leg=',p.get('leg'),'|type=',p.get('kalshi_arb_type'),'|set=',p.get('kalshi_arb_set_id'),'|edge$=',p.get('edge_dollars'),'|leg_date=',p.get('leg_date'),'|rat=',(p.get('rationale') or '')[:55])
print('PENDING_COUNT=',len(pend))
print('tickers_emitted_since_0812=',sorted(set(p.get('ticker') for a,ts,p in whp if ts>='2026-08-12' and p.get('ticker'))))

async def live():
    from trading_corp.utils.secrets import load_secrets
    s=load_secrets()
    kid=getattr(s,'kalshi_karen_api_key_id',None); pem=getattr(s,'kalshi_karen_private_key_pem',None)
    print('\n=== STEP2 LIVE KALSHI (Karen read path, $0 GET) ===')
    print('karen_creds_present=', bool(kid and pem))
    if not (kid and pem):
        print('NO_KAREN_CREDS - skipping live reads'); return
    from pykalshi import AsyncKalshiClient
    marks={}; fd,pp=tempfile.mkstemp(prefix='kq_',suffix='.pem')
    try:
        with os.fdopen(fd,'w') as f: f.write(pem)
        os.chmod(pp, stat.S_IRUSR|stat.S_IWUSR)
        client=AsyncKalshiClient(api_key_id=kid, private_key_path=pp, demo=False)
        for tk in sorted(set(d['ticker'] for d in pend)):
            o={'ticker':tk}
            try:
                m=await client.get_market(tk)
                o['status']=str(getattr(m,'status',None)); o['result']=getattr(m,'result',None); o['close_time']=str(getattr(m,'close_time',None))
                o['yes_bid']=_f(getattr(m,'yes_bid_dollars',None)); o['yes_ask']=_f(getattr(m,'yes_ask_dollars',None))
                o['no_bid']=_f(getattr(m,'no_bid_dollars',None)); o['no_ask']=_f(getattr(m,'no_ask_dollars',None))
                o['last']=_f(getattr(m,'last_price_dollars',None)); o['vol']=str(getattr(m,'volume_fp',None)); o['oi']=str(getattr(m,'open_interest_fp',None))
                try:
                    ob=await m.get_orderbook(depth=5)
                    o['best_yes_bid']=_f(ob.best_yes_bid); o['best_yes_ask']=_f(ob.best_yes_ask); o['spread']=_f(ob.spread); o['mid']=_f(ob.mid)
                except Exception as e: o['ob_err']=repr(e)[:80]
            except Exception as e: o['err']=repr(e)[:120]
            marks[tk]=o
            print('MKT|',tk,'|status=',o.get('status'),'|result=',repr(o.get('result')),'|yb=',o.get('yes_bid'),'|ya=',o.get('yes_ask'),'|nb=',o.get('no_bid'),'|na=',o.get('no_ask'),'|last=',o.get('last'),'|spread=',o.get('spread'),'|mid=',o.get('mid'),'|vol=',o.get('vol'),'|oi=',o.get('oi'),(('|ERR='+o['err']) if 'err' in o else ''))
        await client.aclose()
    finally:
        try: os.unlink(pp)
        except: pass
    print('\n=== STEP3 MARK-TO-MARKET (per position, side math) ===')
    up=dn=flat=0; tot=0.0; usable=0
    for d in sorted(pend,key=lambda z:z['first_ts']):
        tk=d['ticker']; sd=d['side']; entry=_f(d['p'].get('limit_price')); qty=_f(d['p'].get('qty')); o=marks.get(tk,{})
        res=o.get('result'); st=(o.get('status') or '')
        resolved = (res in ('yes','no','void')) or ('resolv' in st.lower()) or ('settl' in st.lower()) or ('final' in st.lower())
        ym=None
        if o.get('mid') is not None: ym=o['mid']
        elif o.get('yes_bid') is not None and o.get('yes_ask') is not None: ym=(o['yes_bid']+o['yes_ask'])/2
        elif o.get('last') is not None: ym=o['last']
        if sd=='yes': cur=ym
        else:
            if o.get('no_bid') is not None and o.get('no_ask') is not None: cur=(o['no_bid']+o['no_ask'])/2
            elif ym is not None: cur=1-ym
            else: cur=None
        yb=o.get('yes_bid'); ya=o.get('yes_ask'); sp=o.get('spread'); two=(yb is not None and ya is not None)
        if resolved: rel='RESOLVED'
        elif o.get('err'): rel='READ-ERR'
        elif (not two) and o.get('mid') is None: rel=('STALE/last-only' if o.get('last') is not None else 'NO-QUOTE')
        elif sp is not None and sp>0.10: rel='WIDE-SPREAD'
        elif not two: rel='ONE-SIDED'
        else: rel='LIVE'
        if cur is not None and entry is not None and qty is not None:
            unreal=qty*(cur-entry); usable+=1; tot+=unreal; thr=qty*0.03
            dirn=('WINNING' if unreal>thr else ('LOSING' if unreal<-thr else 'FLAT'))
            if unreal>thr: up+=1
            elif unreal<-thr: dn+=1
            else: flat+=1
        else: unreal=None; dirn='NA'
        nc=''
        if cur is not None:
            if cur>=0.97: nc='NEAR-CERTAIN-WIN'
            elif cur<=0.03: nc='NEAR-CERTAIN-LOSS'
        print((('*RESOLVED-UNBOOKED* ' if resolved else '')+tk),'|side=',sd,'|entry=',entry,'|cur=',(None if cur is None else round(cur,3)),'|unreal$=',(None if unreal is None else round(unreal,3)),'|dir=',dirn,'|rel=',rel,nc,'|entry_fee=',fee(qty,entry),'|close=',o.get('close_time'),'|res=',repr(res))
    print('\nAGG pending=',len(pend),'usable_marks=',usable,'up=',up,'down=',dn,'flat=',flat,'total_unreal$=',round(tot,4))

try: asyncio.run(live())
except Exception as e: print('LIVE_SECTION_ERROR:',repr(e))
print('\n=== DONE_MTM ===')
'@
$cmd | ssh $h "tr -d '\r\357\273\277' | (cd /home/azureuser/trading_corp && ./venv/bin/python -)"
