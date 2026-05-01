"""Pytest fixtures shared across tests."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# Disable LLM narration during tests so we never hit the API.
os.environ.setdefault("ANTHROPIC_API_KEY", "")


@pytest.fixture
def tmp_db(tmp_path: Path) -> str:
    db_file = tmp_path / "test_trading_corp.db"
    return f"sqlite:///{db_file.as_posix()}"


@pytest.fixture
def tmp_risk_yaml(tmp_path: Path) -> Path:
    p = tmp_path / "risk.yaml"
    p.write_text(
        """
global:
  per_trade_risk_pct: 0.015
  per_strategy_daily_loss_pct: 0.03
  per_account_max_drawdown_pct: 0.15
  correlation_cap: 0.7
  target_annualized_vol: 0.25
trend_alignment:
  counter_trend_size_multiplier: 0.5
overrides: {}
""".strip(),
        encoding="utf-8",
    )
    return p
