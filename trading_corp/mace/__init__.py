"""MACE — Multi-Asset Condor Engine (robinhood_mace division).

Zero-HITL defined-risk iron condors on liquid ETFs. Plan of record:
`planning/mace_v1_plan.md` (Board-approved 2026-08-09, 7 rulings +
Amendment A2026-08-09 joint-account takeover).

Import boundary (plan § Architecture): only `mace.rh_broker` may import
`trading_corp.brokers.*`; `strategy.py` / `manager.py` import only
`mace.*`, stdlib, and injected callables.
"""
