# kalshi_arbitrage → Karen account isolation — DEPLOY RUNBOOK (prod-only)

**Date:** 2026-07-21 ~03:20 UTC · Board-authorized autonomous deploy (drift-gate → deploy → flatcheck → restart → verify; 5th restart of the day). Prod-only per the standing prod≠git pattern (not committed/merged).

## Goal
Isolate `kalshi_arbitrage` onto its own Kalshi account (KV: `KALSHI-KAREN-API-KEY-ID` / `KALSHI-KAREN-PRIVATE-KEY-PEM`). Other kalshi divisions (llm/weather/crypto/copy) stay on the original shared keypair.

## Change — new per-division `secret_ref` mechanism (mirrors bitunix), 3 files
Patched against **prod's drifted content** (prod `secrets.py`/`main.py` were drifted vs git — pulled prod, count-asserted anchored patch, byte-diff = only my additions). Files are LF on prod.
- `trading_corp/utils/secrets.py` — add `KALSHI_KAREN_API_KEY_ID` + `KALSHI_KAREN_PRIVATE_KEY_PEM` to KV fetch list + redact-names + dataclass fields + `load_secrets` populate + `register_redact_literal` on the karen PEM.
- `trading_corp/main.py` `_build_broker_for_division` (kalshi block) — resolve `_k_api_key_id`/`_k_private_key_pem` from `secrets.kalshi_karen_*` when `getattr(division,"secret_ref")=="kalshi_karen"`, else the shared `secrets.kalshi_*`; used in BOTH the read-only KalshiBroker and the KalshiLiveBroker branches.
- `config/divisions.yaml` — `kalshi_arbitrage` block gains `secret_ref: kalshi_karen`.

## md5 (raw, LF)
| File | base (prod pre) | patched (live) |
|---|---|---|
| secrets.py | 093880f0…432 | **e427c8e0…942** |
| main.py | dbffae20…269 | **e5d95225…41b** |
| divisions.yaml | 188794ad…50c | **c8a18f69…6fa** |

## Verification (post-restart, PID 288664, 03:19:54 UTC)
- Boot: "loaded 37 secrets" (+2 Karen), 0 tracebacks, KV fetched both KALSHI-KAREN-* (no SecretNotFound).
- kalshi_arbitrage KalshiBroker connected to **Karen: balance $505.84 / portfolio $2.12** → equity snapshot **$507.96** (n_positions=2) — distinct from shared **$532.84**.
- kalshi_llm_arbitrage **$532.84** (unchanged), kalshi_crypto/weather $500 (unchanged).
- No orders placed (paper/standby/`auto_execute:false` intact); PM route HTTP 200.

## Rollback
- Restore backups: `~/trading_corp/.bak_karen_20260721/{trading_corp_utils_secrets.py.bak, trading_corp_main.py.bak, config_divisions.yaml.bak}` → their live paths + restart.
- Or revert the 3 additions (remove `secret_ref: kalshi_karen`; remove the main.py resolver + arg swaps; remove the secrets.py KAREN fields) + restart.
- Package + prod-file originals + patcher retained locally in `cc/deploy_karen/` (`karen_pkg.b64`, `orig/`, `patch_karen.py`).

## Notes
- web is in-process → this needed a full engine restart (bounces all divisions; RH re-auth self-heals on boot). No further restart-required work queued tonight.
- Memory: [[kalshi-arbitrage-followup-and-commingling-2026-07-21]] records the isolation as a per-division-isolation exception to the shared-account design note (not a bug fix). No edge/prospect characterization.
