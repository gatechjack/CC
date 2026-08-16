"""Boot index-refresh retry fix — unit proof (no network, no live restart).

Proves the two helpers behind the boot fast-retry:
  * `_pk_guarded_refresh` bounds one attempt by `timeout` and never raises — so a
    hung cold connection fails FAST instead of stalling ~2 min.
  * `_pk_boot_refresh_retry` retries on failure and stops on first success — so a
    single transient boot failure recovers in ~(timeout+backoff)s, NOT a full
    15-min steady cycle.
Steady-state behaviour (timeout=None, no retry) is exercised too, to show it is
unchanged.
"""
import asyncio
import time

from trading_corp.main import _pk_guarded_refresh, _pk_boot_refresh_retry

STEADY_CYCLE_SEC = 900.0  # index_refresh_sec — what the OLD behaviour waited a full cycle of


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class _Log:
    def __init__(self):
        self.info_msgs, self.warn_msgs = [], []
    def info(self, msg, *a):
        self.info_msgs.append(msg % a if a else msg)
    def warning(self, msg, *a):
        self.warn_msgs.append(msg % a if a else msg)


# ── _pk_guarded_refresh ──────────────────────────────────────────────────────
def test_guarded_refresh_success_returns_true_and_logs_count():
    async def do():   # healthy fetch -> game count
        return 913
    lg = _Log()
    assert _run(_pk_guarded_refresh(do, timeout=12.0, log=lg)) is True
    assert any("913 games" in m for m in lg.info_msgs)
    assert lg.warn_msgs == []


def test_guarded_refresh_timeout_bounds_a_hang_fast_no_raise():
    async def hang():          # mimics the cold-connection stall
        await asyncio.sleep(30.0)
        return 0
    lg = _Log()
    t0 = time.monotonic()
    ok = _run(_pk_guarded_refresh(hang, timeout=0.10, log=lg))   # 100ms bound
    elapsed = time.monotonic() - t0
    assert ok is False                      # failed, did NOT raise
    assert elapsed < 2.0                    # bounded fast, not 30s
    assert any("index refresh failed" in m for m in lg.warn_msgs)


def test_guarded_refresh_swallows_exception_returns_false():
    async def boom():
        raise ConnectionError("Server disconnected without sending a response.")
    lg = _Log()
    assert _run(_pk_guarded_refresh(boom, timeout=12.0, log=lg)) is False
    assert any("Server disconnected" in m for m in lg.warn_msgs)


def test_guarded_refresh_timeout_none_is_unbounded_steady_state_path():
    # steady-state passes no timeout -> unbounded; slow-but-OK fetch still succeeds
    async def slowish():
        await asyncio.sleep(0.05)
        return 41
    assert _run(_pk_guarded_refresh(slowish, timeout=None)) is True


# ── _pk_boot_refresh_retry ───────────────────────────────────────────────────
def _counting_refresh(outcomes):
    """refresh_fn returning outcomes[i] for call i; records timeouts seen."""
    calls = {"n": 0, "timeouts": []}
    async def refresh_fn(*, timeout=None):
        calls["timeouts"].append(timeout)
        i = calls["n"]
        calls["n"] += 1
        return outcomes[i] if i < len(outcomes) else outcomes[-1]
    return refresh_fn, calls


def test_boot_retry_succeeds_first_try_no_sleep():
    refresh_fn, calls = _counting_refresh([True])
    slept = []
    ok = _run(_pk_boot_refresh_retry(refresh_fn, tries=3, timeout=12.0,
                                     backoff=10.0, sleep_fn=lambda s: _noop(slept, s)))
    assert ok is True
    assert calls["n"] == 1 and slept == []           # one attempt, no backoff
    assert calls["timeouts"] == [12.0]               # timeout forwarded


def test_boot_retry_recovers_on_second_try_within_one_backoff():
    # THE fix: transient boot failure -> retry succeeds after ONE 10s backoff,
    # i.e. ~timeout+backoff, NOT a full 900s steady cycle.
    refresh_fn, calls = _counting_refresh([False, True])
    slept = []
    lg = _Log()
    ok = _run(_pk_boot_refresh_retry(refresh_fn, tries=3, timeout=12.0,
                                     backoff=10.0, sleep_fn=lambda s: _noop(slept, s), log=lg))
    assert ok is True
    assert calls["n"] == 2                            # failed once, then succeeded
    assert slept == [10.0]                            # exactly one backoff waited
    total_wait = sum(slept)
    assert total_wait == 10.0 and total_wait < STEADY_CYCLE_SEC   # ~30s not ~15min
    assert calls["timeouts"] == [12.0, 12.0]          # every attempt bounded


def test_boot_retry_all_fail_falls_through_after_tries():
    refresh_fn, calls = _counting_refresh([False, False, False])
    slept = []
    lg = _Log()
    ok = _run(_pk_boot_refresh_retry(refresh_fn, tries=3, timeout=12.0,
                                     backoff=10.0, sleep_fn=lambda s: _noop(slept, s), log=lg))
    assert ok is False
    assert calls["n"] == 3 and slept == [10.0, 10.0]  # tries attempts, tries-1 backoffs
    assert any("falling through to the steady refresh cycle" in m for m in lg.warn_msgs)


async def _noop(slept, s):
    slept.append(s)   # record the requested backoff without actually waiting
    return None
