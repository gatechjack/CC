"""Seed-roster + G0-loser loads for Prediction Markets (P1). READ-ONLY on legacy state.

Unions the live + PCT-paper whales from the legacy `agent_state` table
(data/trading_corp.db, opened sqlite mode=ro -- never written) + a config yaml + CLI
extras into a P1 seed roster. No engine import; no writes. Paths injectable for offline tests.

Spec: reports/prediction_markets/P1_PLAN.md §5, §8.
"""
from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Iterable

LEGACY_DB_DEFAULT = "data/trading_corp.db"

# G0 known net-losers (full addresses -- TRANSITION_TO_BUILD_AGENT.md). Used by `pm_cli
# g0-validate`; NOT part of the scoreboard seed (they are probe targets, not tracked whales).
G0_KNOWN_LOSERS: list[dict] = [
    {"wallet": "0x43e0f84fe8fb4623a5ff485fe9f7bc0f4b458618", "user_name": "evanng", "note": "UFC ~-13.7k"},
    {"wallet": "0x8056189d56833ce5b3945dea9149b62c5111b64d", "user_name": "csgod", "note": "UFC ~-9.5k"},
    {"wallet": "0x71ed0bc95433cdf1be29f43219725fce9addd9eb", "user_name": "d1k21", "note": "Fed ~-168k"},
]

# legacy agent_state (agent, key) sources for the P1 seed roster
_ROSTER_SOURCES = [
    ("poly_kalshi_mlb", "live_whales", "live"),
    ("polymarket_copy_trader", "selected_whales", "pct_selected"),
    ("polymarket_copy_trader", "pinned_whales", "pct_pinned"),
]


def wallet_of(entry: Any) -> str:
    """Normalize a roster entry to a lowercase wallet (dict wallet|proxy_wallet, or bare str).
    Mirrors legacy roster_split.wallet_of field-variant handling."""
    if isinstance(entry, dict):
        return str(entry.get("wallet") or entry.get("proxy_wallet") or "").lower()
    return str(entry or "").lower()


def _name_of(entry: Any) -> str:
    if isinstance(entry, dict):
        return str(entry.get("user_name") or entry.get("name") or "")
    return ""


def read_agent_state(legacy_db_path: str, agent: str, key: str) -> Any:
    """READ-ONLY (sqlite mode=ro) fetch + json-decode of agent_state.value_json at (agent, key).
    Returns the decoded value or None. Never creates or writes the DB."""
    uri = "file:%s?mode=ro" % os.path.abspath(legacy_db_path)
    conn = sqlite3.connect(uri, uri=True)
    try:
        row = conn.execute(
            "SELECT value_json FROM agent_state WHERE agent = ? AND key = ?", (agent, key)
        ).fetchone()
    finally:
        conn.close()
    if not row or row[0] is None:
        return None
    try:
        return json.loads(row[0])
    except (TypeError, ValueError):
        return None


def _load_seed_yaml(path: str) -> list:
    if not path or not os.path.exists(path):
        return []
    import yaml  # lazy (only when a yaml path is given)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or []
    if isinstance(data, dict):
        data = data.get("wallets") or []
    return data if isinstance(data, list) else []


def load_seed_roster(*, legacy_db_path: str | None = None, seed_yaml_path: str | None = None,
                     extra_wallets: Iterable[str] | None = None) -> list[dict]:
    """Union live_whales + PCT selected+pinned (legacy agent_state, read-only) + seed yaml +
    CLI extras. Dedup by lowercase wallet (first source wins the label). Missing legacy DB is
    tolerated (returns just yaml + extras). Returns [{wallet, user_name, source}]."""
    legacy = legacy_db_path or LEGACY_DB_DEFAULT
    out: dict[str, dict] = {}

    def _add(entry: Any, source: str) -> None:
        w = wallet_of(entry)
        if not w or w in out:
            return
        out[w] = {"wallet": w, "user_name": _name_of(entry), "source": source}

    if os.path.exists(legacy):
        for agent, key, source in _ROSTER_SOURCES:
            for entry in (read_agent_state(legacy, agent, key) or []):
                _add(entry, source)
    for entry in _load_seed_yaml(seed_yaml_path or ""):
        _add(entry, "seed_yaml")
    for w in (extra_wallets or []):
        _add(w, "cli")
    return list(out.values())
