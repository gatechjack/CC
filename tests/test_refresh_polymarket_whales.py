"""Tests for the realized-basis copy-roster refresh (option (c) Phase 1).

Network-free: a fake PolymarketDataAPIClient (async context manager) serves
canned leaderboard / activity / resolution fixtures; a temp sqlite DB backs
the pinned-merge + audit-cache paths.

Load-bearing assertions:
  - the REDEEM-grounded realized compute drives selection, and realized
    metrics ride along on each `selected_whales` record (additive);
  - the **pinned-whales merge survives unchanged** — a dashboard-pinned wallet
    that the algorithm did NOT select is still present afterward as
    `source="pinned_promotion"` (the hard-stop invariant);
  - the inflation gate drops a whale the naive scorer WOULD have kept, and it
    shows up in the `gated_out_inflation` list with its ratio (D4);
  - `--dry-run` writes NOTHING to `selected_whales` (read-only), and the
    dry-run comparison attributes each mover to cause via the three score
    columns (D2b).
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from trading_corp.data.polymarket_data_api_client import ActivityRow, LeaderboardEntry
from trading_corp.data.polymarket_whale_audit import build_audit_report
from trading_corp.data.whale_screening import _fetch_wallet_activity_windowed
from trading_corp.persistence.db import init_db, load_agent_state, set_agent_state
from trading_corp.scripts import refresh_polymarket_whales as refresh_mod
from trading_corp.scripts.refresh_polymarket_whales import refresh_polymarket_selection

REDEEM_SENTINEL = 999


@pytest.fixture
def db_url(tmp_path):
    db_file = tmp_path / "test_refresh.db"
    url = f"sqlite:///{db_file}"
    init_db(url)
    return url


# ── fixtures builders ────────────────────────────────────────────────────


def _act(
    ts: int, cid: str, *, wallet: str, side: str = "BUY", oi: int = 0,
    price: float = 0.5, size: float = 100.0, type_: str = "TRADE",
) -> ActivityRow:
    return ActivityRow(
        proxy_wallet=wallet, timestamp=ts, condition_id=cid, type=type_,
        size=size, usdc_size=size * price, transaction_hash=f"0xh{cid}{ts}",
        price=price, asset="", side=side, outcome_index=oi,
        title=f"market {cid}", slug=cid, event_slug="ev",
        outcome="Yes" if oi == 0 else "No", name="whale",
    )


def _redeem(ts: int, cid: str, *, wallet: str, size: float) -> ActivityRow:
    return ActivityRow(
        proxy_wallet=wallet, timestamp=ts, condition_id=cid, type="REDEEM",
        size=size, usdc_size=size, transaction_hash=f"0xr{cid}{ts}", price=0.0,
        asset="", side="", outcome_index=REDEEM_SENTINEL, title="", slug=cid,
        event_slug="ev", outcome="", name="whale",
    )


def _lb(wallet: str, *, rank: int, user_name: str, vol: float = 1000.0, pnl: float = 100.0) -> LeaderboardEntry:
    return LeaderboardEntry(
        rank=rank, proxy_wallet=wallet, user_name=user_name, x_username="",
        verified_badge=False, vol=vol, pnl=pnl, profile_image="",
    )


def _resolved(winning_outcome_index: int = 0) -> dict[str, Any]:
    return {"status": "resolved", "winning_outcome_index": winning_outcome_index}


def _whale_a_rows() -> list[ActivityRow]:
    """Good whale: 4 winning CLEAN holds (+$50 each) + 1 loss (−$30).
    n_resolved=5, n_winning=4, realized +$170, inflation ~0."""
    rows: list[ActivityRow] = []
    ts = 1_700_000_000
    for i in range(1, 5):
        cid = f"cid_a{i}"
        rows.append(_act(ts + i, cid, wallet="0xa", oi=0, price=0.5, size=100.0))
        rows.append(_redeem(ts + i + 100, cid, wallet="0xa", size=100.0))
    rows.append(_act(ts + 5, "cid_a5", wallet="0xa", oi=1, price=0.3, size=100.0))  # loss
    return rows


def _whale_b_rows() -> list[ActivityRow]:
    """Inflated whale: 4 winning decisions each ~95% round-tripped. Naive
    held-to-resolution PnL looks huge (+$100 each) but realized is only +$5
    each → aggregate inflation_ratio ~0.95 → gated out."""
    rows: list[ActivityRow] = []
    ts = 1_700_000_000
    for i in range(1, 5):
        cid = f"cid_b{i}"
        rows.append(_act(ts + i, cid, wallet="0xb", oi=0, price=0.5, size=200.0))
        rows.append(_act(ts + i + 50, cid, wallet="0xb", oi=0, side="SELL", price=0.5, size=190.0))
        rows.append(_redeem(ts + i + 100, cid, wallet="0xb", size=10.0))
    return rows


class _FakeClient:
    def __init__(self, lb, activity, resolutions):
        self._lb = lb
        self._act = activity
        self._res = resolutions

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return None

    async def fetch_leaderboard(self, *, category=None, limit=100, offset=0):
        rows = list(self._lb.get(category, []))
        return rows[offset:offset + limit]

    async def fetch_activity(self, wallet, *, limit=200, offset=0):
        # Offset-paginated so the windowed walk terminates (partial/empty page).
        rows = self._act.get(wallet, [])
        return list(rows[offset:offset + limit])

    async def fetch_market_resolutions(self, condition_ids, chunk_size=50):
        return {cid: self._res.get(cid, {"status": "not_found"}) for cid in condition_ids}


def _build_client() -> _FakeClient:
    a_entry = _lb("0xa", rank=1, user_name="GoodWhale")
    b_entry = _lb("0xb", rank=2, user_name="InflatedWhale")
    lb = {"Politics": [a_entry, b_entry], None: [a_entry, b_entry]}
    activity = {"0xa": _whale_a_rows(), "0xb": _whale_b_rows()}
    resolutions: dict[str, dict[str, Any]] = {}
    for i in range(1, 5):
        resolutions[f"cid_a{i}"] = _resolved(0)
        resolutions[f"cid_b{i}"] = _resolved(0)
    resolutions["cid_a5"] = _resolved(0)  # A bought oi=1 → loses
    return _FakeClient(lb, activity, resolutions)


async def _run(db_url, *, dry_run: bool, algo_select: bool = False) -> dict[str, Any]:
    fake = _build_client()
    with patch.object(refresh_mod, "PolymarketDataAPIClient", lambda: fake):
        return await refresh_polymarket_selection(
            db_url=db_url, categories=("Politics",), candidates_per_category=20,
            min_resolved=3, inflation_threshold=0.5, top_per_category=2,
            top_global=2, activity_limit=500, dry_run=dry_run, algo_select=algo_select,
        )


# ── selection on the realized basis + additive metrics ───────────────────


async def test_realized_compute_drives_selection_with_metrics(db_url):
    summary = await _run(db_url, dry_run=False)
    by_wallet = {r["wallet"]: r for r in summary["selected_whales"]}
    assert "0xa" in by_wallet  # good whale selected
    assert "0xb" not in by_wallet  # inflated whale gated out
    a = by_wallet["0xa"]
    assert a["n_resolved_decisions"] == 5
    assert a["n_winning_decisions"] == 4
    assert a["decision_win_rate"] == pytest.approx(0.8)
    assert a["realized_pnl_usdc"] == pytest.approx(170.0)
    assert a["realized_roi"] == pytest.approx(170.0 / 230.0, rel=1e-3)
    assert a["pnl_inflation_ratio"] == pytest.approx(0.0, abs=1e-6)
    # the consumed keys are still present (additive only)
    for k in ("wallet", "user_name", "category", "rank", "composite_score"):
        assert k in a
    # 9-row fixture exhausts well within the page ceiling → not truncated
    assert a["window_truncated"] is False


async def test_inflation_gate_lists_dropped_whale(db_url):
    summary = await _run(db_url, dry_run=False)
    gated = {g["wallet"]: g for g in summary["gated_out_inflation"]}
    assert "0xb" in gated
    assert gated["0xb"]["pnl_inflation_ratio"] > 0.5
    assert gated["0xb"]["pnl_inflation_ratio"] == pytest.approx(0.95, rel=1e-2)


# ── pinned-merge survival (HARD STOP invariant) ──────────────────────────


async def test_pinned_whale_survives_algo_select_refresh(db_url):
    """In algo-select mode, a dashboard-pinned wallet the algorithm did NOT
    select must remain in the WRITTEN roster as source='pinned_promotion'
    (pinned merge unchanged), alongside the algo picks."""
    set_agent_state(
        "polymarket_copy_trader", "pinned_whales",
        [{"wallet": "0xc", "user_name": "PinnedWhale", "category": "Politics"}],
        db_url=db_url,
    )
    summary = await _run(db_url, dry_run=False, algo_select=True)
    assert summary["pinned_merged"] == 1
    written = {r["wallet"]: r for r in summary["written_selected_whales"]}
    assert "0xc" in written  # pin survived the algo rebuild
    assert written["0xc"]["source"] == "pinned_promotion"
    assert written["0xc"]["rank"] is None
    assert written["0xc"]["composite_score"] is None
    assert "0xa" in written  # algo pick also written in algo-select mode
    loaded = load_agent_state("polymarket_copy_trader", "selected_whales", db_url=db_url)
    persisted = {r["wallet"] for r in loaded[0]}
    assert "0xc" in persisted and "0xa" in persisted


async def test_pins_only_writes_only_pins(db_url):
    """DEFAULT (pins-only): the DB roster contains ONLY pinned whales; the
    algorithm's picks appear in the report ranking but are NOT auto-written."""
    set_agent_state(
        "polymarket_copy_trader", "pinned_whales",
        [{"wallet": "0xc", "user_name": "PinnedWhale", "category": "Politics"}],
        db_url=db_url,
    )
    summary = await _run(db_url, dry_run=False)  # default = pins-only
    assert summary["write_mode"] == "pins_only"
    assert summary["pinned_merged"] == 1
    loaded = load_agent_state("polymarket_copy_trader", "selected_whales", db_url=db_url)
    persisted = {r["wallet"] for r in loaded[0]}
    assert persisted == {"0xc"}  # ONLY the pin written — no algo auto-select
    # the algo ranking is still in the report for operator review
    ranking = {r["wallet"] for r in summary["selected_whales"]}
    assert "0xa" in ranking


async def test_algo_select_writes_algo_plus_pins(db_url):
    set_agent_state(
        "polymarket_copy_trader", "pinned_whales",
        [{"wallet": "0xc", "user_name": "PinnedWhale", "category": "Politics"}],
        db_url=db_url,
    )
    summary = await _run(db_url, dry_run=False, algo_select=True)
    assert summary["write_mode"] == "algo_select"
    loaded = load_agent_state("polymarket_copy_trader", "selected_whales", db_url=db_url)
    persisted = {r["wallet"] for r in loaded[0]}
    assert "0xa" in persisted  # algo pick written (legacy behavior, now opt-in)
    assert "0xc" in persisted  # pin merged


# ── dry-run: no write + cause attribution ────────────────────────────────


async def test_dry_run_writes_nothing(db_url):
    summary = await _run(db_url, dry_run=True)
    # roster computed in-memory (incl. pinned merge) but NOT persisted
    assert any(r["wallet"] == "0xa" for r in summary["selected_whales"])
    assert load_agent_state("polymarket_copy_trader", "selected_whales", db_url=db_url) is None
    assert load_agent_state("polymarket_copy_trader", "selection_metadata", db_url=db_url) is None


async def test_dry_run_cause_attribution(db_url):
    summary = await _run(db_url, dry_run=True)
    cmp = summary["dry_run_comparison"]
    # B is selected by the naive scorer (inflated PnL looks great) but dropped
    # on the realized basis → appears in `dropped`.
    assert "0xb" in cmp["dropped"]
    assert len(cmp["movers"]) >= 1
    for m in cmp["movers"]:
        for k in ("s_naive_tw", "s_naive_plain", "s_realized",
                  "delta_timeweight", "delta_realized"):
            assert k in m
    b_mover = next(m for m in cmp["movers"] if m["wallet"] == "0xb")
    assert b_mover["excluded_realized"] is True
    assert "inflation" in (b_mover["exclusion_reason"] or "")


async def test_algo_select_skips_comparison_pins_only_has_it(db_url):
    # pins-only (default) is a review mode → produces the cause-attribution diff
    s_pins = await _run(db_url, dry_run=False)
    assert "dry_run_comparison" in s_pins
    assert "gated_out_inflation" in s_pins
    # algo-select is the commit mode → skips the extra naive comparison
    s_algo = await _run(db_url, dry_run=False, algo_select=True)
    assert "dry_run_comparison" not in s_algo
    assert "gated_out_inflation" in s_algo  # gated-out computed in all modes


# ── paginated activity window reconstructs a straddling decision (Commit 3) ──


def _whale_straddle_rows() -> list[ActivityRow]:
    """12 rows for 0xs. With activity_limit=5 the single decision cid_s
    (4 BUYs of 50@0.5 = $100 cost + REDEEM $200) STRADDLES the page boundary:
    BUYs at indices 0,1 (page 1) and 5,6 (page 2); REDEEM at index 7 (page 2).
    A single page sees only 2 BUYs and misses the REDEEM → corrupted realized;
    the full paginated walk reconstructs cost basis $100 → realized +$100."""
    w = "0xs"
    return [
        _act(2000, "cid_s", wallet=w, oi=0, price=0.5, size=50.0),    # 0  BUY cid_s
        _act(1999, "cid_s", wallet=w, oi=0, price=0.5, size=50.0),    # 1  BUY cid_s
        _act(1998, "cid_pad1", wallet=w, oi=0, price=0.5, size=10.0), # 2  filler (unresolved)
        _act(1997, "cid_pad2", wallet=w, oi=0, price=0.5, size=10.0), # 3
        _act(1996, "cid_pad3", wallet=w, oi=0, price=0.5, size=10.0), # 4
        _act(1995, "cid_s", wallet=w, oi=0, price=0.5, size=50.0),    # 5  BUY cid_s (page 2)
        _act(1994, "cid_s", wallet=w, oi=0, price=0.5, size=50.0),    # 6  BUY cid_s
        _redeem(1993, "cid_s", wallet=w, size=200.0),                 # 7  REDEEM cid_s (page 2)
        _act(1992, "cid_pad4", wallet=w, oi=0, price=0.5, size=10.0), # 8
        _act(1991, "cid_pad5", wallet=w, oi=0, price=0.5, size=10.0), # 9
        _act(1990, "cid_pad6", wallet=w, oi=0, price=0.5, size=10.0), # 10
        _act(1989, "cid_pad7", wallet=w, oi=0, price=0.5, size=10.0), # 11
    ]


def _whale_truncating_rows() -> list[ActivityRow]:
    """6 rows for 0xt. With activity_limit=2 / max_pages=2 the walk fetches only
    the first 4 rows and stops at the page ceiling (max_pages_hit) → the record
    must be flagged window_truncated. The captured rows still form a valid
    winning decision cid_t (2 BUYs $50 cost + REDEEM $100 → realized +$50)."""
    w = "0xt"
    return [
        _act(2000, "cid_t", wallet=w, oi=0, price=0.5, size=50.0),     # 0 BUY
        _redeem(1999, "cid_t", wallet=w, size=100.0),                  # 1 REDEEM
        _act(1998, "cid_t", wallet=w, oi=0, price=0.5, size=50.0),     # 2 BUY
        _act(1997, "cid_pad1", wallet=w, oi=0, price=0.5, size=10.0),  # 3
        _act(1996, "cid_pad2", wallet=w, oi=0, price=0.5, size=10.0),  # 4 (beyond ceiling)
        _act(1995, "cid_pad3", wallet=w, oi=0, price=0.5, size=10.0),  # 5
    ]


async def test_truncated_whale_excluded_from_selection_and_unrankable(db_url):
    """A window_truncated whale is hard-gated out of algorithmic selection and
    surfaced in the unrankable section (not silently dropped)."""
    t_entry = _lb("0xt", rank=1, user_name="truncwhale")
    fake = _FakeClient(
        {"Politics": [t_entry], None: [t_entry]},
        {"0xt": _whale_truncating_rows()},
        {"cid_t": _resolved(0)},
    )
    with patch.object(refresh_mod, "PolymarketDataAPIClient", lambda: fake):
        summary = await refresh_polymarket_selection(
            db_url=db_url, categories=("Politics",), candidates_per_category=20,
            min_resolved=1, inflation_threshold=0.5, top_per_category=2,
            top_global=2, activity_limit=2, max_pages=2, dry_run=True,
        )
    sel = {r["wallet"] for r in summary["selected_whales"]}
    assert "0xt" not in sel  # truncated → excluded from algo selection
    assert summary["filters"]["window_truncated"] >= 1
    unr = {u["wallet"]: u for u in summary["unrankable_truncated"]}
    assert "0xt" in unr  # surfaced with partial numbers
    assert unr["0xt"]["window_truncated"] is True
    assert unr["0xt"]["n_resolved_decisions_partial"] >= 1


async def test_truncated_pinned_whale_survives_via_pin(db_url):
    """Pins are operator decisions and override the truncation gate: a pinned
    truncated whale survives the rebuild (still surfaced as unrankable)."""
    set_agent_state(
        "polymarket_copy_trader", "pinned_whales",
        [{"wallet": "0xt", "user_name": "truncwhale", "category": "Politics"}],
        db_url=db_url,
    )
    t_entry = _lb("0xt", rank=1, user_name="truncwhale")
    fake = _FakeClient(
        {"Politics": [t_entry], None: [t_entry]},
        {"0xt": _whale_truncating_rows()},
        {"cid_t": _resolved(0)},
    )
    with patch.object(refresh_mod, "PolymarketDataAPIClient", lambda: fake):
        summary = await refresh_polymarket_selection(
            db_url=db_url, categories=("Politics",), candidates_per_category=20,
            min_resolved=1, inflation_threshold=0.5, top_per_category=2,
            top_global=2, activity_limit=2, max_pages=2, dry_run=False,
        )
    loaded = load_agent_state("polymarket_copy_trader", "selected_whales", db_url=db_url)
    assert loaded is not None
    written = {r["wallet"]: r for r in loaded[0]}
    assert "0xt" in written  # pin overrides the truncation gate
    assert written["0xt"]["source"] == "pinned_promotion"
    assert any(u["wallet"] == "0xt" for u in summary["unrankable_truncated"])


async def test_truncated_whale_cannot_enter_algo_selected_roster(db_url):
    """CONTAINMENT GUARANTEE: a window_truncated whale CANNOT enter the
    algorithmically-selected (written) roster — even in --algo-select mode and
    even with a selectable score. The SAME whale with a COMPLETE window IS
    written, proving the exclusion is the truncation gate, not a low score
    (cid_t reconciles to +$50 realized identically in both runs)."""
    t_entry = _lb("0xt", rank=1, user_name="truncwhale")
    rows = _whale_truncating_rows()

    # (1) TRUNCATED window (limit=2/max_pages=2 → page ceiling) + --algo-select.
    fake_t = _FakeClient(
        {"Politics": [t_entry], None: [t_entry]}, {"0xt": rows}, {"cid_t": _resolved(0)},
    )
    with patch.object(refresh_mod, "PolymarketDataAPIClient", lambda: fake_t):
        s_trunc = await refresh_polymarket_selection(
            db_url=db_url, categories=("Politics",), candidates_per_category=20,
            min_resolved=1, inflation_threshold=0.5, top_per_category=2, top_global=2,
            activity_limit=2, max_pages=2, dry_run=False, algo_select=True,
        )
    written_t = {r["wallet"] for r in s_trunc["written_selected_whales"]}
    assert "0xt" not in written_t  # gated out of the algo-selected write
    assert "0xt" not in {r["wallet"] for r in s_trunc["selected_whales"]}  # and the ranking
    assert any(u["wallet"] == "0xt" for u in s_trunc["unrankable_truncated"])  # surfaced

    # (2) SAME whale, COMPLETE window (limit=500/max_pages=10 → exhausts) → selectable.
    fake_c = _FakeClient(
        {"Politics": [t_entry], None: [t_entry]}, {"0xt": rows}, {"cid_t": _resolved(0)},
    )
    with patch.object(refresh_mod, "PolymarketDataAPIClient", lambda: fake_c):
        s_full = await refresh_polymarket_selection(
            db_url=db_url, categories=("Politics",), candidates_per_category=20,
            min_resolved=1, inflation_threshold=0.5, top_per_category=2, top_global=2,
            activity_limit=500, max_pages=10, dry_run=False, algo_select=True,
        )
    written_c = {r["wallet"] for r in s_full["written_selected_whales"]}
    assert "0xt" in written_c  # complete window + identical score → IS selected
    assert not any(u["wallet"] == "0xt" for u in s_full["unrankable_truncated"])


async def test_windowed_walk_reconstructs_straddling_decision():
    rows = _whale_straddle_rows()
    fake = _FakeClient({}, {"0xs": rows}, {})
    resolutions = {"cid_s": _resolved(0)}

    # FULL window: activity_limit=5, up to 10 pages → all 12 rows fetched.
    full, _pages, reason = await _fetch_wallet_activity_windowed(
        fake, "0xs", activity_limit=5, max_pages=10, target_buy_rows=150,
    )
    assert reason == "exhausted"
    assert len(full) == 12
    rpt_full = build_audit_report(
        leaderboard_entry=None, activity_rows=full, resolutions=resolutions,
        proxy_wallet="0xs",
    )
    assert rpt_full.n_resolved_decisions == 1
    assert rpt_full.n_winning_decisions == 1
    assert rpt_full.total_buy_usdc_resolved == pytest.approx(100.0)  # all 4 BUYs
    assert rpt_full.realized_pnl.realized_pnl_usdc == pytest.approx(100.0)  # 200 - 100
    assert rpt_full.realized_pnl.pnl_inflation_ratio == pytest.approx(0.0, abs=1e-6)

    # TRUNCATED to page 1 only (max_pages=1): 2 of 4 BUYs, REDEEM unseen →
    # cost basis halved + redemption missed → corrupted realized. This is
    # exactly the failure the pagination fix closes.
    trunc, _p2, _r2 = await _fetch_wallet_activity_windowed(
        fake, "0xs", activity_limit=5, max_pages=1, target_buy_rows=150,
    )
    assert len(trunc) == 5
    rpt_trunc = build_audit_report(
        leaderboard_entry=None, activity_rows=trunc, resolutions=resolutions,
        proxy_wallet="0xs",
    )
    assert rpt_trunc.total_buy_usdc_resolved == pytest.approx(50.0)  # only 2 BUYs
    assert rpt_trunc.realized_pnl.realized_pnl_usdc == pytest.approx(-50.0)
    assert rpt_trunc.realized_pnl.realized_pnl_usdc != pytest.approx(100.0)
