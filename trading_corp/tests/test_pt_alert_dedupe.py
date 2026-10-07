"""PT-held Telegram alert de-dupe (2026-10-05).

Episode-level throttle of the `notifier.reject` push in MaceManager._pt_mark_guard:
one alert when a rung ENTERS the held state, suppressed on every subsequent held tick
(including leg_inversion<->frozen flips on the same dead wing), re-alert only after the
rung RESOLVES (passes the guard) then re-enters held. The per-tick mace_pt_mark_reject
audit row and the guard's True/False decision are UNCHANGED.

Self-contained: the manager is built via object.__new__ with only the attributes
_pt_mark_guard touches, and strategy.assess_pt_mark_trust is monkeypatched to a scripted
MarkTrust sequence (auto-restored by the monkeypatch fixture, so the real guard tests are
unaffected). _pt_mark_guard is async -> driven with asyncio.run.
"""
import asyncio
import datetime
from types import SimpleNamespace

from trading_corp.mace.manager import MaceManager
from trading_corp.mace import strategy as st
from trading_corp.mace.strategy import MarkTrust

NOW = datetime.datetime(2026, 10, 5, 15, 0, 0, tzinfo=datetime.timezone.utc)
MARK = 0.13

LI = ("leg_inversion", False)  # untrusted + alert -> HELD
FR = ("frozen", False)         # untrusted + alert -> HELD (same dead-wing episode)
OKR = ("ok", True)             # trusted -> passes guard (resolve)
NB = ("no_baseline", False)    # untrusted but alert=False -> silent one-cycle hold


class _FakeExec:
    async def mark(self, spec):
        return None

    async def leg_mids(self, spec):
        return {}


class _FakeStore:
    def load_all(self):
        return []


def _make_mgr():
    audits = []
    rejects = []
    m = object.__new__(MaceManager)
    m.cfg = SimpleNamespace(management=SimpleNamespace(time_exit_dte=21))
    m.store = _FakeStore()
    m.executor = _FakeExec()
    m.notifier = SimpleNamespace(reject=lambda **kw: rejects.append(kw))
    m._audit_fn = lambda kind, **p: audits.append((kind, p))
    m._pt_mark_trust = {}
    m._pt_held = set()
    return m, audits, rejects


def _rung(rid):
    return SimpleNamespace(rung_id=rid, symbol="XLE", spec=object(),
                           expiry=datetime.date(2026, 10, 30))


def _drive(mgr, rid, script, monkeypatch):
    """script: list of (reason, trusted_bool). Returns list of _pt_mark_guard bool results."""
    seq = iter(script)

    def fake_assess(*a, **k):
        reason, trusted = next(seq)
        # alert=False only for the no_baseline silent hold; every other untrusted reason alerts.
        alert = (reason != "no_baseline") and (not trusted)
        return MarkTrust(trusted, reason, alert, f"{reason} detail")

    monkeypatch.setattr(st, "assess_pt_mark_trust", fake_assess)
    monkeypatch.setattr(st, "shorter_dated_same_strike_siblings", lambda *a, **k: [])
    r = _rung(rid)
    return [asyncio.run(mgr._pt_mark_guard(r, MARK, NOW, None)) for _ in script]


def test_nine_ticks_flipping_one_alert(monkeypatch):
    mgr, audits, rejects = _make_mgr()
    script = [LI, LI, LI, LI, LI, FR, LI, FR, LI]  # 9 held ticks, reasons flipping
    results = _drive(mgr, "R1", script, monkeypatch)
    assert results == [False] * 9                 # HOLD every tick (decision unchanged)
    assert len(rejects) == 1                        # exactly ONE Telegram alert
    assert len(audits) == 9                         # audit row EVERY tick (trail complete)
    assert all(k == "mace_pt_mark_reject" for k, _ in audits)


def test_replay_today_reset_rung(monkeypatch):
    mgr, audits, rejects = _make_mgr()
    script = [LI, LI, LI, LI, LI, FR, LI, FR]       # observed 2026-10-05 reset-rung sequence
    _drive(mgr, "XLE-reset", script, monkeypatch)
    assert len(rejects) == 1                         # 1, not 4 (reason-keyed), not 8
    assert len(audits) == 8


def test_resolve_then_reenter_realerts(monkeypatch):
    mgr, audits, rejects = _make_mgr()
    results = _drive(mgr, "R2", [LI, OKR, LI], monkeypatch)
    assert results == [False, True, False]           # held, resolved, held again
    assert len(rejects) == 2                          # re-alert after resolve->re-enter
    assert "R2" in mgr._pt_held                       # ends in a fresh held episode


def test_resolve_clears_held_state(monkeypatch):
    mgr, _audits, _rejects = _make_mgr()
    _drive(mgr, "R3", [LI], monkeypatch)
    assert "R3" in mgr._pt_held
    _drive(mgr, "R3", [OKR], monkeypatch)             # passes guard
    assert "R3" not in mgr._pt_held


def test_two_rungs_each_alert_once(monkeypatch):
    mgr, audits, rejects = _make_mgr()
    _drive(mgr, "A", [LI, LI, LI], monkeypatch)
    _drive(mgr, "B", [LI, LI], monkeypatch)
    assert len(rejects) == 2                          # one per rung, not global
    symbols_rids = [r for r in rejects]
    assert len(rejects) == 2 and len(audits) == 5     # 3 + 2 audit rows


def test_no_baseline_silent_then_leg_inversion_alerts_once(monkeypatch):
    mgr, audits, rejects = _make_mgr()
    results = _drive(mgr, "R4", [NB, LI, LI], monkeypatch)
    assert results == [False, False, False]
    assert len(rejects) == 1                          # NB is silent (no alert); first LI alerts once
    assert len(audits) == 2                           # NB writes no audit row; 2 LI ticks do


def test_audit_payload_unchanged_every_tick(monkeypatch):
    mgr, audits, rejects = _make_mgr()
    _drive(mgr, "R5", [LI, LI], monkeypatch)
    assert len(audits) == 2
    for kind, payload in audits:
        assert kind == "mace_pt_mark_reject"
        assert payload["reason"] == "leg_inversion"
        assert payload["mark"] == round(MARK, 4)
        assert payload["symbol"] == "XLE"
        assert "detail" in payload and payload["detail"]
