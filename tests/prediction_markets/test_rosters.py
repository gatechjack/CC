"""Tests for trading_corp.prediction_markets.rosters — offline (tmp legacy sqlite + tmp yaml).

Spec: reports/prediction_markets/P1_PLAN.md §5, §11.
"""
import json
import sqlite3

from trading_corp.prediction_markets import rosters


def _make_legacy(tmp_path, states):
    p = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(p))
    conn.execute("CREATE TABLE agent_state (agent TEXT, key TEXT, value_json TEXT, updated_ts TEXT, "
                 "PRIMARY KEY(agent, key))")
    for agent, key, value in states:
        conn.execute("INSERT INTO agent_state VALUES (?, ?, ?, ?)",
                     (agent, key, json.dumps(value), "2026-01-01T00:00:00Z"))
    conn.commit()
    conn.close()
    return str(p)


def test_wallet_of_normalizes():
    assert rosters.wallet_of({"wallet": "0xABC"}) == "0xabc"
    assert rosters.wallet_of({"proxy_wallet": "0xDEF"}) == "0xdef"
    assert rosters.wallet_of("0xGHI") == "0xghi"
    assert rosters.wallet_of({}) == ""
    assert rosters.wallet_of(None) == ""


def test_g0_known_losers_full_addresses():
    assert len(rosters.G0_KNOWN_LOSERS) == 3
    ws = {x["wallet"] for x in rosters.G0_KNOWN_LOSERS}
    assert "0x43e0f84fe8fb4623a5ff485fe9f7bc0f4b458618" in ws
    assert all(w.startswith("0x") and len(w) == 42 for w in ws)


def test_read_agent_state_readonly(tmp_path):
    legacy = _make_legacy(tmp_path, [("a", "k", [1, 2, 3])])
    assert rosters.read_agent_state(legacy, "a", "k") == [1, 2, 3]
    assert rosters.read_agent_state(legacy, "a", "missing") is None


def test_load_seed_roster_union_dedup_normalize(tmp_path):
    legacy = _make_legacy(tmp_path, [
        ("poly_kalshi_mlb", "live_whales",
         [{"wallet": "0xLIVE1", "user_name": "SDTrading"},
          {"proxy_wallet": "0xLIVE2", "user_name": "xifutloong3"}]),
        ("polymarket_copy_trader", "selected_whales",
         [{"wallet": "0xPCT1", "user_name": "FordBronco"},
          {"wallet": "0xLIVE1", "user_name": "dup"}]),
        ("polymarket_copy_trader", "pinned_whales",
         [{"wallet": "0xPIN1", "user_name": "pako"}]),
    ])
    roster = rosters.load_seed_roster(legacy_db_path=legacy, extra_wallets=["0xCLI1", "0xlive1"])
    by = {r["wallet"]: r for r in roster}
    assert set(by) == {"0xlive1", "0xlive2", "0xpct1", "0xpin1", "0xcli1"}  # deduped + lowercased
    assert by["0xlive1"]["source"] == "live"            # first source wins over pct dup + cli
    assert by["0xlive2"]["user_name"] == "xifutloong3"  # proxy_wallet normalized
    assert by["0xcli1"]["source"] == "cli"


def test_missing_legacy_db_tolerated(tmp_path):
    roster = rosters.load_seed_roster(legacy_db_path=str(tmp_path / "nope.db"), extra_wallets=["0xX"])
    assert [r["wallet"] for r in roster] == ["0xx"]


def test_seed_yaml_loaded(tmp_path):
    y = tmp_path / "seed.yaml"
    y.write_text("wallets:\n  - {wallet: '0xYAML1', user_name: extra}\n", encoding="utf-8")
    roster = rosters.load_seed_roster(legacy_db_path=str(tmp_path / "none.db"), seed_yaml_path=str(y))
    assert roster[0]["wallet"] == "0xyaml1" and roster[0]["source"] == "seed_yaml"
