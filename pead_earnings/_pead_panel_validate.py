"""Pre-restart validation for the PEAD panel + scan_evaluation edits. Runs in a THROWAWAY process on
prod (engine untouched). Exercises: (V2) importing the edited pead_strategy (new imports resolve),
(V3a) the isolated-DB reader against the real earnings_watch.db, (V3b) build_pead_view read-only +
rendering the real partial template. Any exception -> exit 1 -> DO NOT restart."""
import asyncio
import sys
import traceback
from types import SimpleNamespace

sys.path.insert(0, "/home/azureuser/trading_corp")


def main() -> None:
    # V2 — the edited live strategy must import cleanly (new imports: _percentile, passes_screen,
    # insert_scan_evaluation). A bad import would crash the engine on restart.
    import trading_corp.agents.strategies.pead_strategy  # noqa: F401
    print("V2 IMPORT_pead_strategy_OK")

    from trading_corp.web import pead_view
    # V3a — isolated watcher DB reader (mode=ro) against the real DB.
    up = pead_view.query_upcoming_earnings()
    print("V3a reader.available=%s watchlist=%d reported=%d stale=%s stats=%s"
          % (up.get("available"), len(up.get("watchlist") or []),
             len(up.get("reported") or []), up.get("stale"), up.get("stats")))

    # V3b — full view (read-only; data_exec=None => no broker calls) + render the real partial.
    deps = SimpleNamespace(db_url="sqlite:///data/trading_corp.db", data_exec=None)
    view = asyncio.run(pead_view.build_pead_view(deps))
    print("V3b view_has_upcoming=%s funnel=%s" % ("upcoming" in view, view.get("funnel")))
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(
        directory="/home/azureuser/trading_corp/trading_corp/web/templates")
    # The engine registers custom filters on app.state.templates (app.py). This throwaway env lacks
    # them; stub them so the render completes — our panel section uses plain '%'-formatting, no filters,
    # so this faithfully validates our additions' structure + the real `view` data.
    for _fn in ("money", "money_signed", "strike", "pct", "pct_signed", "compact_num",
                "et_hms", "et_short", "et_full"):
        templates.env.filters[_fn] = lambda x, *a, **k: x
    html = templates.get_template("partials/pead_live_sections.html").render(v=view, request=None)
    print("V3b RENDER_OK bytes=%d has_panel=%s has_sue_plausibility=%s"
          % (len(html), "Upcoming Earnings" in html, "SUE plausibility" in html))


if __name__ == "__main__":
    try:
        main()
        print("VALIDATE_ALL_OK")
    except Exception:
        traceback.print_exc()
        sys.exit(1)
