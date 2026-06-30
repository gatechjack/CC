"""Phase 1 (2026-06-29) — two-live-bitunix-division architecture, wired but NOT
cut over. SFP stays live on its current key throughout; futures is brought live
on its own account only at the separate Phase 2 cutover.

Proves the SFP-safety crux:
  * boot-guard keys on secret_ref (distinct refs pass even when they transiently
    resolve to the SAME account during the Phase-2 cutover; same ref refuses);
  * the reconciler is per-account isolated — a reconciler scoped to one division
    NEVER sees the other division's rows, audit, or two-tick confirm;
  * the SINGLE-live path (division=None) is behaviorally IDENTICAL to today
    (the Phase-1 SFP-undisturbed case);
  * secrets.py resolves the new bitunix_sfp account keys without disturbing
    bitunix_futures.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from trading_corp.main import (
    _bitunix_live_secret_ref_conflicts,
    _resolve_bitunix_creds,
)
from trading_corp.agents.divisions.bitunix_position_reconciler import (
    POSITION_STATE_RECONCILED_KIND,
    RECONCILER_ACTOR,
    _latest_position_state_payload,
    _load_tracked_live_rows,
    _recon_actor,
    reconcile_position_state,
)
from trading_corp.brokers.bitunix import BitunixBroker
from trading_corp.persistence import db
from trading_corp.utils.secrets import Secrets


# ─── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def db_url(tmp_path: Path) -> str:
    p = tmp_path / "phase1.db"
    url = f"sqlite:///{p}"
    db.init_db(url)
    return url


def _seed_live(db_url, order_id, division, *, side="buy", symbol="BTC/USDT.P",
               execution_mode="live", qty=0.01) -> None:
    extra = {"execution_mode": execution_mode, "broker_order_id": order_id,
             "entry_reference_price": 65000.0, "stop_price": 64000.0}
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO paper_trade_record ("
            " order_id, ts, strategy, division, symbol, side, qty, "
            " entry_reference_price, stop_price, tp_price, max_hold_seconds, "
            " result, extra_json"
            ") VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (order_id, "2026-06-29T00:00:00+00:00", division, division, symbol,
             side, qty, 65000.0, 64000.0, 66000.0, 86400, None,
             json.dumps(extra)),
        )


def _flat_broker() -> BitunixBroker:
    b = BitunixBroker(api_key="k", api_secret="s")
    b._client = MagicMock()
    b._request = AsyncMock(return_value=[])   # broker FLAT
    b._halt_new_orders = False
    b._halt_reason = None
    return b


def _div(slug, secret_ref=None):
    return SimpleNamespace(slug=slug, secret_ref=secret_ref,
                           broker="bitunix", enabled=True)


# ─── boot-guard: per-secret_ref distinctness ─────────────────────────────────

def test_guard_distinct_refs_pass_even_when_same_account():
    """★ Phase-2 transient: sfp→bitunix_sfp + futures→bitunix_futures both live;
    both KV secrets transiently hold the SAME original account's key. Distinct
    REFS → guard PASSES (no conflict). This is the whole point of keying on the
    ref string, not the resolved account."""
    divs = [_div("bitunix_sfp", "bitunix_sfp"), _div("bitunix_futures", None)]
    live = ["bitunix_sfp", "bitunix_futures"]
    assert _bitunix_live_secret_ref_conflicts(divs, live) == {}


def test_guard_shared_ref_refuses():
    """Two live divisions sharing a secret_ref (same account) → CONFLICT. This is
    the Phase-1 misconfig the guard must catch (both on bitunix_futures)."""
    divs = [_div("bitunix_sfp", "bitunix_futures"), _div("bitunix_futures", None)]
    live = ["bitunix_sfp", "bitunix_futures"]
    conflict = _bitunix_live_secret_ref_conflicts(divs, live)
    assert "bitunix_futures" in conflict
    assert set(conflict["bitunix_futures"]) == {"bitunix_sfp", "bitunix_futures"}


def test_guard_one_live_no_conflict():
    """Phase-1 runtime: only SFP live → no conflict regardless of its ref."""
    divs = [_div("bitunix_sfp", "bitunix_futures"), _div("bitunix_futures", None)]
    assert _bitunix_live_secret_ref_conflicts(divs, ["bitunix_sfp"]) == {}


def test_guard_zero_live_no_conflict():
    divs = [_div("bitunix_sfp", "bitunix_futures"), _div("bitunix_futures", None)]
    assert _bitunix_live_secret_ref_conflicts(divs, []) == {}


# ─── secrets: bitunix_sfp account resolvable, bitunix_futures intact ──────────

def test_secrets_dataclass_has_bitunix_sfp_fields():
    assert "bitunix_sfp_api_key" in Secrets.__dataclass_fields__
    assert "bitunix_sfp_api_secret" in Secrets.__dataclass_fields__
    # futures fields preserved
    assert "bitunix_futures_api_key" in Secrets.__dataclass_fields__


def test_resolve_creds_secret_ref_sfp_picks_sfp_keys():
    secrets = SimpleNamespace(
        bitunix_futures_api_key="FUT_K", bitunix_futures_api_secret="FUT_S",
        bitunix_sfp_api_key="SFP_K", bitunix_sfp_api_secret="SFP_S",
    )
    div = SimpleNamespace(secret_ref="bitunix_sfp")
    assert _resolve_bitunix_creds(div, secrets) == ("SFP_K", "SFP_S")


def test_resolve_creds_default_ref_still_futures():
    secrets = SimpleNamespace(
        bitunix_futures_api_key="FUT_K", bitunix_futures_api_secret="FUT_S",
        bitunix_sfp_api_key="SFP_K", bitunix_sfp_api_secret="SFP_S",
    )
    # secret_ref unset → falls back to bitunix_futures (Phase-1 SFP behavior)
    assert _resolve_bitunix_creds(SimpleNamespace(secret_ref=None), secrets) == (
        "FUT_K", "FUT_S")


# ─── reconciler row isolation ────────────────────────────────────────────────

def test_load_rows_division_scopes_to_own_rows(db_url):
    """★ Simultaneous SFP + futures open live rows. Each division's row read sees
    ONLY its own; None (legacy) sees both."""
    _seed_live(db_url, "sfp-1", "bitunix_sfp")
    _seed_live(db_url, "fut-1", "bitunix_futures")
    _seed_live(db_url, "paper-1", "bitunix_sfp", execution_mode="paper")  # excluded

    sfp = {r["order_id"] for r in _load_tracked_live_rows(db_url, "bitunix_sfp")}
    fut = {r["order_id"] for r in _load_tracked_live_rows(db_url, "bitunix_futures")}
    both = {r["order_id"] for r in _load_tracked_live_rows(db_url, None)}

    assert sfp == {"sfp-1"}                    # futures row invisible to SFP
    assert fut == {"fut-1"}                    # SFP row invisible to futures
    assert both == {"sfp-1", "fut-1"}          # legacy = all live rows


def test_load_rows_sfp_only_filtered_equals_legacy(db_url):
    """SFP-UNCHANGED: with ONLY SFP rows present (today's prod state), the
    division-scoped read == the legacy unscoped read — Phase-1 is a no-op for SFP."""
    _seed_live(db_url, "sfp-1", "bitunix_sfp")
    _seed_live(db_url, "sfp-2", "bitunix_sfp", side="sell")
    scoped = {r["order_id"] for r in _load_tracked_live_rows(db_url, "bitunix_sfp")}
    legacy = {r["order_id"] for r in _load_tracked_live_rows(db_url, None)}
    assert scoped == legacy == {"sfp-1", "sfp-2"}


# ─── audit actor scoping + two-tick confirm isolation ────────────────────────

def test_recon_actor_scoping():
    assert _recon_actor(None) == RECONCILER_ACTOR            # legacy single path
    assert _recon_actor("bitunix_futures") == f"{RECONCILER_ACTOR}:bitunix_futures"
    assert _recon_actor("bitunix_sfp") == f"{RECONCILER_ACTOR}:bitunix_sfp"


def _seed_audit(db_url, actor, kind=POSITION_STATE_RECONCILED_KIND):
    payload = {"match_count": 0, "missing_on_broker_count": 0,
               "orphan_on_broker_count": 0, "missing_on_broker": [],
               "orphan_on_broker": []}
    with db.connect(db_url) as conn:
        conn.execute(
            "INSERT INTO audit_event (ts, actor, kind, payload_json) "
            "VALUES (?,?,?,?)",
            ("2026-06-29T00:00:00+00:00", actor, kind, json.dumps(payload)),
        )


def test_latest_payload_isolated_by_division(db_url):
    """★ A futures reconcile tick (scoped actor) must NOT satisfy an SFP
    two-tick confirm, and vice versa — else one account's clean/divergence tick
    would reset the other's auto-book / halt-release state machine."""
    _seed_audit(db_url, _recon_actor("bitunix_futures"))
    assert _latest_position_state_payload(db_url, "bitunix_futures") is not None
    assert _latest_position_state_payload(db_url, "bitunix_sfp") is None
    # legacy single actor unaffected (no legacy-actor rows seeded)
    assert _latest_position_state_payload(db_url, None) is None


# ─── end-to-end: futures reconciler NEVER sees SFP rows ──────────────────────

@pytest.mark.asyncio
async def test_futures_reconcile_never_sees_sfp_rows(db_url):
    """★ THE SFP-SAFETY CRUX. Both divisions have an OPEN live row; the futures
    broker is FLAT. A futures-scoped reconcile must report ONLY the futures row
    as missing — the SFP row must be invisible (else SFP's live trade would be
    mis-booked / false-halted by the futures account's reconciler)."""
    _seed_live(db_url, "sfp-1", "bitunix_sfp")
    _seed_live(db_url, "fut-1", "bitunix_futures")
    broker = _flat_broker()

    result = await reconcile_position_state(
        broker, db_url, division="bitunix_futures",
    )
    missing_ids = {m.order_id for m in result.missing_on_broker}
    assert missing_ids == {"fut-1"}          # SFP row NOT seen
    assert "sfp-1" not in missing_ids
    # the divergence audit is written under the futures-scoped actor only
    assert _latest_position_state_payload(db_url, "bitunix_futures") is not None
    assert _latest_position_state_payload(db_url, "bitunix_sfp") is None


@pytest.mark.asyncio
async def test_sfp_reconcile_scoped_equals_legacy_when_sfp_only(db_url):
    """SFP-UNCHANGED end-to-end: with ONLY SFP rows (today's prod), a
    division='bitunix_sfp' reconcile and the legacy division=None reconcile
    surface the SAME missing set (the single open SFP row vs a flat broker)."""
    _seed_live(db_url, "sfp-1", "bitunix_sfp")

    scoped = await reconcile_position_state(
        _flat_broker(), db_url, division="bitunix_sfp", halt_on_divergence=False,
    )
    legacy = await reconcile_position_state(
        _flat_broker(), db_url, division=None, halt_on_divergence=False,
    )
    assert {m.order_id for m in scoped.missing_on_broker} == \
           {m.order_id for m in legacy.missing_on_broker} == {"sfp-1"}
