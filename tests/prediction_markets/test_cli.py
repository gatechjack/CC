"""Smoke test for trading_corp/scripts/pm_cli.py (report + rollup, sync subcommands). Offline.
Loads the CLI by file path (it lives outside the package). Spec: §5, §11.
"""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from trading_corp.prediction_markets import db, ingest
from trading_corp.data.polymarket_data_api_client import ClosedPositionRow

_CLI_PATH = Path(__file__).resolve().parents[2] / "trading_corp" / "scripts" / "pm_cli.py"
_FIX = Path(__file__).parent / "fixtures" / "closed_positions"
NOW = 1_700_000_000


def _pm_cli():
    spec = importlib.util.spec_from_file_location("pm_cli_under_test", _CLI_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _Cli:
    def __init__(self, page):
        self._page = page

    async def fetch_closed_positions(self, wallet, *, limit=50, offset=0):
        return [ClosedPositionRow.from_api(r) for r in (self._page if offset == 0 else [])]


async def _noev(slug, **kw):
    return []


async def _seed(tmp_path):
    p = str(tmp_path / "pm.db")
    db.init_db(p)
    page = json.loads((_FIX / "winner_page.json").read_text(encoding="utf-8"))
    with db.connect(p) as conn:
        await ingest.backfill_wallet(conn, "0xwinnerwhale", client=_Cli(page), now_ts=NOW, fetch_events=_noev)
    return p


@pytest.mark.skipif(not _CLI_PATH.exists(), reason="pm_cli.py not present in this tree")
async def test_cli_rollup_and_report_json(tmp_path, capsys):
    p = await _seed(tmp_path)
    pm = _pm_cli()
    assert pm.main(["--db", p, "rollup"]) == 0            # sync subcommand
    capsys.readouterr()
    assert pm.main(["--db", p, "report", "--min-resolved", "1", "--format", "json"]) == 0
    board = json.loads(capsys.readouterr().out)
    assert {"ufc", "mlb", "nba"} <= {r["category"] for r in board}


@pytest.mark.skipif(not _CLI_PATH.exists(), reason="pm_cli.py not present in this tree")
def test_cli_only_wallets_restricts_to_subset():
    pm = _pm_cli()
    a = SimpleNamespace(only_wallets=["0xAAA", "0xBBB"], legacy_db="/no/such.db", seed_yaml=None, wallets=None)
    assert pm._seed_wallets(a) == ["0xaaa", "0xbbb"]     # bypasses roster -> single-wallet deploy checkpoint
    a2 = SimpleNamespace(only_wallets=None, legacy_db="/no/such.db", seed_yaml=None, wallets=["0xCLI"])
    assert pm._seed_wallets(a2) == ["0xcli"]             # roster path (empty legacy + cli extra)
