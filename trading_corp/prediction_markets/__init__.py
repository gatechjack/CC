"""Prediction Markets platform package (P1 — data foundation).

Greenfield, standalone-process only: the trading engine never imports this
package and this package never imports the engine. Uses a SEPARATE SQLite
datastore (data/prediction_markets.db) fully isolated from the legacy
trading_corp.db — the isolation is P1's whole safety story (see P1_PLAN §3).

Spec: reports/prediction_markets/P1_PLAN.md, incl. the §3A data-integrity
(negRisk realizedPnl quarantine) amendment.
"""

__all__ = ["db", "category"]
