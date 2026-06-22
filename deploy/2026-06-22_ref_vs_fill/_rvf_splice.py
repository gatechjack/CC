"""Splice the ref-vs-fill capture hunk onto PROD's actual observer blob
(e88a7abc, which carries D4 — absent from this branch's base) so the deploy
preserves D4. Verifies the result == prod + exactly my 8-line hunk, and ==
worktree-HEAD observer + exactly the D4 block. Pure local; no prod contact."""
import hashlib
import subprocess

PROD = "deploy/_prod_observer.py"       # fetched read-only, md5==e88a7abc
OUT = "deploy/_target_observer.py"

ANCHOR = '            record.extra["entry_role"] = str(getattr(fill, "role", "") or "")\n'
HUNK = (
    '            # ref-vs-fill (2026-06-22): stamp the ACTUAL entry fill price (the\n'
    '            # broker-observed VWAP from the signed fill, NOT the alert/reference\n'
    '            # price) so close-side PnL books from the real fill. Only when known\n'
    '            # (>0); else omit so the PnL reader falls back to\n'
    '            # entry_reference_price (paper rows / unknown fills book at the ref).\n'
    '            _aefp = float(getattr(fill, "price", 0.0) or 0.0)\n'
    '            if _aefp > 0:\n'
    '                record.extra["actual_entry_fill_price"] = _aefp\n'
)

with open(PROD, "rb") as f:
    prod = f.read().replace(b"\r\n", b"\n")
assert hashlib.md5(prod).hexdigest() == "e88a7abca643f2048facfcb19a6c559b", "prod md5 drift!"

text = prod.decode("utf-8")
n = text.count(ANCHOR)
assert n == 1, f"anchor occurrences = {n} (want exactly 1)"
spliced = text.replace(ANCHOR, ANCHOR + HUNK)
spliced_b = spliced.encode("utf-8")
with open(OUT, "wb") as f:
    f.write(spliced_b)

target_md5 = hashlib.md5(spliced_b).hexdigest()
print("prod   observer md5 :", hashlib.md5(prod).hexdigest())
print("TARGET observer md5 :", target_md5)
print("delta vs prod (lines):", len(spliced.splitlines()) - len(text.splitlines()), "(want 8)")

# Cross-check: spliced minus my worktree-HEAD observer should == ONLY the D4 block
# (i.e. the spliced target == prod+hunk, and worktree-HEAD == base+hunk; their
# difference is exactly D4, which prod has and the branch base does not).
wt_head = subprocess.check_output(
    ["git", "show", "HEAD:trading_corp/agents/divisions/bitunix_futures_observer.py"]
).replace(b"\r\n", b"\n").decode("utf-8")
assert HUNK in wt_head, "hunk missing from worktree HEAD (unexpected)"
assert HUNK in spliced, "hunk missing from spliced target (splice failed)"
# the spliced target must contain the D4 marker that the branch base lacks
assert "_concurrent_position_guard_verdict" in spliced, "D4 lost in splice!"
print("OK: hunk present in target, D4 preserved in target")
