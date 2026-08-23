#!/usr/bin/env python3
"""Launch pm_web (uvicorn). Standalone: NO WebDeps, NO engine imports; reads only prediction_markets.db.

On the box it runs via the systemd unit (prediction-markets-web.service). Locally / manually:
  PYTHONPATH=. venv/bin/python trading_corp/scripts/pm_web.py
Host/port from PM_WEB_HOST / PM_WEB_PORT (default 127.0.0.1:8081 -- loopback-only, so pm_web is reachable ONLY
via Caddy+Authelia, never directly, unlike the engine dashboard on 0.0.0.0:8000).
Spec: reports/prediction_markets/P2_PLAN.md §3.1, §12.
"""
from __future__ import annotations

import os


def main() -> int:
    import uvicorn  # lazy: offline unit tests import the app directly, never this launcher
    host = os.environ.get("PM_WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("PM_WEB_PORT", "8081"))
    uvicorn.run("trading_corp.prediction_markets.web.app:app", host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
