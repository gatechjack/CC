"""READ-ONLY: SDTrading mlb sliced by market type (moneyline / total / spread) -- Task 1, investigation pt2.
Classifies by the EXACT slug suffix after the game-prefix (auditable; matches the 254/167/56 already
reported). Reads data/prediction_markets.db mode=ro. No writes. Per-slice: n, wins, losses, win_rate,
net_realized_pnl, cost_basis, cost-ROI, avg_win_price, distinct games, per-game win% (net>0)."""
import sqlite3, re, collections

DB = '/home/azureuser/trading_corp/data/prediction_markets.db'
c = sqlite3.connect('file:%s?mode=ro' % DB, uri=True); c.row_factory = sqlite3.Row
w = c.execute("SELECT wallet FROM pm_whale WHERE user_name='SDTrading'").fetchone()
wallet = w['wallet']
print('SDTrading wallet =', wallet)

rows = [dict(r) for r in c.execute(
    "SELECT condition_id,slug,event_slug,outcome,outcome_index,avg_price,cur_price,won,realized_pnl,"
    "total_bought,cost_basis FROM pm_closed_position WHERE wallet=? AND category='mlb' AND pnl_suspect=0",
    (wallet,)).fetchall()]
print('scoreable mlb rows =', len(rows))

GP = re.compile(r'^(mlb-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2})(.*)$')
def parts(r):
    m = GP.match(r['slug'] or '')
    return (m.group(1), m.group(2)) if m else ((r['slug'] or ''), '')
def slice_of(r):
    suf = parts(r)[1]
    if suf.startswith('-total'):  return 'total'
    if suf.startswith('-spread'): return 'spread'
    if suf == '':                 return 'moneyline'
    return 'other:' + suf
def gk(r):
    return parts(r)[0]

sl = collections.defaultdict(list)
for r in rows:
    sl[slice_of(r)].append(r)

def agg(rs):
    n = len(rs); won = sum(1 for r in rs if r['won'] == 1); lost = sum(1 for r in rs if r['won'] == 0)
    net = sum(r['realized_pnl'] or 0 for r in rs); cost = sum(r['cost_basis'] or 0 for r in rs)
    wp = [r['avg_price'] for r in rs if r['won'] == 1 and r['avg_price'] is not None]
    gp = collections.defaultdict(float)
    for r in rs:
        gp[gk(r)] += r['realized_pnl'] or 0
    games = len(gp); gwin = sum(1 for g in gp if gp[g] > 0)
    return dict(n=n, won=won, lost=lost, wr=(won/(won+lost) if won+lost else 0), net=net, cost=cost,
               roi=(net/cost if cost else 0), awp=(sum(wp)/len(wp) if wp else 0),
               games=games, pergame=(gwin/games if games else 0), gwin=gwin)

hdr = '%-11s %4s %4s %4s %6s %13s %13s %8s %8s %5s %5s %7s'
row = '%-11s %4d %4d %4d %5.1f%% %13.1f %13.1f %+7.1f%% %8.4f %5d %5d %6.1f%%'
print('\n' + hdr % ('slice', 'n', 'won', 'lost', 'win%', 'net_pnl', 'cost_basis', 'costROI', 'avgWinPx', 'game', 'gWin', 'pergame'))
print('-' * 108)
tn = tnet = tcost = 0
for name in ['moneyline', 'total', 'spread']:
    rs = sl.get(name, [])
    if not rs:
        print('%-11s (none)' % name); continue
    a = agg(rs); tn += a['n']; tnet += a['net']; tcost += a['cost']
    print(row % (name, a['n'], a['won'], a['lost'], a['wr']*100, a['net'], a['cost'], a['roi']*100,
                 a['awp'], a['games'], a['gwin'], a['pergame']*100))
for name in [k for k in sl if k.startswith('other')]:
    a = agg(sl[name]); tn += a['n']; tnet += a['net']; tcost += a['cost']
    print(row % (name[:11], a['n'], a['won'], a['lost'], a['wr']*100, a['net'], a['cost'], a['roi']*100,
                 a['awp'], a['games'], a['gwin'], a['pergame']*100))
allr = agg(rows)
print('-' * 108)
print(row % ('ALL', allr['n'], allr['won'], allr['lost'], allr['wr']*100, allr['net'], allr['cost'],
             allr['roi']*100, allr['awp'], allr['games'], allr['gwin'], allr['pergame']*100))
print('\nsanity: slice n sum=%d (expect %d) ; slice net sum=%.1f (expect %.1f) ; slice cost sum=%.1f (expect %.1f)'
      % (tn, allr['n'], tnet, allr['net'], tcost, allr['cost']))
print('note: per-slice "game" = distinct games IN THAT SLICE (a game with ML+total is counted in both slices, '
      'so slice games do NOT sum to the 380 overall). pergame% = games with net>0 within the slice.')
c.close()
print('\n== END three-way slice ==')
