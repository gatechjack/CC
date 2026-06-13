"""Byte-identical equivalence proof for the option (c) Phase 3 extraction.

Phase 3 moves the per-candidate `/activity` walk loop out of both rosters
(`refresh_polymarket_whales`, `seed_polymarket_watchlist_deep`) into the
shared `fetch_activity_window_for_candidates` helper, with the two caller
behavior deltas parameterized (`broad_catch`, `on_termination`).

This test is the surgical proof that the extraction changed NOTHING: it
embeds verbatim inline copies of each caller's PRE-refactor loop as a
golden reference, then drives the SAME fixture (covering every termination
path) through both the reference and the new helper and asserts the
outputs — `(activity_by_wallet, truncated_by_wallet, all_condition_ids)`
plus seed's `termination_reasons` + `with_activity` telemetry — are
identical. Combined with the unchanged caller-level suites
(`test_refresh_polymarket_whales`, `test_polymarket_watchlist_seed`),
which pin the exact downstream scores/ranks/flags/gating, this establishes
the byte-identical contract end-to-end.

Network-free.
"""
from __future__ import annotations

from typing import Any

from trading_corp.data.polymarket_data_api_client import (
    ActivityRow,
    PolymarketDataAPIError,
)
from trading_corp.data import whale_screening
from trading_corp.data.whale_screening import (
    _fetch_wallet_activity_windowed,
    fetch_activity_window_for_candidates,
)
from trading_corp.scripts import seed_polymarket_watchlist_deep as seed_mod


# ── fixtures ──────────────────────────────────────────────────────────────


def _act(
    ts: int, cid: str, *, side: str = "BUY", type_: str = "TRADE",
    oi: int = 0, price: float = 0.5, size: float = 100.0,
) -> ActivityRow:
    return ActivityRow(
        proxy_wallet="0xw", timestamp=ts, condition_id=cid, type=type_,
        size=size, usdc_size=size * price, transaction_hash=f"0x{cid}{ts}",
        price=price, asset="", side=side, outcome_index=oi,
        title=f"m {cid}", slug=cid, event_slug="ev",
        outcome="Yes" if oi == 0 else "No", name="whale",
    )


class _FakeClient:
    """Offset-paginated /activity fake with per-wallet error injection.

    `pages_by_wallet`: {wallet: list[page]} where page is list[ActivityRow].
    `raise_at`: {wallet: (offset_threshold, exc)} — raise `exc` once the walk
    requests an offset >= threshold (models a mid-walk transient failure).
    """

    def __init__(
        self,
        pages_by_wallet: dict[str, list[list[ActivityRow]]],
        raise_at: dict[str, tuple[int, Exception]] | None = None,
    ) -> None:
        self._pages = pages_by_wallet
        self._raise = raise_at or {}

    async def fetch_activity(
        self, wallet: str, *, limit: int, offset: int,
    ) -> list[ActivityRow]:
        if wallet in self._raise:
            threshold, exc = self._raise[wallet]
            if offset >= threshold:
                raise exc
        pages = self._pages.get(wallet, [])
        idx = offset // limit if limit else 0
        if 0 <= idx < len(pages):
            return list(pages[idx])
        return []


# ── golden references: verbatim copies of each caller's PRE-refactor loop ──


async def _refresh_loop_reference(
    client: Any, candidates: dict[str, Any], *,
    activity_limit: int, max_pages: int, target_buy_rows: int | None,
) -> tuple[dict[str, list[ActivityRow]], dict[str, bool], set[str]]:
    """Verbatim refresh_polymarket_whales loop as it stood before Phase 3
    (broad `except Exception` wrapper; no termination telemetry)."""
    all_condition_ids: set[str] = set()
    activity_by_wallet: dict[str, list] = {}
    truncated_by_wallet: dict[str, bool] = {}
    eff_target = (
        target_buy_rows if target_buy_rows is not None
        else max_pages * activity_limit + 1
    )
    for wallet in candidates:
        try:
            acts, _pages, reason = await _fetch_wallet_activity_windowed(
                client, wallet, activity_limit=activity_limit,
                max_pages=max_pages, target_buy_rows=eff_target,
            )
        except Exception:
            acts, reason = [], "fetch_error"
        truncated_by_wallet[wallet] = reason in ("max_pages_hit", "fetch_error")
        activity_by_wallet[wallet] = acts
        for a in acts:
            if a.type == "TRADE" and a.side == "BUY" and a.condition_id:
                all_condition_ids.add(a.condition_id)
    return activity_by_wallet, truncated_by_wallet, all_condition_ids


async def _seed_loop_reference(
    client: Any, candidates: dict[str, Any], *,
    activity_limit: int, max_pages_per_wallet: int, target_buy_rows: int | None,
) -> tuple[dict[str, list[ActivityRow]], dict[str, bool], set[str], dict[str, int], int]:
    """Verbatim seed_polymarket_watchlist_deep loop as it stood before Phase 3
    (no broad catch; termination_reasons + with_activity telemetry)."""
    termination_reasons = {
        "target_buys_reached": 0, "exhausted": 0,
        "max_pages_hit": 0, "fetch_error": 0,
    }
    with_activity = 0
    eff_target = (
        target_buy_rows if target_buy_rows is not None
        else max_pages_per_wallet * activity_limit + 1
    )
    all_condition_ids: set[str] = set()
    activity_by_wallet: dict[str, list[ActivityRow]] = {}
    truncated_by_wallet: dict[str, bool] = {}
    for wallet in candidates:
        acts, _pages_fetched, term_reason = await _fetch_wallet_activity_windowed(
            client, wallet, activity_limit=activity_limit,
            max_pages=max_pages_per_wallet, target_buy_rows=eff_target,
        )
        activity_by_wallet[wallet] = acts
        truncated_by_wallet[wallet] = term_reason in ("max_pages_hit", "fetch_error")
        termination_reasons[term_reason] = termination_reasons.get(term_reason, 0) + 1
        if acts:
            with_activity += 1
        for a in acts:
            if a.type == "TRADE" and a.side == "BUY" and a.condition_id:
                all_condition_ids.add(a.condition_id)
    return (
        activity_by_wallet, truncated_by_wallet, all_condition_ids,
        termination_reasons, with_activity,
    )


# ── scenario builders ─────────────────────────────────────────────────────


def _walk_to_exhaustion_fixture() -> tuple[dict, _FakeClient]:
    """Covers exhausted (partial page), max_pages_hit (full pages to ceiling),
    and fetch_error (PolymarketDataAPIError mid-walk) under the default
    walk-to-exhaustion target (target_buy_rows=None)."""
    pages = {
        # partial first page (3 < limit 5) -> exhausted
        "0x_exh": [[
            _act(2000, "cid_e1"), _act(1999, "cid_e2"), _act(1998, "cid_e3"),
        ]],
        # full pages (== limit 5), all BUYs, exceeds max_pages 3 -> max_pages_hit
        "0x_max": [
            [_act(3000 - i, f"cid_m{p}_{i}") for i in range(5)]
            for p in range(4)
        ],
        # PolymarketDataAPIError on the very first fetch -> fetch_error, acts=[]
        "0x_err": [],
    }
    raise_at = {"0x_err": (0, PolymarketDataAPIError("boom"))}
    candidates = {"0x_exh": {}, "0x_max": {}, "0x_err": {}}
    return candidates, _FakeClient(pages, raise_at)


def _target_reached_fixture() -> tuple[dict, _FakeClient]:
    """Covers target_buys_reached under an explicit target_buy_rows."""
    pages = {"0x_tgt": [[_act(1000 - i, f"cid_t{i}") for i in range(5)]]}
    return {"0x_tgt": {}}, _FakeClient(pages)


# ── refresh equivalence (broad_catch=True) ────────────────────────────────


async def test_refresh_helper_equals_pre_refactor_loop_default_target():
    candidates, client = _walk_to_exhaustion_fixture()
    # refresh-only: a non-PolymarketDataAPIError must be swallowed by the broad
    # except wrapper (-> fetch_error), NOT propagate. The walk catches only
    # PolymarketDataAPIError, so a ValueError exercises broad_catch exactly.
    client._pages["0x_broad"] = []
    client._raise["0x_broad"] = (0, ValueError("unexpected"))
    candidates["0x_broad"] = {}

    ref = await _refresh_loop_reference(
        client, candidates, activity_limit=5, max_pages=3, target_buy_rows=None,
    )
    new = await fetch_activity_window_for_candidates(
        client, candidates, activity_limit=5, max_pages=3,
        target_buy_rows=None, broad_catch=True,
    )
    assert new == ref
    # Spot-check the termination classification reached each path.
    _act_by, trunc, cids = new
    assert trunc == {
        "0x_exh": False, "0x_max": True, "0x_err": True, "0x_broad": True,
    }
    assert "cid_e1" in cids and "cid_m0_0" in cids


async def test_refresh_helper_equals_pre_refactor_loop_explicit_target():
    candidates, client = _target_reached_fixture()
    ref = await _refresh_loop_reference(
        client, candidates, activity_limit=10, max_pages=3, target_buy_rows=3,
    )
    new = await fetch_activity_window_for_candidates(
        client, candidates, activity_limit=10, max_pages=3,
        target_buy_rows=3, broad_catch=True,
    )
    assert new == ref
    assert new[1] == {"0x_tgt": False}  # target_buys_reached -> not truncated


# ── seed equivalence (on_termination telemetry) ───────────────────────────


async def test_seed_helper_equals_pre_refactor_loop_default_target():
    candidates, client = _walk_to_exhaustion_fixture()
    ref = await _seed_loop_reference(
        client, candidates, activity_limit=5, max_pages_per_wallet=3,
        target_buy_rows=None,
    )
    ref_act, ref_trunc, ref_cids, ref_terms, ref_with = ref

    terms = {
        "target_buys_reached": 0, "exhausted": 0,
        "max_pages_hit": 0, "fetch_error": 0,
    }
    with_activity = {"n": 0}

    def _on_term(wallet: str, reason: str, acts: list[ActivityRow]) -> None:
        terms[reason] = terms.get(reason, 0) + 1
        if acts:
            with_activity["n"] += 1

    new_act, new_trunc, new_cids = await fetch_activity_window_for_candidates(
        client, candidates, activity_limit=5, max_pages=3,
        target_buy_rows=None, on_termination=_on_term,
    )
    assert (new_act, new_trunc, new_cids) == (ref_act, ref_trunc, ref_cids)
    assert terms == ref_terms
    assert with_activity["n"] == ref_with
    # exhausted (1) + max_pages_hit (1) + fetch_error (1); only the two with
    # rows count toward with_activity (the errored wallet returns []).
    assert terms == {
        "target_buys_reached": 0, "exhausted": 1,
        "max_pages_hit": 1, "fetch_error": 1,
    }
    assert with_activity["n"] == 2


async def test_seed_helper_equals_pre_refactor_loop_explicit_target():
    candidates, client = _target_reached_fixture()
    ref = await _seed_loop_reference(
        client, candidates, activity_limit=10, max_pages_per_wallet=3,
        target_buy_rows=3,
    )
    ref_act, ref_trunc, ref_cids, ref_terms, ref_with = ref

    terms = {
        "target_buys_reached": 0, "exhausted": 0,
        "max_pages_hit": 0, "fetch_error": 0,
    }
    counted = {"n": 0}

    def _on_term(wallet: str, reason: str, acts: list[ActivityRow]) -> None:
        terms[reason] = terms.get(reason, 0) + 1
        if acts:
            counted["n"] += 1

    new = await fetch_activity_window_for_candidates(
        client, candidates, activity_limit=10, max_pages=3,
        target_buy_rows=3, on_termination=_on_term,
    )
    assert new == (ref_act, ref_trunc, ref_cids)
    assert terms == ref_terms
    assert terms["target_buys_reached"] == 1
    assert counted["n"] == ref_with == 1


# ── decoupling: single-source walk, no seed re-export ──────────────────────


def test_walk_is_single_source_in_whale_screening():
    """The walk has ONE definition, in whale_screening. seed no longer
    re-exports it — option (c) Phase 4 removed the back-compat shim, so every
    caller/test imports it directly from whale_screening. This is the fully
    removed refresh->seed script-imports-script coupling."""
    assert (
        whale_screening._fetch_wallet_activity_windowed
        is _fetch_wallet_activity_windowed
    )
    assert not hasattr(seed_mod, "_fetch_wallet_activity_windowed")
