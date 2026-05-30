"""AST-based completeness gate against `secrets.X` AttributeError class.

Surfaced 2026-05-30 by the Stage-1 prod-deploy rollback: `main.py:1087`
referenced `secrets.odds_api_key` but the `Secrets` dataclass had no
such field, crashing the process at first import-and-construct. Module
import alone did NOT catch it (the AttributeError is at construction,
not at `from … import`).

This test parses `trading_corp/main.py` and asserts every distinct
`secrets.X` attribute read resolves to a field (or @property) on the
`Secrets` dataclass. Mechanical, parse-only — no runtime construction
required.

Strengthens [[mocks-dont-catch-sdk-shape]] and [[verify-premises-against-ground-truth]]
discipline: if a future surgical edit to main.py reads a new
`secrets.X` without round-tripping the dataclass field, this gate
fails before the deploy.
"""
from __future__ import annotations

import ast
import dataclasses
from pathlib import Path

from trading_corp.utils.secrets import Secrets


MAIN_PY = Path(__file__).resolve().parent.parent / "trading_corp" / "main.py"


def _attrs_read_off_secrets(source: str) -> set[str]:
    """Return the set of attribute names read off a bare `secrets` Name."""
    tree = ast.parse(source)
    found: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "secrets"
        ):
            found.add(node.attr)
    return found


def _secrets_surface() -> set[str]:
    """Return Secrets dataclass field names + @property names."""
    field_names = {f.name for f in dataclasses.fields(Secrets)}
    prop_names = {
        name
        for name, attr in vars(Secrets).items()
        if isinstance(attr, property)
    }
    return field_names | prop_names


def test_main_py_secrets_reads_resolve_to_dataclass_surface() -> None:
    """Every `secrets.X` access in main.py must resolve to a Secrets field or property."""
    source = MAIN_PY.read_text(encoding="utf-8")
    reads = _attrs_read_off_secrets(source)
    surface = _secrets_surface()
    missing = reads - surface
    assert not missing, (
        f"main.py reads {sorted(missing)} off `secrets` but Secrets has no such "
        f"field or @property. This is the 2026-05-30 odds_api_key class of bug. "
        f"Add the field to Secrets (+ populator in load_secrets) before the next "
        f"deploy."
    )


def test_ast_helper_catches_the_2026_05_30_regression() -> None:
    """Synthetic regression: a hypothetical `secrets.never_added_field` MUST be caught."""
    synthetic = "x = secrets.never_added_field\n"
    reads = _attrs_read_off_secrets(synthetic)
    surface = _secrets_surface()
    assert "never_added_field" in reads
    assert "never_added_field" not in surface
    assert reads - surface == {"never_added_field"}
