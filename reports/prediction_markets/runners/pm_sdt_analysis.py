"""READ-ONLY characterization of SDTrading's mlb drill-through (2026-08-24 investigation).
Reads data/prediction_markets.db mode=ro; imports the DEPLOYED classifier so we report what the
code actually does. No writes. Answers Jack's three observations + the defect checks."""
import sqlite3, re, collections
from trading_corp.prediction_markets.category import classify_market_shape

DB = '/home/azureuser/trading_corp/data/prediction_markets.db'
c = sqlite3.connect('file:%s?mode=ro' % DB, uri=True); c.row_factory = sqlite3.Row

w = c.execute("SELECT wallet FROM pm_whale WHERE user_name='SDTrading'").fetchone()
if not w:
    print('SDTrading NOT FOUND by user_name'); raise SystemExit
wallet = w['wallet']
print('SDTrading wallet =', wallet)

cs = c.execute("SELECT n_resolved,wins,losses,win_rate,roi,roi_notional,avg_win_price,n_anomaly,"
               "n_excluded,n_condition_ids,n_two_sided,two_sided_pct,n_single_game,single_game_pct "
               "FROM pm_category_stats WHERE wallet=? AND category='mlb'", (wallet,)).fetchone()
print('\n== SCOREBOARD AGGREGATE (pm_category_stats mlb) ==')
for k in cs.keys():
    print('  %-16s = %s' % (k, cs[k]))

rows = [dict(r) for r in c.execute(
    "SELECT condition_id,slug,event_slug,title,outcome,outcome_index,avg_price,cur_price,won,"
    "realized_pnl,total_bought,cost_basis,pnl_suspect,pnl_anomaly,anomaly_reason,resolved_ts "
    "FROM pm_closed_position WHERE wallet=? AND category='mlb' ORDER BY resolved_ts DESC", (wallet,)).fetchall()]
sc = [r for r in rows if r['pnl_suspect'] == 0]
print('\n== ROW COUNTS ==  all=%d  scoreable=%d  quarantined=%d' % (
    len(rows), len(sc), sum(1 for r in rows if r['pnl_suspect'] == 1)))

print('\n== 15 NEWEST rows (what Jack saw): slug | outcome | avgpx curpx | won | pnl ==')
for r in rows[:15]:
    print('  %-44s | %-26s | %.2f %.2f | %s | %+8.1f' % (
        (r['slug'] or '')[:44], (r['outcome'] or '')[:26], r['avg_price'] or 0, r['cur_price'] or 0,
        r['won'], r['realized_pnl'] or 0))

# ---- (1) MARKET TYPE ----
def mtype(r):
    txt = ((r['slug'] or '') + ' ' + (r['title'] or '')).lower()
    if 'spread' in txt or 'run-line' in txt or 'runline' in txt or '-1.5' in txt or '+1.5' in txt:
        return 'spread'
    if 'total' in txt or 'o/u' in txt or 'over-under' in txt or 'over/under' in txt or ' over ' in txt or ' under ' in txt:
        return 'total'
    return 'moneyline?'
mt = collections.Counter(mtype(r) for r in rows)
mt_sc = collections.Counter(mtype(r) for r in sc)
print('\n== (1) MARKET TYPE by slug/title keyword ==')
print('  ALL rows      :', dict(mt))
print('  scoreable only:', dict(mt_sc))
print('  keyword hits  : spread-in-txt=%d  total/ou-in-txt=%d' % (
    sum(1 for r in rows if 'spread' in ((r['slug'] or '')+(r['title'] or '')).lower()),
    sum(1 for r in rows if any(k in ((r['slug'] or '')+(r['title'] or '')).lower() for k in ('total','o/u','over','under')))))

# slug suffixes after the game prefix -> reveals exact market-type encoding
suf = collections.Counter()
GP = re.compile(r'^(mlb-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2})(.*)$')
for r in rows:
    m = GP.match(r['slug'] or '')
    suf[m.group(2) if m else ('NO-GAME-PREFIX:' + (r['slug'] or '')[:24])] += 1
print('  -- slug suffix after game-prefix (top 25; empty = plain game/moneyline) --')
for s, ct in suf.most_common(25):
    print('     %5d  %r' % (ct, s))

# what the DEPLOYED classifier says (why single_game% = 100%)
shape = collections.Counter(classify_market_shape(r['slug'], r['event_slug'], r['title']) for r in rows)
print('  -- classify_market_shape (the deployed code) over ALL rows --', dict(shape))
print('     sample moneyline? slugs:', [ (r['slug'] or '')[:40] for r in rows if mtype(r) == 'moneyline?' ][:6])

# ---- (2) GAMES / LEGS / PER-GAME WIN RATE ----
def gamekey(r):
    m = GP.match(r['slug'] or '')
    return m.group(1) if m else (r['event_slug'] or (r['slug'] or ''))
legs = collections.Counter(gamekey(r) for r in rows)
legs_sc = collections.Counter(gamekey(r) for r in sc)
print('\n== (2) GAMES ==  distinct games (all rows)=%d  (scoreable)=%d' % (len(legs), len(legs_sc)))
print('  legs-per-game distribution (scoreable):', dict(sorted(collections.Counter(legs_sc.values()).items())))
gp = collections.defaultdict(float)
for r in sc:
    gp[gamekey(r)] += r['realized_pnl'] or 0
gw = sum(1 for g in gp if gp[g] > 0); gl = len(gp) - gw
wl = collections.Counter(r['won'] for r in sc)
per_leg = 100.0 * wl[1] / (wl[1] + wl[0]) if (wl[1] + wl[0]) else 0
per_game = 100.0 * gw / len(gp) if gp else 0
print('  per-LEG  win%%: won=%d lost=%d  = %.1f%%   (scoreboard win_rate=%s)' % (wl[1], wl[0], per_leg, cs['win_rate']))
print('  per-GAME win%% (game net>0): games=%d won=%d lost=%d = %.1f%%' % (len(gp), gw, gl, per_game))

# ---- (3) LOSSES ----
losses = [r for r in sc if r['won'] == 0]
print('\n== (3) LOSSES ==  scoreable won=%d lost=%d  (hand win%% = %.1f)' % (
    wl[1], wl[0], per_leg))
print('  sample losing rows (up to 12): slug | outcome | avgpx curpx | pnl')
for r in losses[:12]:
    print('    %-44s | %-22s | %.2f %.2f | %+8.1f' % (
        (r['slug'] or '')[:44], (r['outcome'] or '')[:22], r['avg_price'] or 0, r['cur_price'] or 0, r['realized_pnl'] or 0))

# ---- DEFECT CHECKS ----
print('\n== DEFECT CHECK A: won == (cur_price >= 0.9) ? ==')
mm = [r for r in rows if (1 if (r['cur_price'] or 0) >= 0.9 else 0) != r['won']]
print('  mismatches = %d (expect 0)' % len(mm))
for r in mm[:8]:
    print('    curpx=%.3f won=%s slug=%s' % (r['cur_price'] or 0, r['won'], (r['slug'] or '')[:44]))

print('\n== DEFECT CHECK B: win_rate scoreboard vs hand ==')
print('  scoreboard win_rate=%s ; hand wins/(wins+losses)=%.4f ; scoreboard wins=%s losses=%s ; hand wins=%d losses=%d' % (
    cs['win_rate'], (wl[1] / (wl[1] + wl[0]) if (wl[1] + wl[0]) else 0), cs['wins'], cs['losses'], wl[1], wl[0]))
wp = [r['avg_price'] for r in sc if r['won'] == 1 and r['avg_price'] is not None]
print('  avg_win_price scoreboard=%s ; hand mean over %d won rows=%.4f' % (cs['avg_win_price'], len(wp), (sum(wp)/len(wp) if wp else 0)))
netsc = sum(r['realized_pnl'] or 0 for r in sc); costsc = sum(r['cost_basis'] or 0 for r in sc)
print('  roi scoreboard=%s ; hand net/cost = %.4f (net=%.1f cost=%.1f)' % (cs['roi'], (netsc/costsc if costsc else 0), netsc, costsc))

print('\n== DEFECT CHECK C: the ANOM rows (pnl_anomaly=1) ==')
anom = [r for r in rows if r['pnl_anomaly'] == 1]
print('  count=%d (scoreboard n_anomaly=%s)' % (len(anom), cs['n_anomaly']))
for r in anom:
    print('    slug=%-40s | out=%-18s | avgpx=%.3f tb=%.1f cost=%.1f pnl=%+.1f | reason=%s suspect=%s won=%s' % (
        (r['slug'] or '')[:40], (r['outcome'] or '')[:18], r['avg_price'] or 0, r['total_bought'] or 0,
        r['cost_basis'] or 0, r['realized_pnl'] or 0, r['anomaly_reason'], r['pnl_suspect'], r['won']))

print('\n== DEFECT CHECK D: two-sided grouping (spreads/totals must NOT group as two-sided) ==')
byc = collections.defaultdict(set)
mkt = {}
for r in rows:
    byc[r['condition_id']].add(r['outcome_index'])
    mkt[r['condition_id']] = (r['slug'] or '')
two = [cid for cid, o in byc.items() if len(o) > 1]
print('  condition_ids held on >1 outcome_index = %d (scoreboard n_two_sided=%s, n_condition_ids=%s)' % (
    len(two), cs['n_two_sided'], cs['n_condition_ids']))
for cid in two[:8]:
    print('    two-sided cid=%s  slug=%s  outcomes=%s' % (cid[:14], mkt[cid][:40], sorted(byc[cid])))
# show one game's legs -> distinct condition_ids across ML/spread/total
ex = None
for r in rows:
    if '2026-08-23' in (r['slug'] or '') and any(t in (r['slug'] or '') for t in ('ari', 'cin')):
        ex = gamekey(r); break
if ex:
    print('  -- example game %s: its legs (each market = distinct condition_id) --' % ex)
    for r in [x for x in rows if gamekey(x) == ex]:
        print('     cid=%s oi=%s | %-40s | %-18s | won=%s pnl=%+.0f' % (
            r['condition_id'][:14], r['outcome_index'], (r['slug'] or '')[:40], (r['outcome'] or '')[:18],
            r['won'], r['realized_pnl'] or 0))

os = c.execute("SELECT n_resolved,roi,avg_win_price FROM pm_category_onesided_stats WHERE wallet=? AND category='mlb'", (wallet,)).fetchone()
print('\n== one-sided slice (pm_category_onesided_stats mlb) ==', dict(os) if os else None)
c.close()
print('\n== END SDTrading mlb characterization ==')
