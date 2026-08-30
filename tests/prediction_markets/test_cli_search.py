"""Stage 4 SEARCH -- the `pm_cli search` ENTRY-POINT (the wiring rung). Offline; NO engine, NO network
(injected async-CM client + injected clock). These tests exist because of the lesson that NAMED this rung:
"A FEATURE IS NOT SHIPPED UNTIL SOMETHING CAN INVOKE IT." The search MODULE was built + tested across R2/R3
while the CLI entry-point fell between two rungs -- `pm_cli search` errored `invalid choice`. So these tests
drive the REAL entry point `pm.main(["search", ...])` (parser + is_async dispatch + the composed run), NOT
`_cmd_search` in isolation -- a box-scratch that tests a function proves the function, not that anything
invokes it. Coverage:
  - the subparser EXISTS + dispatches _cmd_search (the anti-"invalid choice" guard -- the exact gap closed);
  - --dry-run PREVIEWS (discovery + new-vs-complete split) with NO backfill + NO write (1 leaderboard call);
  - a real run backfills a first-sight whale -> rollup -> /positions recency pull -> writes a status='candidate'
    row, printing per-wallet progress (a 20-40 min run must not look like a hang);
  - Ruling 1 THROUGH the CLI: a second run does NOT re-pull an already-complete whale, and the candidate
    persists (idempotent, 0 new written).
Spec: reports/prediction_markets/STAGE4_SEARCH_PLAN_2026-08-29.md sec 8A/9A/9D/10; the wiring-rung ledger.
"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

from trading_corp.prediction_markets import db

_CLI_PATH = Path(__file__).resolve().parents[2] / "trading_corp" / "scripts" / "pm_cli.py"
NOW = 1_700_000_000


def _pm_cli():
    """Load pm_cli by file path (it lives outside the package), a FRESH module per test -> monkeypatch of
    its module globals (_client, _now) is isolated to that test."""
    spec = importlib.util.spec_from_file_location("pm_cli_search_under_test", _CLI_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _cp(wallet, i, *, ts=NOW):
    """A ClosedPositionRow-shaped fixture (attr access, as ingest.cp_to_record reads). mlb-* slug -> tier-1,
    so _categorize never reaches fetch_events (fully offline). resolved_ts == timestamp (ingest.py) -> ts."""
    return SimpleNamespace(
        proxy_wallet=wallet, condition_id="c%d" % i, slug="mlb-g%d" % i, event_slug="mlb-g%d" % i,
        title="MLB %d" % i, outcome="Yes", outcome_index=0, avg_price=0.5, total_bought=100.0,
        realized_pnl=10.0, cur_price=1.0, end_date="2026-01-01", timestamp=ts)


def _open_pos(wallet, i=0):
    """A /positions-shaped OPEN holding; mlb-* slug -> category 'mlb' (the open-position recency proxy)."""
    return SimpleNamespace(condition_id="op%d" % i, slug="mlb-open%d" % i, event_slug="mlb-open%d" % i,
                           title="MLB open", outcome="Yes", outcome_index=0, size=10.0, avg_price=0.5,
                           initial_value=5.0, current_value=6.0, pnl=1.0)


class FakeSearchClient:
    """ONE injected client for the whole pipeline: leaderboard (discovery) + closed-positions (backfill) +
    positions (recency). An async context manager, exactly like PolymarketDataAPIClient, so `_client()` can
    be swapped for it. Records what was pulled so a test can prove Ruling 1 (a complete whale is NOT re-pulled)."""

    def __init__(self, *, leaderboard=(), closed=None, positions=None):
        self.leaderboard = list(leaderboard)                                # [(wallet, name)]
        self.closed = {k.lower(): list(v) for k, v in (closed or {}).items()}
        self.positions = {k.lower(): list(v) for k, v in (positions or {}).items()}
        self.pulled = []                                                    # wallets whose /closed-positions hit
        self.positions_calls = []                                           # wallets whose /positions hit

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def fetch_leaderboard(self, *, category, limit):
        return [SimpleNamespace(proxy_wallet=w, user_name=n) for w, n in self.leaderboard]

    async def fetch_closed_positions(self, wallet, *, limit=50, offset=0):
        w = wallet.lower()
        if offset == 0:
            self.pulled.append(w)
        return self.closed.get(w, [])[offset:offset + limit]

    async def fetch_positions(self, wallet, **kw):
        w = wallet.lower()
        self.positions_calls.append(w)
        return self.positions.get(w, [])


def _seed_db(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    return p


def _wire(monkeypatch, pm, client):
    monkeypatch.setattr(pm, "_client", lambda: client)   # injected async-CM client -> NO network
    monkeypatch.setattr(pm, "_now", lambda: NOW)          # injected clock -> deterministic recency


# ═══════════════ the subparser is WIRED + dispatches (the anti-"invalid choice" guard) ═══════════════

def test_search_subparser_wired_and_dispatches_cmd_search():
    pm = _pm_cli()
    a = pm.build_parser().parse_args(["search"])           # would raise SystemExit if 'search' were not a choice
    assert a.func is pm._cmd_search and a.is_async is True  # reachable AND async-dispatched by main()
    assert a.category == "Sports" and a.leaderboard_limit == 250 and a.dry_run is False
    b = pm.build_parser().parse_args(
        ["search", "--dry-run", "--category", "Politics", "--leaderboard-limit", "10"])
    assert b.dry_run is True and b.category == "Politics" and b.leaderboard_limit == 10


# ═══════════════ --dry-run: PREVIEW only -- no backfill, no run row, no candidate ═══════════════

def test_search_dry_run_previews_without_backfill_or_write(tmp_path, capsys, monkeypatch):
    p = _seed_db(tmp_path)
    with db.connect(p) as conn:
        conn.execute("INSERT INTO pm_whale (wallet, backfill_complete) VALUES ('0xdone', 1)")   # already complete
    pm = _pm_cli()
    client = FakeSearchClient(leaderboard=[("0xdone", "D"), ("0xnew", "N")],
                              closed={"0xnew": [_cp("0xnew", i) for i in range(12)]})
    _wire(monkeypatch, pm, client)
    assert pm.main(["--db", p, "search", "--dry-run"]) == 0        # invoked through the real parser + dispatch
    out = capsys.readouterr().out
    assert "DRY-RUN" in out and "discovered=2" in out
    assert "1 already complete" in out and "1 to backfill" in out  # the split is STATED before committing
    assert client.pulled == [] and client.positions_calls == []    # NOTHING pulled -- 1 leaderboard call only
    with db.connect(p) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pm_search_run").fetchone()[0] == 0        # no run opened
        assert conn.execute("SELECT COUNT(*) FROM pm_closed_position").fetchone()[0] == 0   # no backfill
        assert conn.execute("SELECT COUNT(*) FROM pm_watchlist WHERE status='candidate'").fetchone()[0] == 0


# ═══════════════ a real run: discover -> backfill -> rollup -> recency -> write candidate ═══════════════

def test_search_real_run_backfills_and_writes_candidate(tmp_path, capsys, monkeypatch):
    p = _seed_db(tmp_path)
    pm = _pm_cli()
    client = FakeSearchClient(
        leaderboard=[("0xwhale", "Whale")],
        closed={"0xwhale": [_cp("0xwhale", i) for i in range(12)]},   # 12 < 50 -> short page -> verdict complete
        positions={"0xwhale": [_open_pos("0xwhale")]})                # an open mlb holding -> the recency proxy
    _wire(monkeypatch, pm, client)
    assert pm.main(["--db", p, "search"]) == 0
    out = capsys.readouterr().out
    assert "0xwhale" in client.pulled and "0xwhale" in client.positions_calls
    assert "1/1]" in out                                              # per-wallet progress line ([  1/1] ...)
    assert "SEARCH DONE" in out and "candidates WRITTEN=1" in out
    with db.connect(p) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pm_closed_position WHERE wallet='0xwhale'").fetchone()[0] == 12
        assert conn.execute("SELECT backfill_complete FROM pm_whale WHERE wallet='0xwhale'").fetchone()[0] == 1
        run = conn.execute("SELECT status, finished_ts, n_discovered, n_candidates_written "
                           "FROM pm_search_run").fetchone()
        assert run["status"] == "ok" and run["finished_ts"] == NOW and run["n_discovered"] == 1
        assert run["n_candidates_written"] == 1
        cand = conn.execute("SELECT status, source, active FROM pm_watchlist "
                            "WHERE wallet='0xwhale' AND category='mlb'").fetchone()
        assert cand["status"] == "candidate" and cand["source"] == "search" and cand["active"] == 1
        # the open-position recency proxy row landed (category-scoped), and NOTHING was auto-promoted
        assert conn.execute("SELECT COUNT(*) FROM pm_open_position WHERE wallet='0xwhale' AND category='mlb'"
                            ).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM pm_watchlist WHERE status='pinned'").fetchone()[0] == 0


# ═══════════════ Ruling 1 through the CLI: a complete whale is NOT re-pulled; candidate persists ═══════════════

def test_search_second_run_skips_complete_whale_and_keeps_candidate(tmp_path, capsys, monkeypatch):
    p = _seed_db(tmp_path)
    pm = _pm_cli()
    client = FakeSearchClient(
        leaderboard=[("0xdone", "D")],
        closed={"0xdone": [_cp("0xdone", i) for i in range(12)]},
        positions={"0xdone": [_open_pos("0xdone")]})
    _wire(monkeypatch, pm, client)
    # run 1: first-sight -> backfilled complete + candidate written
    assert pm.main(["--db", p, "search"]) == 0
    assert "0xdone" in client.pulled
    with db.connect(p) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pm_watchlist WHERE wallet='0xdone' AND status='candidate'"
                            ).fetchone()[0] == 1
    client.pulled.clear()
    capsys.readouterr()
    # run 2: the whale is complete -> Ruling 1 says NEVER auto-re-pull; the candidate already exists (0 new)
    assert pm.main(["--db", p, "search"]) == 0
    out = capsys.readouterr().out
    assert client.pulled == []                          # NOT re-pulled -- read from DB
    assert "skip (already complete)" in out and "candidates WRITTEN=0" in out
    with db.connect(p) as conn:
        assert conn.execute("SELECT COUNT(*) FROM pm_watchlist WHERE wallet='0xdone' AND status='candidate'"
                            ).fetchone()[0] == 1        # exactly one, no duplicate
        assert conn.execute("SELECT COUNT(*) FROM pm_search_run").fetchone()[0] == 2   # both runs recorded
