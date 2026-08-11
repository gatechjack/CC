"""Phase-3 AST import-boundary test (plan § Architecture import rule).

ONLY `trading_corp/mace/rh_broker.py` may import `trading_corp.brokers.*`. Every
other module under `trading_corp/mace/` must stay broker-neutral (domain types
only), so a future Tasty impl replaces rh_broker.py alone. This walks the AST of
each mace module and asserts the boundary — a cheap guard against an accidental
`from trading_corp.brokers...` creeping into strategy/execution/manager.
"""
from __future__ import annotations

import ast
from pathlib import Path

MACE_DIR = Path(__file__).resolve().parents[1] / "trading_corp" / "mace"
_BROKER_PREFIX = "trading_corp.brokers"


def _imports_brokers(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == _BROKER_PREFIX or mod.startswith(_BROKER_PREFIX + "."):
                return True
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == _BROKER_PREFIX or alias.name.startswith(_BROKER_PREFIX + "."):
                    return True
    return False


def test_only_rh_broker_imports_trading_corp_brokers():
    offenders = sorted(
        p.name for p in MACE_DIR.glob("*.py")
        if p.name != "rh_broker.py" and _imports_brokers(p)
    )
    assert offenders == [], f"mace modules importing trading_corp.brokers.*: {offenders}"


def test_rh_broker_is_the_broker_seam():
    # sanity: the one file that IS allowed to must actually carry the import,
    # so the test can't pass trivially by everyone avoiding the broker layer.
    assert _imports_brokers(MACE_DIR / "rh_broker.py")
