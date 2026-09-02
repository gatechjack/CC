"""Multi-account M2 -- the accounts overview (/) + per-account page (/account/{id}), DISPLAY-ONLY (2026-09-01).
Offline FastAPI TestClient, PM DB (schema 15) + a read-only legacy arm read. Proves, against TWO accounts (jack
populated, karen display-only), the ruled behaviour:
  - / lists both accounts; a PM-traded account shows realized / win-loss / SAMPLE / open-at-cost SEPARATELY with
    the thin-sample caveat travelling WITH the number (R2c discipline); a 0-subdivision account states it is NOT
    traded by PM (display-only) in the COPY, not an empty frame;
  - /account/{id} shows the per-subdivision P&L table (jack) or the display-only limitation (karen);
  - the global arm STATE is visible read-only (R4); there is NO arm/attach/place control on any account page;
  - an unknown account 404s.
"""
import time
from fastapi.testclient import TestClient
from trading_corp.prediction_markets import db


def _seed(conn):
    conn.execute("INSERT INTO pm_account (account_id,venue,secret_ref,label,active,created_ts) "
                 "VALUES ('kalshi_jack','kalshi','KALSHI','Jack (KALSHI)',1,1787000000)")
    conn.execute("INSERT INTO pm_account (account_id,venue,secret_ref,label,active,created_ts) "
                 "VALUES ('kalshi_karen','kalshi','kalshi_karen','Karen',1,1787000000)")
    conn.execute("INSERT INTO pm_subdivision (account_id,category,label,market_types,sizing_mode,fixed_stake_usd,"
                 "active,created_ts) VALUES ('kalshi_jack','mlb','Jack MLB','moneyline','contracts',0.01,1,1787000000)")
    conn.execute("INSERT INTO pm_subdivision_attachment (account_id,category,wallet,active,source,added_ts) "
                 "VALUES ('kalshi_jack','mlb','0xw',1,'seed',1787000000)")
    base = dict(account_id="kalshi_jack", category="mlb", wallet="0xw", condition_id="0xc", outcome_index=1,
                signal_id="s", client_order_id="c", ticker="KXMLBGAME-26AUG311840SDCIN-SD", order_side="bid",
                outcome_leg="yes", is_exit=0, submitted_count=5, submitted_price=0.60, time_in_force="ioc",
                outcome_status="filled", fill_count=5.0, fill_price=0.60, fee=0.0, dry_run=0,
                submitted_ts=1788200000, response_ts=1788200000, close_source=None, realized_pnl=None, won=None)
    def ins(**kw):
        r = dict(base); r.update(kw); cols=",".join(r); conn.execute(
            "INSERT INTO pm_subdivision_order (%s) VALUES (%s)" % (cols, ",".join(["?"]*len(r))), tuple(r.values()))
    ins(client_order_id="c1")                                                              # open entry
    ins(client_order_id="c2", is_exit=1, close_source="settlement", fill_price=1.0, realized_pnl=2.0, won=1)  # win
    ins(client_order_id="c3", is_exit=1, close_source="settlement", fill_price=0.0, realized_pnl=-3.0, won=0,
        ticker="KXMLBGAME-26AUG311840SDCIN-CIN", outcome_index=0)                          # loss


def _client(monkeypatch, tmp_path):
    p = str(tmp_path / "pm.db"); monkeypatch.setenv("PM_DB_PATH", p); db.init_db(p)
    # M4: account scoping is now enforced -> the operator viewing these pages must be an authenticated ADMIN, else
    # the fail-closed filter shows nothing. These M2 display tests assume the console operator (admin); the SCOPING
    # semantics themselves (admin sees all / Karen sees only hers / no-identity sees nothing) are proven in
    # test_m4_gates.py, not re-litigated here.
    monkeypatch.setenv("PM_ADMIN_IDENTITIES", "jack")
    with db.connect(p) as conn:
        _seed(conn)
    from trading_corp.prediction_markets.web.app import app
    cl = TestClient(app); cl.headers.update({"Remote-User": "jack"})
    return cl


def test_accounts_overview_lists_both_with_honest_pnl(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    html = cl.get("/").text
    assert ">Accounts</b>" in html                                    # the new top-of-hierarchy nav (copy-desk wraps label in <b>)
    # jack: PM-traded -> realized/win-loss/SAMPLE/open shown separately, caveat with the number
    assert 'href="/account/kalshi_jack"' in html and "Jack (KALSHI)" in html
    assert "Realized" in html and "settled" in html and "1W / 1L" in html   # W/L split (win + loss seeded)
    assert "at cost" in html                                          # open exposure at cost (design label)
    assert "too small a sample" in html or "outcome, not proven edge" in html   # the thin-sample caveat travels with the number
    # karen: renders through the SAME path with the SAME figures -- NO display-only mode (Jack ruled it out; both
    # accounts are trading accounts, and kalshi_karen/mlb is live).
    assert 'href="/account/kalshi_karen"' in html and "Karen" in html
    assert "display-only" not in html.lower() and "not traded by Prediction Markets" not in html
    # R4: global arm state visible (read-only), NO control. No arm row was ever written -> 'absent' -> NEVER ARMED
    # (distinct from DISARMED, which means a row exists and says off; post-deploy item 3).
    assert "GLOBAL NEVER ARMED" in html                               # R4: global arm state visible (read-only; no arm rows -> never armed)
    for tok in ("hx-post", "<form", "<button", "<input", "/attach/"):
        assert tok not in html.lower(), tok


def test_account_page_jack_shows_subdivision_pnl(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    html = cl.get("/account/kalshi_jack").text
    assert "Sub-divisions" in html and "MLB" in html
    assert "1 / 1" in html or "1W / 1L" in html                       # win/loss split visible
    assert "settled" in html.lower()                                  # sample size shown
    assert 'href="/live/kalshi_jack/mlb"' in html                     # links down to the live sub-division
    assert "at cost" in html.lower()                                  # open at cost, not mark
    assert "GLOBAL NEVER ARMED" in html                               # R4: global arm state visible (read-only; no arm rows -> never armed)
    # read-only: no control ELEMENTS ("disarm" as a word is fine -- it is in the read-only "arm/disarm is a CLI action" copy)
    for tok in ("hx-post", "<form", "<button", "<input", "/attach/"):
        assert tok not in html.lower(), tok


def test_account_page_karen_same_path_no_display_only(monkeypatch, tmp_path):
    """Jack ruled out display-only entirely (fix-pass item 1): EVERY account renders the same aggregate figures
    + sub-divisions section. Karen (no sub-division seeded here) shows the figures + a neutral 'no sub-divisions',
    NEVER a 'display-only' / 'not traded by Prediction Markets' state."""
    cl = _client(monkeypatch, tmp_path)
    html = cl.get("/account/kalshi_karen").text
    assert "Aggregate performance" in html and "Realized" in html        # the SAME figures as any account
    assert "display-only" not in html.lower()
    assert "not traded by Prediction Markets" not in html
    assert "no Prediction Markets sub-divisions" not in html


def test_unknown_account_404(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    r = cl.get("/account/nope")
    assert r.status_code == 404
    assert "Account not found" in r.text


def test_account_pages_are_read_only(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    assert cl.post("/account/kalshi_jack").status_code == 405
    assert cl.post("/").status_code == 405


def test_mark_coverage_label_shown_at_full_coverage(monkeypatch, tmp_path):
    """Post-deploy item 1: the 'N of M priced' mark-coverage label is shown under EVERY current-value figure --
    including when N == M (full coverage, neutral) and when M == 0 ('0 positions'), never only in the partial
    case. Jack has exactly one OPEN position (ticker SDCIN-SD, yes leg); we prime the ONE ui_cache both the
    accounts (_cache_marks) and division (build_from_cache) paths read so it prices -> full coverage. Karen holds
    nothing -> '0 positions'. Scoped via monkeypatch so the primed singleton never leaks into another test."""
    from trading_corp.prediction_markets.web import marks as marks_mod, ui_cache
    cl = _client(monkeypatch, tmp_path)
    # jack's single OPEN position nets to the SDCIN-CIN NO leg, 5 contracts (c1+c2 YES net flat; c3 leaves held NO).
    T = "KXMLBGAME-26AUG311840SDCIN-CIN"                                  # the seeded OPEN position's ticker (NO leg)
    mk = marks_mod.Mark(T, yes_bid=0.60, no_bid=0.38, yes_ask=0.64, no_ask=0.40, last=0.61, status="active", as_of=1788200000)
    primed = ui_cache.UICache()
    primed.update(slates={}, marks=marks_mod.MarksResult(marks={T: mk}, ok=True, as_of=1788200000), refreshed_ts=1788200000)
    monkeypatch.setattr(ui_cache, "cache", lambda: primed)
    overview = cl.get("/").text
    assert "1 of 1 priced" in overview                                   # jack: full coverage -> neutral 'N of M priced' shown
    assert "0 positions" in overview                                     # karen: nothing to price -> '0 positions', still labelled
    assert "partial:" not in overview                                    # full coverage is NOT flagged partial
    acct = cl.get("/account/kalshi_jack").text
    assert "1 of 1 priced" in acct                                       # aggregate current-value figure carries it too
    assert "partial:" not in acct


# ── M3 balance display (2026-09-01): per-shard split + banded age + the shard-0-direction line + honest-empty ──
def _write_snap(tmp_path, account_id, by_shard, has_breakdown=True, total=None, ts=None):
    import time
    from trading_corp.prediction_markets import shard_snapshot as ss
    from trading_corp.prediction_markets.shard_balance import ShardBalances
    tot = total if total is not None else sum(by_shard.values())
    with db.connect(str(tmp_path / "pm.db")) as c:
        ss.write_snapshot(c, account_id, ShardBalances(tot, by_shard, has_breakdown), int(ts if ts is not None else time.time()))


def test_balance_section_renders_split_and_age_band(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    _write_snap(tmp_path, "kalshi_jack", {0: 0.0081, 3: 473.5897})
    html = cl.get("/account/kalshi_jack").text
    assert "Cash by shard" in html                                   # the balance section (design heading)
    assert "Shard 3" in html and "473.59" in html                    # the per-shard split (the point, not the total)
    assert 'class="chip' in html and "ago" in html                   # the age band is an obvious chip, not a raw timestamp


def test_balance_honest_empty_present_but_no_snapshot(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)                              # schema 16 -> table present, no rows written
    html = cl.get("/account/kalshi_jack").text
    assert "No balance snapshot yet" in html and "every 5 minutes" in html   # DISTINCT from the table being absent


def test_balance_unknown_breakdown_never_rendered_as_zero(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    _write_snap(tmp_path, "kalshi_karen", {}, has_breakdown=False, total=50.0)   # subaccount-restricted key
    html = cl.get("/account/kalshi_karen").text
    assert "unknown" in html.lower() and "50.00" in html
    assert "Shard 0" not in html                                     # an unknown split is NEVER shown as per-shard $0
