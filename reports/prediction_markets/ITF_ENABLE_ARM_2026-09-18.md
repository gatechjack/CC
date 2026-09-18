# ITF ENABLE + ARM -- LIVE-OPS LEDGER (2026-09-18)

Task: enable + arm the `itf` category on BOTH accounts (kalshi_jack + kalshi_karen). ITF code was deployed
INERT 2026-09-16 (`ITF_DEPLOY_MANIFEST_2026-09-16.md`); this ledger records the enable + arm.

## Base / fork
- **origin/prod-live = `2362db46`** (today's MACE trigger-mid-anchor deploy), NOT `546151ea` as the brief stated.
  ITF code is carried forward unchanged in it. Worktree `cc-wt-itf-enable` @ `pm-itf-enable-arm-2026-09-18`
  is based on `2362db46`; the box matches.
- This task changes NO code (ITF ships on prod-live already). It is guarded DB writes only:
  (a) append `itf_moneyline` to each ITF sub's `market_types`; (b) arm the two ITF sub scopes. Read per cycle;
  NO restart, NO migration (schema head 24).

## STEP 0 -- ATTACH vs BOOT (roster is read AT BOOT, attachment-gated) -- ANSWERED
- Current engine PID **467792**, ExecMainStartTimestamp **Fri 2026-09-18 05:38:04 UTC** (epoch 1789709884).
- ITF subs created + attached BEFORE that boot:
  - kalshi_jack: `created_ts=added_ts=1789709461` (~2026-09-18 05:31:01 UTC), actor jack, promote_to_live, active=1.
  - kalshi_karen: `created_ts=added_ts=1789709477` (~2026-09-18 05:31:17 UTC), actor jack, promote_to_live, active=1.
- Attach (05:31) precedes boot (05:38) by ~7 min -> **the ITF subs ARE in the running roster.**
  **NO RESTART REQUIRED** (unlike the cs2 attach-after-boot starvation case).

## STEP 1 -- ATTACHED WALLET + does it bet ITF -- CONFIRMED
- Wallet on BOTH accounts: **`0x746295855be6f49fe2da2accd69ea34b3cf23ac3`** (active=1).
- Public /positions (complete book, open=15): **3 live ITF-slug positions**, all dated 2026-09-18, all M25 MEN's:
  - `itf-marchi1-chepel1-2026-09-18` -- M25 Santa Margherita di Pula: Andrea De Marchi vs Andrey Chepelev; outcome=Andrea De Marchi; size=1788.9
  - `itf-maxted1-tabacc1-2026-09-18` -- M25 Sintra: Lui Maxted vs Fausto Tabacco; outcome=Fausto Tabacco; size=781.25
  - `itf-schoen1-bertra2-2026-09-18` -- M25 Sintra: Patrick Schoen vs Robin Bertrand; outcome=Robin Bertrand; size=328.7
- The whale actively bets ITF (men's / KXITFMATCH). First fill is most likely a men's match; watch the SERIES.

## STEP 2 -- RESOLVED CONFIG via sub_config_from_row (NOT raw column)
- Both ITF subs: **sizing_mode='fixed', fixed_stake_usd=$5.0** (raw NULL -> CONFIG_DEFAULTS), contracts=5 (IGNORED
  under 'fixed'), per_order_usd_cap=**$25** (raw NULL -> default; siblings are explicit $50), daily=$50, max_open=$100,
  max_orders/day=25, max_slippage=2c, liquidity_ratio=0.75, market_types RESOLVED = `moneyline,total,spread` (NO
  `itf_moneyline` -> INERT).
- **cfb TRAP present:** 'fixed'/$5 => first fill = floor($5/price) contracts (price-dependent), NOT flat 5.
- Siblings: jack/wta & karen/wta = contracts/5; jack/mlb contracts/10; karen/mlb contracts/3; jack/atp contracts/2;
  karen/atp contracts/1; **jack/tennis = fixed/$5** (precedent for fixed on a tennis-family sub).
- Cap interaction: notional ~= $5 (fixed) or <= 5*$0.99 ~= $4.95 (contracts/5) << $25 cap -> clears, wide headroom,
  no pre-submit-reject risk.
- **DECISION PENDING (Jack): keep 'fixed'/$5, or normalize to 'contracts'/5 for flat, price-independent sizing.**

## STEP 3 -- ARM PRE-baseline (byte-unchanged proof anchor)
- Exactly **31 armed / 0 latched** pm_live arm:* rows; `arm:global` armed (by r8_arm, 2026-08-31).
- Both ITF keys **ABSENT** (cold-start) -> arm() creates them without --clear-latch.
- Baseline md5 of the 31 non-ITF rows (key\tvalue_json, sorted) = **`c59a621d758a373c175e02895b4577f2`**
  -- must be IDENTICAL after the arm write (proves the 31 + global untouched, original timestamps preserved).

## PLAN (each a separate authorization)
1. (optional, Jack's ruling) sizing normalize sizing_mode='contracts' contracts=5 on both ITF subs (guarded, backup).
2. ENABLE: append `itf_moneyline` -> `moneyline,total,spread,itf_moneyline` on both ITF subs; POST proves the
   RESOLVED config via sub_config_from_row + every other sub byte-unchanged.
3. Pre-arm: shard-3 balance_breakdown (per-shard, NOT masked total) funded; cap clears (done above).
4. ARM: live-arm kalshi_jack:itf + kalshi_karen:itf; global untouched; 31 rows byte-unchanged (md5 above); fail-closed PRE.
5. FIRST-FILL watch: full read-back both players + leg + SERIES (men KXITFMATCH vs women KXITFWMATCH) from the
   Kalshi title; wrong player/leg/series -> IMMEDIATE global disarm (fire first).
