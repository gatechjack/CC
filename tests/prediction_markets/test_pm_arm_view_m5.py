"""M5 -- the engine-web /pm/arm GLOBAL arm/disarm control. Proves the load-bearing behaviour:
  - admin-gated, FAIL-CLOSED: a non-admin (or no identity) is REFUSED on BOTH GET and POST -- and a refused POST
    calls NEITHER arm nor disarm (proven by a POST as karen, not by a hidden button);
  - the arm/disarm INVOCATION is the GLOBAL master (global_=True), by the caller's identity;
  - ★ RIDER 1 -- the UI NEVER clears a latch: arm() is called WITHOUT require_latch_clear, and a LatchedError is
    caught -> 'latched_refused', never a silent re-arm;
  - ★ RIDER 2 (implicit) -- disarm ALWAYS proceeds (the kill direction) for an admin;
  - the page renders the ARMED / DISARMED / latched states and, when latched, DISABLES arm + shows the CLI clear.

Offline FastAPI TestClient over a MINIMAL app (pm_arm_view.register only) with `arm` monkeypatched -> NO real
legacy DB is touched. Needs fastapi/jinja2 -> runs under pytest on the box at Gate-A."""
import types
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

from trading_corp.web import pm_arm_view
from trading_corp.prediction_markets import arm as armmod

_TPL = Path(pm_arm_view.__file__).parent / "templates"


def _stage1_badge(request):   # base.html calls this global unconditionally; stub its shape so the shell renders.
    return types.SimpleNamespace(division="pm", execution_mode="paper", git_sha="test",
                                 live_since_iso="", live_since_label="0m")


def _client(monkeypatch, *, admins="jack", state=None, calls=None, latched_on_arm=False):
    st = state if state is not None else {"global": {"armed": False}, "global_armed": False}
    monkeypatch.setattr(armmod, "read_status", lambda *a, **k: st)

    def _arm(*a, **k):
        if calls is not None:
            calls.append(("arm", a, k))
        if latched_on_arm:
            raise armmod.LatchedError("latched")

    def _disarm(*a, **k):
        if calls is not None:
            calls.append(("disarm", a, k))

    monkeypatch.setattr(armmod, "arm", _arm)
    monkeypatch.setattr(armmod, "disarm", _disarm)
    if admins is None:
        monkeypatch.delenv("PM_ADMIN_IDENTITIES", raising=False)
    else:
        monkeypatch.setenv("PM_ADMIN_IDENTITIES", admins)

    app = FastAPI()
    tpl = Jinja2Templates(directory=str(_TPL))
    tpl.env.globals["stage1_badge"] = _stage1_badge
    app.state.templates = tpl
    app.state.deps = None
    pm_arm_view.register(app)
    return TestClient(app, raise_server_exceptions=False)


# ── the admin gate (fail-closed) -- prove the DENY by requesting AS the non-admin ──────────────────────────────
def test_get_forbidden_for_non_admin(monkeypatch):
    cl = _client(monkeypatch)
    assert cl.get("/pm/arm", headers={"Remote-User": "karen"}).status_code == 403


def test_get_forbidden_when_no_identity(monkeypatch):
    cl = _client(monkeypatch)
    assert cl.get("/pm/arm").status_code == 403                      # header absent -> not admin -> 403


def test_get_forbidden_when_env_unset(monkeypatch):
    cl = _client(monkeypatch, admins=None)                           # PM_ADMIN_IDENTITIES unset -> nobody admin
    assert cl.get("/pm/arm", headers={"Remote-User": "jack"}).status_code == 403


def test_post_disarm_as_non_admin_refused_and_no_write(monkeypatch):
    calls = []
    cl = _client(monkeypatch, calls=calls)
    r = cl.post("/pm/arm", data={"action": "disarm"}, headers={"Remote-User": "karen"})
    assert r.status_code == 403
    assert calls == []                                               # ★ refused BEFORE any arm-state write


def test_post_arm_as_non_admin_refused_and_no_write(monkeypatch):
    calls = []
    cl = _client(monkeypatch, calls=calls)
    r = cl.post("/pm/arm", data={"action": "arm"}, headers={"Remote-User": "karen"})
    assert r.status_code == 403
    assert calls == []


# ── the arm/disarm invocation (admin) + the riders ────────────────────────────────────────────────────────────
def test_disarm_as_admin_calls_global_disarm(monkeypatch):
    calls = []
    cl = _client(monkeypatch, calls=calls)
    r = cl.post("/pm/arm", data={"action": "disarm"}, headers={"Remote-User": "jack"}, follow_redirects=False)
    assert r.status_code == 303 and "notice=disarmed" in r.headers["location"]
    assert len(calls) == 1 and calls[0][0] == "disarm"
    assert calls[0][2].get("global_") is True                       # GLOBAL master
    assert calls[0][2].get("by") == "jack"                          # by the caller's identity


def test_arm_as_admin_calls_global_arm_without_clearing_latch(monkeypatch):
    calls = []
    cl = _client(monkeypatch, calls=calls)
    r = cl.post("/pm/arm", data={"action": "arm"}, headers={"Remote-User": "jack"}, follow_redirects=False)
    assert r.status_code == 303 and "notice=armed" in r.headers["location"]
    assert len(calls) == 1 and calls[0][0] == "arm"
    assert calls[0][2].get("global_") is True
    # ★ RIDER 1: the UI must NEVER clear a latch -> require_latch_clear is NOT True.
    assert calls[0][2].get("require_latch_clear") is not True


def test_arm_latched_is_refused_never_cleared(monkeypatch):
    calls = []
    cl = _client(monkeypatch, calls=calls, latched_on_arm=True)      # arm() raises LatchedError
    r = cl.post("/pm/arm", data={"action": "arm"}, headers={"Remote-User": "jack"}, follow_redirects=False)
    assert r.status_code == 303 and "notice=latched_refused" in r.headers["location"]
    # arm WAS attempted, but WITHOUT require_latch_clear -> the latch is left set (never silently cleared).
    assert len(calls) == 1 and calls[0][0] == "arm"
    assert calls[0][2].get("require_latch_clear") is not True


def test_unknown_action_is_noop(monkeypatch):
    calls = []
    cl = _client(monkeypatch, calls=calls)
    r = cl.post("/pm/arm", data={"action": "wat"}, headers={"Remote-User": "jack"}, follow_redirects=False)
    assert r.status_code == 303 and "notice=noop" in r.headers["location"]
    assert calls == []                                              # nothing written on an unrecognized action


# ── the page renders state (admin) ────────────────────────────────────────────────────────────────────────────
def test_page_renders_armed(monkeypatch):
    st = {"global": {"armed": True, "latched": False, "reason": "operator_arm", "by": "jack",
                     "ts": "2026-09-01T00:00:00Z", "source": "web"}, "global_armed": True}
    cl = _client(monkeypatch, state=st)
    html = cl.get("/pm/arm", headers={"Remote-User": "jack"}).text
    assert "GLOBAL ARMED" in html
    assert "live-disarm --global" in html                          # the CLI-authoritative kill note is always shown


def test_page_renders_disarmed(monkeypatch):
    cl = _client(monkeypatch, state={"global": {"armed": False}, "global_armed": False})
    html = cl.get("/pm/arm", headers={"Remote-User": "jack"}).text
    assert "GLOBAL DISARMED" in html


def test_page_latched_disables_arm_and_shows_clear(monkeypatch):
    st = {"global": {"armed": False, "latched": True, "auto_trigger": "count_ceiling"}, "global_armed": False}
    cl = _client(monkeypatch, state=st)
    html = cl.get("/pm/arm", headers={"Remote-User": "jack"}).text
    assert "latched" in html and "count_ceiling" in html
    assert "disabled" in html                                       # the ARM button is disabled when latched
    assert "--clear-latch" in html                                 # the CLI acknowledge path is shown


if __name__ == "__main__":
    print("This suite needs fastapi/jinja2 (a pytest TestClient run) -- run under pytest on the box at Gate-A.")
