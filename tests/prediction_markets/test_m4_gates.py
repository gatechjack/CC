"""M4 -- SERVER-SIDE authorization: account-visibility scoping + the write-action admin gate (2026-09-01).

★ The load-bearing proofs Jack ruled: prove Karen CANNOT promote/attach with a test that POSTs AS HER (a direct
request the server refuses), NOT one that checks a button is hidden. So every DENY here is a real POST with
`Remote-User: karen` asserting 403 -- the gate is the boundary, the hidden button is only a hint. Complementary
ALLOW proofs show an admin is let through (the gate is not a blanket block) and that Analyze stays UNGATED (Karen
is the promotion judge). Plus the fail-closed scoping: admin sees ALL accounts, Karen sees ONLY hers, no identity
sees NOTHING, and the ENV-LEADS lockout (PM_ADMIN_IDENTITIES unset -> even Jack sees nothing).

Offline FastAPI TestClient over a temp PM DB (schema >=16). `Remote-User` is the Authelia identity header Caddy
forwards (M4 design doc). Runs on the box at Gate-A where fastapi/jinja2 are present.
"""
from fastapi.testclient import TestClient
from fastapi.responses import PlainTextResponse
from trading_corp.prediction_markets import db
import trading_corp.prediction_markets.web.app as appmod


def _seed(conn):
    # jack: NULL owner (admin-only via env) + a traded MLB sub-division; karen: owner_identity='karen', display-only.
    conn.execute("INSERT INTO pm_account (account_id,venue,secret_ref,label,owner_identity,active,created_ts) "
                 "VALUES ('kalshi_jack','kalshi','KALSHI','Jack (KALSHI)',NULL,1,1787000000)")
    conn.execute("INSERT INTO pm_account (account_id,venue,secret_ref,label,owner_identity,active,created_ts) "
                 "VALUES ('kalshi_karen','kalshi','kalshi_karen','Karen','karen',1,1787000000)")
    conn.execute("INSERT INTO pm_subdivision (account_id,category,label,market_types,sizing_mode,fixed_stake_usd,"
                 "active,created_ts) VALUES ('kalshi_jack','mlb','Jack MLB','moneyline','contracts',0.01,1,1787000000)")


def _client(monkeypatch, tmp_path, admins="jack"):
    p = str(tmp_path / "pm.db"); monkeypatch.setenv("PM_DB_PATH", p); db.init_db(p)
    if admins is None:
        monkeypatch.delenv("PM_ADMIN_IDENTITIES", raising=False)
    else:
        monkeypatch.setenv("PM_ADMIN_IDENTITIES", admins)
    with db.connect(p) as conn:
        _seed(conn)
    return TestClient(appmod.app, raise_server_exceptions=False)


# ── ACCOUNT-VISIBILITY SCOPING (the overview + the per-account page) ──────────────────────────────────────────
def test_overview_admin_sees_all(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    html = cl.get("/", headers={"Remote-User": "jack"}).text
    assert 'href="/account/kalshi_jack"' in html and 'href="/account/kalshi_karen"' in html   # admin: both


def test_overview_karen_sees_only_hers(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    html = cl.get("/", headers={"Remote-User": "karen"}).text
    assert 'href="/account/kalshi_karen"' in html          # her own (owner_identity match)
    assert 'href="/account/kalshi_jack"' not in html       # ★ the NULL-owner account is admin-only -> hidden from her


def test_overview_no_identity_sees_nothing(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    html = cl.get("/").text                                 # no Remote-User header
    assert 'href="/account/kalshi_jack"' not in html and 'href="/account/kalshi_karen"' not in html  # fail-closed


def test_overview_env_unset_locks_out_jack(monkeypatch, tmp_path):
    # ★ ENV-LEADS: PM_ADMIN_IDENTITIES unset -> jack is NOT admin -> his account has NULL owner -> he sees NOTHING.
    # This is the lockout the deploy ordering exists to prevent (set the env BEFORE the scoping enforces).
    cl = _client(monkeypatch, tmp_path, admins=None)
    html = cl.get("/", headers={"Remote-User": "jack"}).text
    assert 'href="/account/kalshi_jack"' not in html and 'href="/account/kalshi_karen"' not in html


def test_account_page_karen_forbidden_for_jack(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    r = cl.get("/account/kalshi_jack", headers={"Remote-User": "karen"})
    assert r.status_code == 403                             # ★ exists, but not hers -> 403 (not 404)


def test_account_page_karen_sees_her_own(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    r = cl.get("/account/kalshi_karen", headers={"Remote-User": "karen"})
    assert r.status_code == 200 and "Karen" in r.text


def test_account_page_admin_sees_jack(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    r = cl.get("/account/kalshi_jack", headers={"Remote-User": "jack"})
    assert r.status_code == 200 and "Jack (KALSHI)" in r.text


def test_account_page_unknown_is_404_not_403(monkeypatch, tmp_path):
    # a non-existent account is NOT the same as a forbidden one: 404 (does not exist) vs 403 (exists, not yours).
    cl = _client(monkeypatch, tmp_path)
    assert cl.get("/account/nope", headers={"Remote-User": "jack"}).status_code == 404


# ── THE WRITE-ACTION ADMIN GATE -- Karen POSTs directly and is REFUSED, server-side ──────────────────────────
def test_promote_as_karen_forbidden(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    r = cl.post("/farm/mlb/promote/0xwhale", headers={"Remote-User": "karen"})
    assert r.status_code == 403                             # ★ Karen cannot promote (proven by a POST, not a hidden button)


def test_demote_as_karen_forbidden(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    r = cl.post("/farm/mlb/demote/0xwhale", headers={"Remote-User": "karen"})
    assert r.status_code == 403


def test_attach_as_karen_forbidden(monkeypatch, tmp_path):
    cl = _client(monkeypatch, tmp_path)
    r = cl.post("/live/kalshi_jack/mlb/attach/0xwhale", headers={"Remote-User": "karen"})
    assert r.status_code == 403                             # ★ attach is the highest-stakes action -> gated hardest


def test_write_actions_no_identity_forbidden(monkeypatch, tmp_path):
    # fail-closed: no identity header -> not admin -> refused (an unauthenticated replay cannot mutate).
    cl = _client(monkeypatch, tmp_path)
    assert cl.post("/farm/mlb/promote/0xwhale").status_code == 403
    assert cl.post("/live/kalshi_jack/mlb/attach/0xwhale").status_code == 403


def test_write_action_admin_allowed(monkeypatch, tmp_path):
    # the gate is not a blanket block: an ADMIN is let through (demote is idempotent + tolerant of a missing pin,
    # so this exercises the real farm-action path off the gate and lands on the 303 PRG redirect).
    cl = _client(monkeypatch, tmp_path)
    r = cl.post("/farm/mlb/demote/0xwhale", headers={"Remote-User": "jack"}, follow_redirects=False)
    assert r.status_code == 303                             # admin proceeds past the gate


def test_gate_helper_admin_passes(monkeypatch, tmp_path):
    # unit-level: the gate returns None (proceed) for an admin, a 403 response for a non-admin.
    _client(monkeypatch, tmp_path)                          # sets PM_ADMIN_IDENTITIES=jack
    class _R:
        def __init__(self, h): self.headers = h
    assert appmod._forbid_if_not_admin(_R({"Remote-User": "jack"})) is None
    resp = appmod._forbid_if_not_admin(_R({"Remote-User": "karen"}))
    assert isinstance(resp, PlainTextResponse) and resp.status_code == 403


def test_analyze_is_not_admin_gated_for_karen(monkeypatch, tmp_path):
    # ★ Analyze stays UNGATED -- Karen is the promotion judge (spend-capped). Isolate the GATE decision from
    # analyze's DB/network internals by stubbing the work + the template; a gate REFUSAL would be exactly 403, so
    # asserting != 403 (and here == 200) proves Analyze is not behind the admin gate.
    cl = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(appmod, "_analysis_is_cached", lambda w, c: True)        # skip the grounding network path
    monkeypatch.setattr(appmod, "_run_analyze", lambda *a, **k: {"wallet": "0xw", "category": "mlb"})
    monkeypatch.setattr(appmod.templates, "TemplateResponse", lambda *a, **k: PlainTextResponse("ok"))
    r = cl.post("/farm/analyze/0xw/mlb", headers={"Remote-User": "karen"})
    assert r.status_code != 403 and r.status_code == 200                          # Karen MAY Analyze


if __name__ == "__main__":
    print("This suite needs fastapi/jinja2 (a pytest TestClient run) -- run under pytest on the box at Gate-A.")
