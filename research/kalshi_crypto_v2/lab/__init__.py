"""kalshi_crypto_v2 model lab (Phase 2). READ-ONLY research; no order surface.

Harness/framework layer (S2): dual EV (taker + maker w/ fill rate), probability
calibration vs market, correlation-aware fractional Kelly, Breeden-Litzenberger
ladder consistency, train/holdout discipline + flat-window rule. Data (S3),
model v1 (S4), baselines (S5) build on these. All historical modeling data lives
in a SEPARATE lab sqlite (research/kalshi_crypto_v2/lab/kcv2_lab.db); prod
trading_corp.db is untouched.
"""
