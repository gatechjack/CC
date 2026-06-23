"""Offline render test for the win-rate Paper/Live toggle panel in the staged
division.html. Validates (1) full-file Jinja syntax, (2) the three branch
cases render correctly without a live server. Run: python render_test.py"""
import os
import jinja2

PATH = os.path.join(os.path.dirname(__file__),
                    "stage", "trading_corp", "web", "templates", "division.html")
src = open(PATH, encoding="utf-8").read()
env = jinja2.Environment()

# 1) full-file syntax / tag-balance
env.parse(src)
print("FULL-FILE PARSE OK")

# 2) isolate the win-rate panel block and render it standalone
head = src.split("<!-- Recent activity -->")[0]
block = head[head.rfind("{% set ps = view.paper_trade_summary %}"):]
tmpl = env.from_string(block)


def totals(n, w, l, e=0, wr=None, pnl=0.0, prea=0):
    cell = {"n": n, "wins": w, "losses": l, "expired": e, "open": 0,
            "win_rate_pct": wr, "sim_pnl": pnl, "n_pre_phase_a": prea}
    return {k: dict(cell) for k in ("7d", "30d", "all")}


def render(ps):
    view = type("V", (), {"paper_trade_summary": ps})
    return tmpl.render(view=view)


# CASE A — bitunix today: paper has history, live epoch-scoped slice empty
A = render({"totals": totals(154, 105, 49, 3, 68.0, -1.2, prea=2),
            "live_totals": totals(0, 0, 0, 0, None, 0.0),
            "has_live": False,
            "metrics_epoch": "2026-06-23T01:17:17.921942+00:00"})
assert "Win rate" in A and 'data-wr-tab="live"' in A and 'data-wr-tab="paper"' in A
assert "No live trades resolved since 2026-06-23 yet." in A
assert "all-time · signal replay, not epoch-scoped" in A
assert 'data-wr-slice="paper" hidden' in A          # paper hidden by default
assert "105W / 49L" in A                             # paper grid present
print("CASE A (epoch set, empty live, default LIVE)        OK")

# CASE B — a live division with resolved post-epoch trades
B = render({"totals": totals(154, 105, 49, 3, 68.0, -1.2),
            "live_totals": totals(14, 4, 10, 0, 28.6, -0.5),
            "has_live": True,
            "metrics_epoch": "2026-06-23T01:17:17+00:00"})
assert 'data-wr-tab="live"' in B and "4W / 10L" in B and "29%" in B
assert "since 2026-06-23 · current logic only" in B
print("CASE B (live slice populated)                       OK")

# CASE C — paper-only division (kalshi/polymarket): NO toggle, unchanged look
C = render({"totals": totals(100, 60, 40, 0, 60.0, 5.0),
            "live_totals": totals(0, 0, 0, 0, None, 0.0),
            "has_live": False,
            "metrics_epoch": None})
assert "Paper-trade win rate" in C            # old title preserved
assert "data-wr-tab" not in C                 # no toggle
assert 'data-wr-slice="live"' not in C        # no live sub-view
assert "would-have-placed alerts replayed" in C
print("CASE C (paper-only division, no toggle)             OK")

print("\nALL RENDER TESTS PASSED")
