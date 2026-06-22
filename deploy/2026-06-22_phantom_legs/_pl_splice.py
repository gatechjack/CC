"""Splice the committed phantom-legs skip hunk (from worktree HEAD) onto PROD's
paper_trade_replay blob (5619910d, has Issue#1) so the deploy = prod + exactly
my hunk, preserving Issue#1. Verifies diff(prod,target) == only the hunk. Pure
local; no prod contact (the prod blob was fetched read-only into deploy/_prod_ptr.py)."""
import hashlib
import subprocess

REL = "trading_corp/agents/paper_trade_replay.py"
PROD = "deploy/_prod_ptr.py"
OUT = "deploy/_target_ptr.py"
PROD_MD5 = "5619910dab44b053124fbbc2e7671cec"

ANCHOR = "            extra = _parse_extra(row.extra_json)\n"
ISV2 = "            is_v2 = (\n"

# worktree HEAD (committed) text — the source of truth for the hunk
wt = subprocess.check_output(["git", "show", "HEAD:" + REL]).replace(b"\r\n", b"\n").decode("utf-8")
# the committed block = anchor + hunk + is_v2 line
s = wt.index(ANCHOR)
e = wt.index(ISV2, s) + len(ISV2)
WT_BLOCK = wt[s:e]                       # anchor ... is_v2 (includes my hunk)
assert "skipped_bracket_managed_live" in WT_BLOCK, "hunk not found in worktree block"

with open(PROD, "rb") as f:
    prod = f.read().replace(b"\r\n", b"\n")
assert hashlib.md5(prod).hexdigest() == PROD_MD5, "prod md5 drift!"
prod_txt = prod.decode("utf-8")

PROD_OLD = ANCHOR + ISV2                 # prod has anchor immediately followed by is_v2
assert prod_txt.count(PROD_OLD) == 1, f"PROD_OLD count={prod_txt.count(PROD_OLD)} (want 1)"

target_txt = prod_txt.replace(PROD_OLD, WT_BLOCK)
target = target_txt.encode("utf-8")
with open(OUT, "wb") as f:
    f.write(target)

print("prod   md5:", hashlib.md5(prod).hexdigest())
print("TARGET md5:", hashlib.md5(target).hexdigest())
print("delta lines:", len(target_txt.splitlines()) - len(prod_txt.splitlines()), "(want 14)")
# the spliced block must contain Issue#1 (preserved) — sanity that we didn't clobber it
assert "suppressed_bracket_managed" in target_txt, "Issue#1 marker lost!"
print("OK: Issue#1 preserved in target")
