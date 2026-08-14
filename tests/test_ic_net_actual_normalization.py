"""Direction-normalizing read accessors for combo_filled.net_actual.

A pre-2026-07-24 leg-attribution bug (fixed in robinhood.py) could store a
combo's `net_actual` with a FLIPPED sign when Robinhood returned the legs
reordered — the 2026-07-24 PMCC rolls booked RKLB -1.17 and OPEN -0.26 for what
were genuine credits, and the stored slippage inflated to abs(-1.17-1.14)=2.31.

`ic_telemetry.normalized_net_actual` / `slippage_vs_limit` correct this at READ
time (credit +, debit −; slippage from magnitudes) WITHOUT mutating the audit
rows, which faithfully record the buggy computation. These tests pin the
normalization and prove combo_slippage_stats routes through it end-to-end.
"""
import json

import pytest

import trading_corp.persistence.db as db
from trading_corp.agents.ic_telemetry import (
    combo_slippage_stats,
    normalized_net_actual,
    slippage_vs_limit,
)


# ── normalized_net_actual ───────────────────────────────────────────────────

def test_credit_negative_flips_to_positive():
    # The two real sign-flipped 2026-07-24 rows.
    assert normalized_net_actual(-1.17, "credit") == pytest.approx(1.17)
    assert normalized_net_actual(-0.26, "credit") == pytest.approx(0.26)


def test_credit_positive_unchanged():
    # A correctly-recorded credit stays positive (no double-flip).
    assert normalized_net_actual(1.17, "credit") == pytest.approx(1.17)
    assert normalized_net_actual(0.26, "credit") == pytest.approx(0.26)


def test_debit_renders_negative():
    # Debit renders negative regardless of the stored magnitude's sign
    # (net_actual is stored as a positive magnitude in the intended design).
    assert normalized_net_actual(-0.20, "debit") == pytest.approx(-0.20)
    assert normalized_net_actual(0.20, "debit") == pytest.approx(-0.20)


def test_net_actual_none_and_bad_value_safe():
    assert normalized_net_actual(None, "credit") is None
    assert normalized_net_actual("not-a-number", "credit") is None
    # Unknown direction: fall back to the magnitude (never crash the digest).
    assert normalized_net_actual(-1.17, None) == pytest.approx(1.17)


# ── slippage_vs_limit (correct slippage vs attempted) ───────────────────────

def test_slippage_immune_to_sign_flip():
    # Stored slippage was abs(-1.17-1.14)=2.31 / abs(-0.26-0.24)=0.50; the true
    # favorable gap is |1.17|-|1.14|=0.03 / |0.26|-|0.24|=0.02.
    assert slippage_vs_limit(-1.17, 1.14) == pytest.approx(0.03)
    assert slippage_vs_limit(-0.26, 0.24) == pytest.approx(0.02)


def test_slippage_correct_row_matches_raw_formula():
    # A correctly-signed credit yields the same slippage the write-side formula did.
    assert slippage_vs_limit(1.17, 1.14) == pytest.approx(0.03)


def test_slippage_none_safe():
    assert slippage_vs_limit(None, 1.14) is None
    assert slippage_vs_limit(-1.17, None) is None


# ── integration: combo_slippage_stats routes through the accessors ──────────

def _seed(url, rows):
    db.init_db(url)
    with db.connect(url) as conn:
        for ts, payload in rows:
            conn.execute(
                "INSERT INTO audit_event (ts, actor, kind, payload_json) "
                "VALUES (?, 'data_exec', 'combo_filled', ?)",
                (ts, json.dumps(payload)),
            )


def test_combo_slippage_stats_normalizes_end_to_end(tmp_path):
    url = f"sqlite:///{tmp_path.as_posix()}/t.db"
    _seed(url, [
        ("2026-07-24T13:38:24+00:00", {
            "combo_id": "5c9e347f", "strategy": "robinhood_pmcc",
            "division": "robinhood_pmcc", "direction": "credit",
            "net_limit_price": 0.24, "net_actual": -0.26,            # sign-flipped
            "actual_vs_limit_slippage_dollars": 0.50,                # inflated (ignored)
            "legs": [{"position_effect": "close"}],
        }),
        ("2026-07-24T13:42:32+00:00", {
            "combo_id": "360f4b92", "strategy": "robinhood_pmcc",
            "division": "robinhood_pmcc", "direction": "credit",
            "net_limit_price": 1.14, "net_actual": -1.17,            # sign-flipped
            "actual_vs_limit_slippage_dollars": 2.31,                # inflated (ignored)
            "legs": [{"position_effect": "close"}],
        }),
    ])
    report = combo_slippage_stats(
        strategy="robinhood_pmcc", division="robinhood_pmcc", db_url=url,
    )
    by_id = {e["combo_id"]: e for e in report["events"]}
    # net_actual rendered as a true positive credit (not the stored -x).
    assert by_id["5c9e347f"]["net_actual"] == pytest.approx(0.26)
    assert by_id["360f4b92"]["net_actual"] == pytest.approx(1.17)
    # slippage recomputed from magnitudes, not the inflated stored value.
    assert by_id["5c9e347f"]["slippage_dollars"] == pytest.approx(0.02)
    assert by_id["360f4b92"]["slippage_dollars"] == pytest.approx(0.03)
    # summary totals reflect the corrected slippage (0.02 + 0.03), not 0.50 + 2.31.
    assert report["summary"]["n"] == 2
    assert report["summary"]["total_slippage_realized"] == pytest.approx(0.05)
    assert report["summary"]["max_slippage"] == pytest.approx(0.03)
