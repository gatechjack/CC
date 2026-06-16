"""Proves the E1 deploy additive-guard's setuptools exception is SCOPED.

Targets pm_e1_lock_diff.classify (the read-only checker); deploy_e1_lock.sh's inline
guard mirrors the same ALLOWED_CHANGES set + the same allow/abort branching, so these
cases cover the deploy guard's logic too. Loaded by file path because
deploy/polymarket_e1/ is a deploy-artifact dir, not an importable package.
"""
import contextlib
import importlib.util
import io
import pathlib
import types

_MOD_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "deploy" / "polymarket_e1" / "pm_e1_lock_diff.py"
)
_spec = importlib.util.spec_from_file_location("pm_e1_lock_diff", _MOD_PATH)
pm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pm)  # side-effect-free: I/O is under __main__

# A minimal lock that exercises the parser: comment, --hash continuation lines, and
# pins for a new E1 package, two already-satisfied packages, and the setuptools target.
LOCK = [
    "# E1 dependency lock (excerpt)",
    "--require-hashes",
    "py-clob-client==0.17.5 \\",
    "    --hash=sha256:deadbeef",
    "web3==6.11.0 \\",
    "anthropic==0.39.0 \\",
    "setuptools==80.10.2 \\",
    "    --hash=sha256:8b0e9d10",
]


def _baseline(setuptools_ver="82.0.1"):
    # web3 + anthropic already installed at the lock versions (the "freeze" baseline);
    # setuptools at 82.0.1 (what prod actually has); py-clob-client absent (to be added).
    return {"web3": "6.11.0", "anthropic": "0.39.0", "setuptools": setuptools_ver}


def test_intended_setuptools_downgrade_is_allowed_not_changed():
    new, changed, allowed, same = pm.classify(_baseline("82.0.1"), LOCK)
    assert "py-clob-client==0.17.5" in new          # E1 package is additive
    assert allowed == ["setuptools: 82.0.1 -> 80.10.2"]
    assert changed == []                            # nothing forces an abort
    assert same == 2                                # web3 + anthropic unchanged


def test_other_changed_package_still_aborts():
    base = _baseline("82.0.1")
    base["anthropic"] = "0.30.0"                    # unrelated drift
    new, changed, allowed, same = pm.classify(base, LOCK)
    assert changed == ["anthropic: 0.30.0 -> 0.39.0"]   # real drift -> would abort
    assert allowed == ["setuptools: 82.0.1 -> 80.10.2"] # exception doesn't mask it


def test_nonmatching_setuptools_baseline_still_aborts():
    # prod on a DIFFERENT setuptools baseline -> the exact-tuple exception must NOT apply
    new, changed, allowed, same = pm.classify(_baseline("79.0.0"), LOCK)
    assert changed == ["setuptools: 79.0.0 -> 80.10.2"]
    assert allowed == []


def test_nonmatching_setuptools_target_still_aborts():
    # lock targets a DIFFERENT setuptools version -> exception must NOT apply
    lock = [l for l in LOCK if not l.startswith("setuptools==")] + ["setuptools==81.0.0 \\"]
    new, changed, allowed, same = pm.classify(_baseline("82.0.1"), lock)
    assert changed == ["setuptools: 82.0.1 -> 81.0.0"]
    assert allowed == []


def test_allowlist_is_an_exact_tuple_not_blanket():
    assert ("setuptools", "82.0.1", "80.10.2") in pm.ALLOWED_CHANGES
    assert ("setuptools", "82.0.1", "81.0.0") not in pm.ALLOWED_CHANGES  # wrong target
    assert ("setuptools", "70.0.0", "80.10.2") not in pm.ALLOWED_CHANGES  # wrong baseline
    assert len(pm.ALLOWED_CHANGES) == 1


# --- deploy_e1_lock.sh's inline guard (a SEPARATE self-contained copy) ---
# Run the actual heredoc bytes from the script, neutralizing only its two prod
# couplings (live importlib.metadata + the hardcoded /tmp lock path). This catches
# any divergence between the two guard copies, and asserts the abort exit code (3)
# and the loud-log line, which the pure-function tests above cannot.
_DEPLOY = (
    pathlib.Path(__file__).resolve().parents[1]
    / "deploy" / "polymarket_e1" / "deploy_e1_lock.sh"
)


def _run_deploy_guard(installed, lock_lines):
    text = _DEPLOY.read_text(encoding="utf-8")
    start = text.index("<<'PY'\n") + len("<<'PY'\n")
    end = text.index("\nPY\n", start)
    src = text[start:end]
    # neutralize prod couplings ONLY — the exception logic is run verbatim
    src = src.replace("from importlib import metadata\n", "")
    src = src.replace('open("/tmp/requirements.lock")', "io.StringIO(__LOCK__)")
    assert "ALLOWED_CHANGES = {(\"setuptools\", \"82.0.1\", \"80.10.2\")}" in src
    dists = [types.SimpleNamespace(metadata={"Name": n}, version=v)
             for n, v in installed.items()]
    g = {
        "io": io,
        "metadata": types.SimpleNamespace(distributions=lambda: dists),
        "__LOCK__": "\n".join(lock_lines),
        "__name__": "deploy_guard",
    }
    out, rc = io.StringIO(), 0
    try:
        with contextlib.redirect_stdout(out):
            exec(compile(src, str(_DEPLOY), "exec"), g)
    except SystemExit as e:
        rc = e.code or 0
    return rc, out.getvalue()


def test_deploy_guard_allows_intended_downgrade_exit0():
    rc, out = _run_deploy_guard(
        {"web3": "6.11.0", "anthropic": "0.39.0", "setuptools": "82.0.1"}, LOCK)
    assert rc == 0
    assert "ALLOWED EXCEPTION" in out
    assert "setuptools: 82.0.1 -> 80.10.2" in out
    assert "NON-ADDITIVE" not in out


def test_deploy_guard_aborts_on_other_change_exit3():
    rc, out = _run_deploy_guard(
        {"web3": "6.11.0", "anthropic": "0.30.0", "setuptools": "82.0.1"}, LOCK)
    assert rc == 3
    assert "anthropic: 0.30.0 -> 0.39.0" in out


def test_deploy_guard_aborts_on_nonmatching_setuptools_exit3():
    rc, out = _run_deploy_guard(
        {"web3": "6.11.0", "anthropic": "0.39.0", "setuptools": "79.0.0"}, LOCK)
    assert rc == 3
    assert "setuptools: 79.0.0 -> 80.10.2" in out
