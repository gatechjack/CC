# Stage 1 — read-only re-baseline after MACE's deploy (2026-08-19). CLEAN.

Ran `pk_stage1_rebaseline_ro.ps1` (RO az run-command). No writes/restart/deploy.

## 1. POST-MACE STATE — poly_kalshi re-armed cleanly
- **Engine PID = 782881** (matches prod-live `4cf6eab`: MACE strike_band_pct deploy `RESTART 775659->782881`, 2026-08-19 05:13 UTC).
- **poly_kalshi_mlb ARMED:** `enabled=True`, `auto_execute=True -> dry_run=False`, `halted=False`, stake $5, loss-cap $100, max_orders 25.
- **prod-live tip = `4cf6eab`** (MACE-only advances since our build: `653a649` weekly-rungs + `bfc81f4`/`4cf6eab` strike-band). No poly_kalshi commits.
- Boot log @ 05:13:15 (MACE restart) re-armed us: `Poly->Kalshi MLB copy WIRED (auto_execute=True -> dry_run=False, stake=$5.0, halt=$100.0)` + `roster invariant OK: 2 live / 4 paper wallet(s), disjoint`.

## 2. RE-BASELINE THE 3 DEPLOY FILES — box == PRE-FIX, zero MACE drift
| file | box LF-md5 (now) | expected PRE-FIX (827bea9) | match |
|---|---|---|---|
| poly_kalshi_executor.py | `d1f871f9c3e83530dc6fba3bd58c2eae` | `d1f871f9c3e83530dc6fba3bd58c2eae` | ✅ |
| mlb_poly_kalshi_match.py | `4b2a5c49fb737d54d5a964868a4cd9fa` | `4b2a5c49fb737d54d5a964868a4cd9fa` | ✅ |
| brokers/kalshi.py | `18626cf0ddcdf6c3663be7d9602abbba` | `18626cf0ddcdf6c3663be7d9602abbba` | ✅ |

**MACE did not touch any of the 3 files** — all byte-identical (LF) to the pre-fix versions our deploy overwrites. The box's poly_kalshi runtime matches our branch base. Confirmed against the deployed box (not the pre-MACE state).

Deploy targets (NEW blobs, for Stage-2 post-install verify):
- executor `257f6433b4e7d5144cfc6eaae88a7552` · matcher `7c191e830b7222cfc59f51cf8c871c97` · kalshi.py `7fb2688f39b9fa3d425e1e0136ee6c3c`.

## 3. NO POLY_KALSHI DRIFT FROM MACE
- **live_whales = 2** (intact — SDTrading + xifutloong3); configured roster (`poly_kalshi_mlb/live_whales`) = 2; paper `selected_whales` = 4. Matches the 08-18 post-demote state.
- **Mark tables present** (`poly_kalshi_mark_live`/`_history`), 0 rows (poller never marks — the Item 2 bug; not a MACE effect).
- Lifetime placed-order rows = 25. Most-recent mark ticks (03:13/03:16): `open=1 marked=0 quote_miss=1` — **Item 2 bug still live on the box** (confirms the fix is still needed).
- Division is exactly as we left it, re-armed under a new PID. No file changed, no state migrated by MACE.

## 4. STAGE-2 SEQUENCE PLAN — two INDEPENDENT deploys
Item 1 files = {`poly_kalshi_executor.py`, `mlb_poly_kalshi_match.py`}; Item 2 file = {`brokers/kalshi.py`}. **Disjoint** → truly independent; deploy separately with a full verify + prod-live advance between.

**Order: Item 2 first (lower-risk, cosmetic, warms the path) → then Item 1 (placement gate, solo careful).** Each deploy:
1. drift-gate the file(s) vs box (confirm still == pre-fix immediately before install),
2. backup (`.bak_<item>_<ts>`),
3. install (file-overwrite, chunked base64 `@file`),
4. LF-md5 verify (box-new == the NEW md5 above),
5. restart (az root ~2.5min boot; AVOID 15:40-15:58 ET MACE window),
6. verify re-arm (WIRED + roster invariant `2 live / 4 paper` + `halted=False`, PID changed),
7. advance prod-live (commit exact deployed blobs, FF-push; message = deploy log).

Post-deploy confirms: **Item 2** → `poly_kalshi mark tick` shows `marked>0` (vs today's all-`quote_miss`); **Item 1** → a live opposite-side signal logs `skip_conflict` (event-driven).

## VERDICT: CLEAN — box re-baselined, no MACE drift, poly_kalshi intact + armed. Ready for Stage 2 on your go.
