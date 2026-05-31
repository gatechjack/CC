"""AST-based completeness gate against dataclass kwarg/field-mismatch class.

Sibling to ``test_secrets_completeness.py`` — that test catches ``secrets.X``
attribute-read drift; this one catches ``Dataclass(X=...)`` construction-
kwarg drift on origin/main.

What this catches
-----------------
- Source-code drift on origin/main where main.py constructs a dataclass
  with a keyword argument the dataclass has no field for. Example: a
  future commit adding ``WebDeps(brand_new_field=foo)`` at main.py
  without also adding ``brand_new_field`` on the WebDeps dataclass.
  Crashes at startup with ``TypeError``, invisible to import-sanity
  checks (which don't execute construction).

What this does NOT catch
------------------------
- Prod-vs-source filesystem drift. The 2026-05-30 22:43-23:09 UTC
  redeploy rolled back on a ``TypeError`` against ``WebDeps.tasty_division``
  because prod's filesystem held a stale ``web/app.py`` predating the
  field's addition on origin/main. origin/main itself was internally
  consistent — this test would have passed against origin/main on
  2026-05-30 because the field was already there. For prod-vs-source
  coverage, see ``scripts/prod_vs_main_file_level_md5_sweep.py`` and
  ``[[deploy-transfer-set-diff-derived-misses-stale-prod-files]]``.

Mechanics
---------
- AST-walk ``trading_corp/main.py``. Collect every ``from X import Y``
  binding into a map ``Y -> X``. Includes imports inside function bodies.
- AST-walk for ``ast.Call`` nodes where ``node.func`` is ``ast.Name``.
  For each call's name ``N``:
    - If ``N``'s binding resolves (via importlib) to a class ``C`` marked
      ``@dataclass``, validate every keyword arg in the call against
      ``C``'s ``__init__`` signature. Names not accepted by ``__init__``
      are findings.
    - Otherwise skip — ``N`` is not a dataclass we know about, or the
      import failed under the test's environment.

Strengthens
-----------
- ``[[mocks-dont-catch-sdk-shape]]``
- ``[[verify-premises-against-ground-truth]]``
- ``[[deploy-transfer-set-diff-derived-misses-stale-prod-files]]`` — this
  test is the SOURCE-CODE half of a two-piece defense; that memory is the
  FILESYSTEM half (the ``prod_vs_main_file_level_md5_sweep.py`` tool).
"""
from __future__ import annotations

import ast
import dataclasses
import importlib
import inspect
from pathlib import Path

import pytest

MAIN_PY = Path(__file__).resolve().parent.parent / "trading_corp" / "main.py"


def _collect_from_imports(tree: ast.Module) -> dict[str, str]:
    """Map local-name -> module-path for every ``from X import Y`` in the tree.

    Walks the full AST so imports inside function bodies are captured.
    Last-wins on name collisions (acceptable approximation).
    """
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                local = alias.asname or alias.name
                bindings[local] = node.module
    return bindings


def _try_import_dataclass(name: str, module_path: str) -> type | None:
    """Return the imported class if it's a ``@dataclass``; else None.

    Suppresses any import-side exception so the test doesn't crash on
    optional-deps absent or unimportable test-env modules.
    """
    try:
        module = importlib.import_module(module_path)
    except Exception:
        return None
    cls = getattr(module, name, None)
    if cls is None or not isinstance(cls, type):
        return None
    if not dataclasses.is_dataclass(cls):
        return None
    return cls


def _accepted_kwargs(cls: type) -> set[str] | None:
    """Return the set of kwarg names accepted by ``cls(...)``.

    Returns None if ``cls.__init__`` accepts ``**kwargs`` (any kwarg
    valid — can't statically validate, skip the call site).

    Uses ``inspect.signature`` for the canonical accepted-kwargs surface
    (handles InitVars, custom ``__init__`` overrides, etc.). Falls back to
    ``dataclasses.fields()`` if signature introspection fails.
    """
    try:
        sig = inspect.signature(cls.__init__)
    except (ValueError, TypeError):
        return {f.name for f in dataclasses.fields(cls)}
    accepts_var_keyword = any(
        p.kind == inspect.Parameter.VAR_KEYWORD
        for p in sig.parameters.values()
    )
    if accepts_var_keyword:
        return None
    return {
        name
        for name, param in sig.parameters.items()
        if name != "self"
        and param.kind
        in (
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        )
    }


def _collect_dataclass_targets(bindings: dict[str, str]) -> dict[str, type]:
    """Resolve every name->module-path binding to a class; keep only ``@dataclass``."""
    out: dict[str, type] = {}
    for name, module_path in bindings.items():
        cls = _try_import_dataclass(name, module_path)
        if cls is not None:
            out[name] = cls
    return out


def _find_kwarg_findings(
    source: str,
    targets: dict[str, type],
) -> list[tuple[int, str, str, list[str]]]:
    """Return ``[(lineno, dataclass_name, bad_kwarg, accepted_kwargs_sorted), ...]``.

    Skips:
    - Calls whose ``.func`` is not ``ast.Name`` (e.g., ``pkg.Foo()``).
    - Names not in ``targets``.
    - Targets whose ``__init__`` accepts ``**kwargs`` (all-accepted).
    - kwargs whose ``.arg`` is None (``**dict`` splat — can't statically validate).
    """
    tree = ast.parse(source)
    findings: list[tuple[int, str, str, list[str]]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)):
            continue
        cls = targets.get(node.func.id)
        if cls is None:
            continue
        accepted = _accepted_kwargs(cls)
        if accepted is None:
            continue  # **kwargs catch-all on target
        for kw in node.keywords:
            if kw.arg is None:
                continue  # **dict splat at call site
            if kw.arg not in accepted:
                findings.append(
                    (node.lineno, node.func.id, kw.arg, sorted(accepted))
                )
    return findings


# ──────────────────────────────────────────────────────────────────────
# Primary gate: every dataclass-construction kwarg in main.py resolves
# to a defined field/InitVar on the target dataclass.
# ──────────────────────────────────────────────────────────────────────


def test_main_py_dataclass_construction_kwargs_match_fields() -> None:
    """Every ``Dataclass(X=...)`` keyword in main.py must be accepted by the dataclass.

    Class of bug this catches: a future commit adds
    ``main.py:NNN  Foo(new_kwarg=...)`` without adding ``new_kwarg`` as a
    field on ``Foo``. The process crashes at startup with TypeError, and
    the import-sanity check (``python -c "from trading_corp.main import run"``)
    does NOT catch it (the AttributeError/TypeError fires at construction,
    not at import).
    """
    source = MAIN_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    bindings = _collect_from_imports(tree)
    targets = _collect_dataclass_targets(bindings)
    findings = _find_kwarg_findings(source, targets)

    if findings:
        lines = [
            f"main.py:{lineno}: {name}({kwarg}=...) — kwarg not accepted by "
            f"{name}.__init__. Accepted: {accepted}"
            for lineno, name, kwarg, accepted in findings
        ]
        pytest.fail(
            "Dataclass construction-kwarg mismatch detected on origin/main "
            "(the WebDeps tasty_division-shape class of bug, applied to "
            "source code rather than prod-vs-source):\n\n"
            + "\n".join(lines)
        )


def test_helper_resolves_real_dataclasses_from_main_py_imports() -> None:
    """Sanity: the import-resolution path actually finds real dataclasses.

    Without this, a silent failure mode is "no dataclasses resolved →
    findings list trivially empty → primary test passes meaninglessly."
    Assert at least one well-known dataclass (``WebDeps``) is discoverable.
    """
    source = MAIN_PY.read_text(encoding="utf-8")
    tree = ast.parse(source)
    bindings = _collect_from_imports(tree)
    targets = _collect_dataclass_targets(bindings)
    assert "WebDeps" in targets, (
        "Sanity: main.py imports WebDeps as a dataclass; helper should "
        "have resolved it. If this fails, the import-resolution layer "
        "is broken — primary test may be passing meaninglessly."
    )


# ──────────────────────────────────────────────────────────────────────
# Regression helpers — synthetic targets prove the AST traversal logic
# without mutating any production dataclass.
# ──────────────────────────────────────────────────────────────────────


def test_ast_helper_catches_synthetic_kwarg_mismatch() -> None:
    """Synthetic regression: a ``Foo(missing_field=...)`` construction MUST be caught."""
    SyntheticTarget = dataclasses.make_dataclass(
        "SyntheticTarget",
        [("present_field", int, dataclasses.field(default=0))],
    )
    source = "x = SyntheticTarget(present_field=1, missing_field=42)\n"
    findings = _find_kwarg_findings(source, {"SyntheticTarget": SyntheticTarget})

    assert len(findings) == 1
    lineno, name, bad_kwarg, accepted = findings[0]
    assert name == "SyntheticTarget"
    assert bad_kwarg == "missing_field"
    assert "present_field" in accepted


def test_ast_helper_catches_synthetic_webdeps_minus_field_pattern() -> None:
    """Operator-spec regression: a WebDeps-shaped dataclass missing one field MUST be caught.

    Synthesizes a ``WebDepsMinusTastyDivision`` via ``make_dataclass`` and
    runs the helper against a source string that constructs it with the
    removed field. Confirms the helper would have caught the 22:43 crash
    *if it had been a source-code defect on origin/main* (which it wasn't —
    that's why Item 5's filesystem sweep ships alongside this test).
    """
    WebDepsMinusTastyDivision = dataclasses.make_dataclass(
        "WebDepsMinusTastyDivision",
        [
            ("db_url", str),
            ("mode", str),
            ("logger_agent", object),
        ],
        # NOTE: no ``tasty_division`` field — mimics prod-filesystem state.
    )
    source = (
        "deps = WebDepsMinusTastyDivision("
        "db_url='sqlite:///t.db', "
        "mode='PAPER', "
        "logger_agent=None, "
        "tasty_division=None)\n"
    )
    findings = _find_kwarg_findings(
        source,
        {"WebDepsMinusTastyDivision": WebDepsMinusTastyDivision},
    )

    assert len(findings) == 1
    _lineno, _name, bad_kwarg, _accepted = findings[0]
    assert bad_kwarg == "tasty_division"


def test_ast_helper_passes_when_all_kwargs_resolve() -> None:
    """Regression: when every kwarg corresponds to a field, no findings produced."""
    Target = dataclasses.make_dataclass(
        "AllFieldsTarget",
        [
            ("a", int, dataclasses.field(default=0)),
            ("b", str, dataclasses.field(default="")),
        ],
    )
    source = "x = AllFieldsTarget(a=1, b='hi')\n"
    findings = _find_kwarg_findings(source, {"AllFieldsTarget": Target})
    assert findings == []


def test_ast_helper_skips_kwargs_splat_at_call_site() -> None:
    """Regression: ``Foo(**some_dict)`` is skipped (can't statically validate)."""
    Target = dataclasses.make_dataclass(
        "SplatTarget",
        [("a", int, dataclasses.field(default=0))],
    )
    source = "d = {'b': 2}\nx = SplatTarget(**d)\n"
    findings = _find_kwarg_findings(source, {"SplatTarget": Target})
    assert findings == []  # **d kwargs have arg=None; skipped


def test_ast_helper_skips_dataclass_accepting_var_kwargs() -> None:
    """Regression: a dataclass whose __init__ accepts ``**kwargs`` is all-accepted.

    If we built such a target manually, every kwarg would be valid. The
    helper should skip the call-site entirely.
    """

    @dataclasses.dataclass
    class VarKwargsTarget:
        a: int = 0

        def __init__(self, **kwargs):
            self.a = kwargs.get("a", 0)

    source = "x = VarKwargsTarget(a=1, anything_else=2)\n"
    findings = _find_kwarg_findings(source, {"VarKwargsTarget": VarKwargsTarget})
    assert findings == []


def test_accepted_kwargs_handles_initvars() -> None:
    """Regression: dataclass with ``InitVar`` field is accepted as a kwarg."""

    @dataclasses.dataclass
    class WithInitVar:
        a: int = 0
        flag: dataclasses.InitVar[bool] = False

        def __post_init__(self, flag: bool) -> None:
            if flag:
                self.a += 1

    accepted = _accepted_kwargs(WithInitVar)
    assert accepted is not None
    assert "a" in accepted
    assert "flag" in accepted


def test_ast_helper_skips_attribute_calls() -> None:
    """Regression: ``pkg.Foo(...)`` (Call where func is Attribute) is skipped.

    This is by design — the helper only resolves bare-Name imports. If a
    construction site uses qualified-name access, it's out of scope for
    today. Document the limitation; revisit if main.py adopts the
    qualified-name pattern.
    """
    Target = dataclasses.make_dataclass(
        "AttrTarget", [("a", int, dataclasses.field(default=0))]
    )
    source = "import pkg\nx = pkg.AttrTarget(missing_field=42)\n"
    findings = _find_kwarg_findings(source, {"AttrTarget": Target})
    assert findings == []  # pkg.AttrTarget call has func=Attribute, skipped
