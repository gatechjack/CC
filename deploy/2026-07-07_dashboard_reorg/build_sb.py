"""Reconcile _state_board.html: swap the working-tree .strip block onto prod-live.

Prod carries a prod-only 'regime' badge line (not on main), so we swap ONLY the
.strip block (drop pxtag + add entry/TP/stop price labels) — extracted straight
from our working-tree template — into the fetched prod version. No hardcoded
hunk, so label tweaks only require editing the template + re-running this. LF.
"""
PROD = "deploy/2026-07-07_dashboard_reorg/prod_state_board.html"
MINE = "trading_corp/web/templates/sfp_cockpit/_state_board.html"
OUT = ("deploy/2026-07-07_dashboard_reorg/staged/trading_corp/web/"
       "templates/sfp_cockpit/_state_board.html")


def strip_block(s):
    """Extract the `<div class="strip"> … </div>` block (4-space-indented open
    to its 4-space-indented close — inner tags are deeper-indented)."""
    start = s.index('    <div class="strip">')
    end = s.index('\n    </div>', start) + len('\n    </div>')
    return s[start:end]


prod = open(PROD, encoding="utf-8").read().replace("\r\n", "\n")
mine = open(MINE, encoding="utf-8").read().replace("\r\n", "\n")
old = strip_block(prod)
new = strip_block(mine)
assert old in prod, "prod .strip block not found"
assert new != old, "working-tree .strip block identical to prod (nothing to apply)"
out = prod.replace(old, new, 1)
open(OUT, "wb").write(out.encode("utf-8"))
print("reconciled _state_board.html written (LF); .strip block swapped from working tree")
