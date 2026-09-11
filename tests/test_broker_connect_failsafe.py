"""Unit tests for the broker-connect fail-safe (Option B, 2026-09-11).

Shared DataExecAgent behavior:
  - a LIVE division (broker.paper is False) whose connect() fails is NEVER swapped to
    paper -- the SAME live object is held, marked degraded, and reconnected on a
    background pass (auto-heal for captured refs + re-resolvers);
  - a PAPER-intended division (paper=True, e.g. fidelity) STILL falls back to paper;
  - the shared RH login latch is reset once per retry pass, then one connect
    re-authenticates the family;
  - the missed-exit alert dedups per degraded episode and re-opens after recovery.

Pure unit tests: fake broker/logger/notifier, no network, no DB, engine untouched.
"""
import asyncio

from trading_corp.agents.data_exec import DataExecAgent
from trading_corp.brokers.paper import PaperBroker


class _FakeLogger:
    def __init__(self):
        self.events = []

    def log_event(self, actor, kind, payload):
        self.events.append({"actor": actor, "kind": kind, "payload": payload})

    def kinds(self):
        return [e["kind"] for e in self.events]


class _FakeNotifier:
    def __init__(self):
        self.pushes = []

    async def push(self, text, *, audit_path="other", audit_context=None):
        self.pushes.append({"text": text, "audit_path": audit_path, "ctx": audit_context})
        return True


class _FakeBroker:
    """Scripted broker. `paper` toggles live/paper. `script` is a list of 'ok'/'raise'
    consumed per connect() (the last entry repeats)."""

    def __init__(self, name, paper, script):
        self.name = name
        self.paper = paper
        self._script = list(script) or ["ok"]
        self.connect_calls = 0
        self.reset_calls = 0

    async def connect(self):
        beh = self._script[min(self.connect_calls, len(self._script) - 1)]
        self.connect_calls += 1
        if beh == "raise":
            raise RuntimeError(f"{self.name} connect failed")


def _mk(name, paper, script, has_reset=False):
    b = _FakeBroker(name, paper, script)
    if has_reset:
        def _reset(_b=b):
            _b.reset_calls += 1
        b.reset_shared_login = _reset   # mirrors RobinhoodBroker.reset_shared_login
    return b


def _agent():
    a = DataExecAgent(_FakeLogger())
    a.safety_notifier = _FakeNotifier()
    return a


def test_live_broker_never_falls_back_to_paper():
    async def go():
        a = _agent()
        live = _mk("robinhood", paper=False, script=["raise"])
        a.brokers["robinhood_mace"] = live
        await a.connect_all()
        assert a.brokers["robinhood_mace"] is live               # SAME object, not swapped
        assert not isinstance(a.brokers["robinhood_mace"], PaperBroker)
        assert a.is_degraded("robinhood_mace")
        assert "broker_live_connect_degraded" in a.logger.kinds()
        assert "broker_fallback_to_paper" not in a.logger.kinds()
    asyncio.run(go())


def test_paper_broker_still_falls_back():
    async def go():
        a = _agent()
        a.brokers["fidelity_joint"] = _mk("paper-exec", paper=True, script=["raise"])
        await a.connect_all()
        assert isinstance(a.brokers["fidelity_joint"], PaperBroker)   # swapped (unchanged)
        assert not a.is_degraded("fidelity_joint")
        assert "broker_fallback_to_paper" in a.logger.kinds()
        assert "broker_live_connect_degraded" not in a.logger.kinds()
    asyncio.run(go())


def test_healthy_boot_no_degraded_no_alerts():
    async def go():
        a = _agent()
        a.brokers["robinhood_mace"] = _mk("robinhood", paper=False, script=["ok"])
        a.brokers["fidelity_joint"] = _mk("paper-exec", paper=True, script=["ok"])
        await a.connect_all()
        assert not a.is_degraded("robinhood_mace")
        assert a.logger.kinds() == []
        assert a.safety_notifier.pushes == []
    asyncio.run(go())


def test_retry_reconnects_same_object_and_clears():
    async def go():
        a = _agent()
        live = _mk("robinhood", paper=False, script=["raise", "ok"], has_reset=True)
        a.brokers["robinhood_pmcc"] = live
        await a.connect_all()
        assert a.is_degraded("robinhood_pmcc")
        await a._retry_degraded_once()
        assert not a.is_degraded("robinhood_pmcc")
        assert a.brokers["robinhood_pmcc"] is live       # healed in place, same object
        assert live.reset_calls == 1                     # shared-login reset before reconnect
        kinds = a.logger.kinds()
        assert "broker_live_connect_recovered" in kinds
        texts = " ".join(p["text"] for p in a.safety_notifier.pushes)
        assert "DISCONNECTED" in texts and "RECONNECTED" in texts
    asyncio.run(go())


def test_retry_stays_degraded_when_still_down():
    async def go():
        a = _agent()
        live = _mk("robinhood", paper=False, script=["raise", "raise"], has_reset=True)
        a.brokers["robinhood_pead"] = live
        await a.connect_all()
        await a._retry_degraded_once()
        assert a.is_degraded("robinhood_pead")
        assert "broker_connect_retry_failed" in a.logger.kinds()
        dis = [p for p in a.safety_notifier.pushes if "DISCONNECTED" in p["text"]]
        assert len(dis) == 1                              # dedup: one disconnect alert
        assert not any("RECONNECTED" in p["text"] for p in a.safety_notifier.pushes)
    asyncio.run(go())


def test_shared_login_reset_once_then_all_recover():
    async def go():
        a = _agent()
        slugs = ("robinhood_mace", "robinhood_pmcc", "robinhood_pead")
        for slug in slugs:
            a.brokers[slug] = _mk("robinhood", paper=False, script=["raise", "ok"], has_reset=True)
        await a.connect_all()
        assert all(a.is_degraded(s) for s in slugs)
        await a._retry_degraded_once()
        assert not any(a.is_degraded(s) for s in slugs)   # one pass heals the family
    asyncio.run(go())


def test_note_missed_exit_once_dedups_and_reopens_after_recovery():
    async def go():
        a = _agent()
        await a.note_missed_exit_once("robinhood_pead", "exit", "x")
        await a.note_missed_exit_once("robinhood_pead", "exit", "x")
        missed = [e for e in a.logger.events if e["kind"] == "broker_missed_exit"]
        assert len(missed) == 1                           # deduped within an episode
        a._missed_exit_alerted.discard("robinhood_pead")  # recovery clears the latch
        await a.note_missed_exit_once("robinhood_pead", "exit", "x")
        missed = [e for e in a.logger.events if e["kind"] == "broker_missed_exit"]
        assert len(missed) == 2                           # fires again next episode
    asyncio.run(go())


def test_reset_shared_login_clears_latch():
    from trading_corp.brokers import robinhood as rh
    rh._LOGIN_DONE = True
    rh.RobinhoodBroker.reset_shared_login()
    assert rh._LOGIN_DONE is False
