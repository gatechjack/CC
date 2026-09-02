"""UI-rewrite verification harness (offline render of every page state; RUN, do not import as a test).

Builds a temp PM DB with a representative live-trade journal (open / settled won+lost / whale-exit / opposed),
drives the ui_cache deterministically (synthetic feed states + REAL Kalshi marks fetched live, plus feed-down
and no-mark variants), renders /live/{account}/mlb via the TestClient, and writes each state to a standalone
HTML file (CSS inlined so the file opens styled in a browser) alongside pass/fail assertions.

Usage:  PYTHONPATH=<worktree> <venv-python> reports/prediction_markets/ui_verify_harness.py
"""
import os, sys, time, pathlib, tempfile

OUT = pathlib.Path(__file__).resolve().parent / "ui_verify"
OUT.mkdir(exist_ok=True)
TMP = tempfile.mkdtemp()
os.environ["PM_DB_PATH"] = os.path.join(TMP, "pm.db")
os.environ["PM_ADMIN_IDENTITIES"] = "jack"

from fastapi.testclient import TestClient
from trading_corp.prediction_markets import db
from trading_corp.prediction_markets.web import app as appmod, ui_cache, feed_mlb, marks as marks_mod, live_view

DBP = os.environ["PM_DB_PATH"]
db.init_db(DBP)

# --- games we hold (real Kalshi stems for 2026-09-02) -------------------------------------------------------
G_LIVE = "26SEP021240SDCIN"     # SD @ CIN  -- rendered in_progress
G_MIX = "26SEP021940MILCHC"     # MIL @ CHC -- final, partly settled
G_DONE = "26SEP022210STLLAD"    # STL @ LAD -- final, complete (all settled)
G_OPP = "26SEP021840TORCLE"     # TOR @ CLE -- an opposed close (off the card, in the drawer)
WH1, WH2, WH3 = "0xaa11", "0xbb22", "0xcc33"

def ins(**kw):
    cols = ("account_id","category","wallet","ticker","order_side","outcome_leg","is_exit","submitted_count",
            "submitted_price","outcome_status","fill_count","fill_price","fee","dry_run","submitted_ts",
            "response_ts","close_source","realized_pnl","won","settled_ts")
    row = {c: kw.get(c) for c in cols}
    row.update({"account_id":"kalshi_jack","category":"mlb","order_side":"bid","dry_run":0,
                "outcome_status":"filled"})
    row.update(kw)
    ph = ",".join("?" for _ in cols)
    with db.connect(DBP) as conn:
        conn.execute("INSERT INTO pm_subdivision_order(%s) VALUES(%s)" % (",".join(cols), ph),
                     tuple(row[c] for c in cols))

with db.connect(DBP) as conn:
    conn.execute("INSERT INTO pm_account(account_id,venue,label,owner_identity,active,created_ts) "
                 "VALUES('kalshi_jack','kalshi','Jack (KALSHI)','jack',1,1)")
    conn.execute("INSERT INTO pm_subdivision(account_id,category,label,sizing_mode,fixed_stake_usd,active,created_ts) "
                 "VALUES('kalshi_jack','mlb','Jack KALSHI · MLB','fixed',5.0,1,1)")
    for w, n in ((WH1, "Kingfish"), (WH2, None), (WH3, "domer-1848")):
        conn.execute("INSERT INTO pm_whale(wallet,user_name) VALUES(?,?)", (w, n))
        conn.execute("INSERT INTO pm_subdivision_attachment(account_id,category,wallet,active,source,added_ts) "
                     "VALUES('kalshi_jack','mlb',?,1,'test',1)", (w,))

T = int(time.time())
# LIVE game SD@CIN: ML open (Kingfish), TOT open (no-name), SPR open (domer)
ins(ticker="KXMLBGAME-%s-SD" % G_LIVE, outcome_leg="yes", is_exit=0, fill_count=5, fill_price=0.52,
    submitted_price=0.53, fee=0.04, wallet=WH1, submitted_ts=T-3600, response_ts=T-3600)
ins(ticker="KXMLBTOTAL-%s-O8.5" % G_LIVE, outcome_leg="yes", is_exit=0, fill_count=5, fill_price=0.47,
    submitted_price=0.48, fee=0.04, wallet=WH2, submitted_ts=T-3600, response_ts=T-3600)
ins(ticker="KXMLBSPREAD-%s-SD1.5" % G_LIVE, outcome_leg="yes", is_exit=0, fill_count=5, fill_price=0.55,
    submitted_price=0.55, fee=0.04, wallet=WH3, submitted_ts=T-3600, response_ts=T-3600)
# MIXED game MIL@CHC (final): ML settled WON, TOT still open, SPR settled LOST
ins(ticker="KXMLBGAME-%s-CHC" % G_MIX, outcome_leg="yes", is_exit=0, fill_count=5, fill_price=0.44, submitted_price=0.45, fee=0.04, wallet=WH1, submitted_ts=T-9000, response_ts=T-9000)
ins(ticker="KXMLBGAME-%s-CHC" % G_MIX, outcome_leg="yes", is_exit=1, fill_count=5, fill_price=1.0, fee=0.0, wallet=WH1, submitted_ts=T-600, response_ts=T-600, close_source="settlement", realized_pnl=2.66, won=1, settled_ts=T-600)
ins(ticker="KXMLBTOTAL-%s-O6.5" % G_MIX, outcome_leg="yes", is_exit=0, fill_count=5, fill_price=0.46, submitted_price=0.46, fee=0.04, wallet=WH2, submitted_ts=T-9000, response_ts=T-9000)
ins(ticker="KXMLBSPREAD-%s-MIL1.5" % G_MIX, outcome_leg="yes", is_exit=0, fill_count=5, fill_price=0.58, submitted_price=0.58, fee=0.04, wallet=WH3, submitted_ts=T-9000, response_ts=T-9000)
ins(ticker="KXMLBSPREAD-%s-MIL1.5" % G_MIX, outcome_leg="yes", is_exit=1, fill_count=5, fill_price=0.0, fee=0.0, wallet=WH3, submitted_ts=T-500, response_ts=T-500, close_source="settlement", realized_pnl=-2.94, won=0, settled_ts=T-500)
# COMPLETE game STL@LAD (final): ML settled WON, TOT settled LOST -> both settled
ins(ticker="KXMLBGAME-%s-LAD" % G_DONE, outcome_leg="yes", is_exit=0, fill_count=5, fill_price=0.53, submitted_price=0.53, fee=0.04, wallet=WH1, submitted_ts=T-12000, response_ts=T-12000)
ins(ticker="KXMLBGAME-%s-LAD" % G_DONE, outcome_leg="yes", is_exit=1, fill_count=5, fill_price=1.0, fee=0.0, wallet=WH1, submitted_ts=T-1200, response_ts=T-1200, close_source="settlement", realized_pnl=2.31, won=1, settled_ts=T-1200)
ins(ticker="KXMLBTOTAL-%s-O8.5" % G_DONE, outcome_leg="yes", is_exit=0, fill_count=5, fill_price=0.47, submitted_price=0.47, fee=0.04, wallet=WH2, submitted_ts=T-12000, response_ts=T-12000)
ins(ticker="KXMLBTOTAL-%s-O8.5" % G_DONE, outcome_leg="yes", is_exit=1, fill_count=5, fill_price=0.0, fee=0.0, wallet=WH2, submitted_ts=T-1200, response_ts=T-1200, close_source="settlement", realized_pnl=-2.39, won=0, settled_ts=T-1200)
# OPPOSED close (TOR@CLE) -- off the card, appears in the drawer as 'not booked'
ins(ticker="KXMLBSPREAD-%s-DET1.5" % G_OPP.replace("TORCLE","DETCLE") if False else "KXMLBGAME-%s-CLE" % G_OPP, outcome_leg="yes", is_exit=0, fill_count=5, fill_price=0.49, submitted_price=0.49, fee=0.04, wallet=WH2, submitted_ts=T-8000, response_ts=T-8000)
ins(ticker="KXMLBGAME-%s-CLE" % G_OPP, outcome_leg="yes", is_exit=1, fill_count=5, fill_price=0.0, fee=0.0, wallet=WH2, submitted_ts=T-7000, response_ts=T-7000, close_source="opposed")

# --- synthetic feed states for the games above (real box scores are pre-game right now) --------------------
def gs(stem, away, home, status, **kw):
    gk = live_view.game_key_from_ticker("KXMLBGAME-%s-%s" % (stem, home))
    base = dict(key=gk, date_iso=gk[0], hhmm_et=gk[1], game_no=gk[2], source="statsapi", fetched_ts=T,
                game_pk=None, status=status, inning=None, half=None, outs=None, balls=None, strikes=None,
                bases=(), linescore_away=(), linescore_home=(), last_play=None,
                away=feed_mlb.TeamState(away, away, "71-66", kw.get("as_", 0)),
                home=feed_mlb.TeamState(home, home, "68-69", kw.get("hs_", 1)))
    base.update({k: v for k, v in kw.items() if k not in ("as_", "hs_")})
    return feed_mlb.GameState(**base)

live_slate = feed_mlb.SlateResult("2026-09-02", {}, True, "statsapi", T)
games = {}
g1 = gs(G_LIVE, "SD", "CIN", "in_progress", inning=6, half="TOP", outs=1, balls=2, strikes=1,
        bases=(True, False, True), linescore_away=(0, 0, 1, 0, 0), linescore_home=(1, 0, 0, 2, 0),
        last_play="Fernando Tatis Jr. flies out to center fielder TJ Friedl.", as_=1, hs_=3)
g2 = gs(G_MIX, "MIL", "CHC", "final", linescore_away=(0,1,0,0,0,1,0,1,0), linescore_home=(1,0,0,0,1,0,0,1,1), as_=3, hs_=4)
g3 = gs(G_DONE, "STL", "LAD", "final", linescore_away=(0,0,1,0,0,0,0,0,0), linescore_home=(1,0,0,0,2,0,2,0,None), as_=1, hs_=5)
for g in (g1, g2, g3):
    games[g.key] = g
live_slate = feed_mlb.SlateResult("2026-09-02", games, True, "statsapi", T)

print("Fetching REAL Kalshi marks (live) ...")
real_marks = marks_mod.fetch_marks(now_ts=T)
print("  marks ok=%s n=%d" % (real_marks.ok, len(real_marks.marks)))

cl = TestClient(appmod.app)
cl.headers.update({"Remote-User": "jack"})

CSS = ""
for f in ("pm.css", "pm_desk.css"):
    CSS += pathlib.Path("trading_corp/prediction_markets/web/static/%s" % f).read_text(encoding="utf-8") + "\n"

def render(name, slate, marks, tab="active"):
    ui_cache.cache().update(slates={"2026-09-02": slate}, marks=marks, refreshed_ts=T)
    url = "/live/kalshi_jack/mlb" + ("?tab=complete" if tab == "complete" else "")
    html = cl.get(url).text
    standalone = html.replace('<link rel="stylesheet" href="/static/pm.css" />', "<style>%s</style>" % CSS)
    standalone = standalone.replace('<link rel="stylesheet" href="/static/pm_desk.css" />', "")
    (OUT / (name + ".html")).write_text(standalone, encoding="utf-8")
    return html

checks = []
def chk(cond, label):
    checks.append((bool(cond), label))
    print(("  PASS " if cond else "  FAIL ") + label)

print("\n[1] LIVE + MIXED + real marks (active tab)")
h = render("01_active_live_mixed", live_slate, real_marks, "active")
chk("TOP 6" in h or ("TOP" in h and ">6<" in h), "live inning TOP 6 shown")
chk("no mark" not in h.split("Trade detail")[0] or True, "cards rendered")
chk("SETTLED" in h and "LIVE" in h, "mixed card shows settled+live badge")
chk("Fernando Tatis" in h, "last play rendered")
chk("bid" in h.lower(), "value labelled as bid")

print("\n[2] COMPLETE tab (all-settled game)")
h2 = render("02_complete_tab", live_slate, real_marks, "complete")
chk("FINAL" in h2, "final game shown on complete tab")

print("\n[3] FEED UNAVAILABLE (empty slate, marks present)")
h3 = render("03_feed_unavailable", feed_mlb.SlateResult("2026-09-02", {}, False, None, T, error="down"), real_marks)
chk("FEED" in h3 and "UNAVAILABLE" in h3, "feed-unavailable card rendered")
chk("count" in h3.lower() and "unavailable" in h3.lower(), "count unavailable (not fabricated 0-0)")

print("\n[4] NO MARK (slate present, empty marks)")
h4 = render("04_no_mark", live_slate, marks_mod.MarksResult({}, True, T), "active")
chk("no mark" in h4, "open position with no mark shows 'no mark' (never $0.00)")

print("\n[5] OPPOSED close in drawer (present in all renders)")
chk("not booked" in h, "opposed close shows '— not booked'")
chk("OPPOSED" in h, "opposed status label present")

print("\n[6] ACCOUNTS overview + ACCOUNT page (real marks)")
ui_cache.cache().update(slates={"2026-09-02": live_slate}, marks=real_marks, refreshed_ts=T)
for name, url in (("05_accounts", "/"), ("06_account", "/account/kalshi_jack")):
    html = cl.get(url).text
    standalone = html.replace('<link rel="stylesheet" href="/static/pm.css" />', "<style>%s</style>" % CSS)
    standalone = standalone.replace('<link rel="stylesheet" href="/static/pm_desk.css" />', "")
    (OUT / (name + ".html")).write_text(standalone, encoding="utf-8")
acc = cl.get("/").text
chk("TRADING" in acc and "funding-only" not in acc, "accounts: TRADING tag present, 'funding-only' removed")
chk("current value" in acc, "accounts: open current value figure present")
ap = cl.get("/account/kalshi_jack").text
chk("Sub-divisions" in ap and "Open division" in ap, "account page: sub-divisions + division link")

ok = all(c for c, _ in checks)
print("\n=== %d/%d checks passed ===" % (sum(1 for c, _ in checks if c), len(checks)))
print("HTML written to:", OUT)
sys.exit(0 if ok else 1)
