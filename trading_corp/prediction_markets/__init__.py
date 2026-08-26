"""Prediction Markets platform package (P1 — data foundation).

Greenfield, standalone-process only: the trading engine never imports this
package and this package never imports the engine. Uses a SEPARATE SQLite
datastore (data/prediction_markets.db) fully isolated from the legacy
trading_corp.db — the isolation is P1's whole safety story (see P1_PLAN §3).

Spec: reports/prediction_markets/P1_PLAN.md, incl. the §3A data-integrity
(negRisk realizedPnl quarantine) amendment.

REQUIREMENTS / ANTI-DRIFT: reports/prediction_markets/PM_REQUIREMENTS.md is the
durable requirements artifact -- the three lists and their three data bases, the
canonical vocabulary, and standing build requirements. Read it before touching this
package. Handoffs that carried SHAs but dropped the product description are what
produced the 2026-08 rebuild; this pointer exists so the requirements are reachable
from the code, not only the reports directory.
"""

__all__ = ["db", "category"]
