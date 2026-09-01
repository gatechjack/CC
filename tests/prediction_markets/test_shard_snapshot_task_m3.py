"""M3 WRITER (engine-side, 2026-09-01). Offline, fixture-free/self-runnable. Proves per-account key resolution
(secret_ref -> the right keypair, so Karen is read with HER keys), and the end-to-end write path: a fake venue
/portfolio/balance read -> parse -> write_snapshot -> read_latest gives back the PER-SHARD split. No broker, no
network -- a duck-typed fake client stands in (the standing lens: exercise the real path, here parse->write->read)."""
import asyncio
import os
import tempfile
import types

from trading_corp.prediction_markets import db, shard_snapshot as ss, shard_snapshot_task as sst


def test_resolve_kalshi_keys_per_account():
    s = types.SimpleNamespace(kalshi_api_key_id="JID", kalshi_private_key_pem="JPEM",
                              kalshi_karen_api_key_id="KID", kalshi_karen_private_key_pem="KPEM")
    assert sst.resolve_kalshi_keys("kalshi_karen", s) == ("KID", "KPEM")   # Karen -> her isolated keypair
    assert sst.resolve_kalshi_keys("KALSHI", s) == ("JID", "JPEM")         # anything else -> the shared keypair
    assert sst.resolve_kalshi_keys(None, s) == ("JID", "JPEM")
    s2 = types.SimpleNamespace(kalshi_api_key_id="JID", kalshi_private_key_pem="JPEM")   # no karen fields present
    assert sst.resolve_kalshi_keys("kalshi_karen", s2) == (None, None)     # tolerant -> None -> caller skips


class _FakeClient:
    """Duck-typed venue client: `.get(path)` returns a balance response dict (sync; fetch_shard_balances handles
    the non-awaitable). Raises if told to (to exercise the loop's fail-soft)."""
    def __init__(self, resp, boom=False):
        self._resp, self._boom = resp, boom

    def get(self, path):
        if self._boom:
            raise RuntimeError("venue read failed")
        return self._resp


def _db():
    d = tempfile.mkdtemp(); p = os.path.join(d, "pm.db"); os.environ["PM_DB_PATH"] = p; db.init_db(p); return p


def test_snapshot_once_end_to_end_write_read():
    p = _db()
    resp = {"balance_dollars": "473.60",
            "balance_breakdown": [{"exchange_index": 0, "balance": "0.0081"},
                                  {"exchange_index": 3, "balance": "473.5897"}]}
    sb = asyncio.run(sst.snapshot_once(p, "kalshi_jack", _FakeClient(resp), now_ts=1788240000))
    assert sb.by_shard == {0: 0.0081, 3: 473.5897}                        # parsed the split from the fake venue read
    with db.connect(p) as c:
        v = ss.read_latest(c, "kalshi_jack", now_ts=1788240000)
    assert v.by_shard == {0: 0.0081, 3: 473.5897} and abs(v.total_dollars - 473.60) < 1e-9   # persisted + re-read


def test_snapshot_once_raises_on_bad_read_so_loop_fail_softs():
    p = _db()
    import pytest
    with pytest.raises(Exception):
        asyncio.run(sst.snapshot_once(p, "kalshi_jack", _FakeClient(None, boom=True)))
    # (the loop wraps snapshot_once in try/except -> a bad read skips THAT account this cycle, never crashes)


if __name__ == "__main__":
    # self-run without pytest for the raises-case
    import traceback
    fns = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    npass = 0
    for k, f in fns:
        try:
            if k == "test_snapshot_once_raises_on_bad_read_so_loop_fail_softs":
                p = _db(); raised = False
                try:
                    asyncio.run(sst.snapshot_once(p, "kalshi_jack", _FakeClient(None, boom=True)))
                except Exception:
                    raised = True
                assert raised, "expected snapshot_once to raise on a bad read"
            else:
                f()
            print("PASS", k); npass += 1
        except Exception:
            traceback.print_exc(); print("FAIL", k)
    print("ALL %d PASS" % npass if npass == len(fns) else "SOME FAILED")
