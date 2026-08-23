"""pm_web -- standalone Prediction Markets web app (P2).

NO engine imports (never trading_corp.web / main / agents), NO WebDeps, NO agent handles.
Reads/writes ONLY data/prediction_markets.db. Its own uvicorn process (scripts/pm_web.py).
Spec: reports/prediction_markets/P2_PLAN.md §3.1.
"""
