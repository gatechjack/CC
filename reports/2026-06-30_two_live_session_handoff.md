# Session Handoff — 2026-06-30 — BitUnix two-live-division cutover COMPLETE

## Baseline (start next session here)
- **git: main == origin == prod-runtime == `9bfd7ff`** (operator-validated matches prod). Use `9bfd7ff`, NOT the intermediate `3534e71`/`5424ecb`. (This handoff + backlog/deploy-log doc commit sits ON TOP of `9bfd7ff` — docs only, runtime files unchanged, so runtime parity holds.)
- cc working checkout is on `tooling-run-capped-python-alias-fix-2026-06-21` (its own branch — fine); `main` lives via worktree/ref.
- Full detail: memory [[bitunix-two-live-phase1]].

## What shipped this session (DONE + LIVE)
**Backlog #27 — 2nd live bitunix division (futures, own funded account) alongside the live SFP division, with per-account reconciler isolation. Phase 1 + Phase 2 COMPLETE + VERIFIED LIVE 2026-06-30.**

- **Phase 1 (code, wired, no cutover)** — merge `5424ecb`. 3 additive files: `secrets.py` (adds `bitunix_sfp` KV account), `bitunix_position_reconciler.py` (optional `division=` → per-account row+audit isolation; `None`=legacy), `main.py` (boot-guard count→**per-secret_ref distinctness**; reconciler ≤1 live=legacy single, ≥2 live=per-division loop). SFP observer+strategy BYTE-UNCHANGED. Full suite == 28F baseline + 13 new tests.
- **Phase 2 (cutover)** — key-separation swap, all operator-run:
  - 2a: `divisions.yaml` bitunix_sfp `secret_ref bitunix_futures→bitunix_sfp` (account-neutral; SFP kept the ORIGINAL account, now via its own `BITUNIX-SFP-*` key). Verified SFP authed, equity $653.61.
  - 2b: operator repointed `BITUNIX-FUTURES-*` KV secret → NEW funded account (Azure portal), IP-bound 168.62.60.79.
  - 2c: `strategies.yaml` bitunix_futures `mode halted→trading` + `execution_mode paper→live`; unit `--live-divisions` += `bitunix_futures` (root, Azure Run Command). Verified.
  - **Result: both divisions LIVE on DISTINCT accounts** — SFP `$653.61` (original, `BITUNIX-SFP-*`) + futures `$118.05` (new, `BITUNIX-FUTURES-*`), two isolated scoped reconcilers (`:bitunix_sfp` / `:bitunix_futures`), no 403.
- **Config parity to main**: `3534e71` (cutover config) + `9bfd7ff` (bitunix_sfp DD-cap 0.99 sync) — main blob md5 == prod verified.
- **Board-approved SFP scale-up** captured in main: `risk_pct_real 0.10 / considerable 0.20 / leverage 25.0` (the "conservative" comments are now stale — cosmetic fix pending).

## Prod state at wrap (read-only confirmed)
Engine PID 13679, NRestarts=0, active, 0 tracebacks. Both reconcilers clean (60s poll). Both divisions FLAT.
**Today's trading (2026-06-30):** futures 4 live BTC-short trades = 1W/3L, net **−$0.21** (first live trades on new acct, clean round-trips). SFP: 0 trades. ★ **Isolation VALIDATED LIVE** — all 4 futures stop-outs fired divergence→auto-book→halt-release scoped `:bitunix_futures`; `:bitunix_sfp` stayed clean. (Only residual: both divisions holding LIVE positions at the same instant — SFP was flat all day.)

## Agent/operator boundary (this session)
ALL agent SSH was **read-only** (verifies / cat / md5sum / journalctl / sqlite SELECT). **Operator ran EVERY prod write/restart** (tl1_apply, hard reboot, tl1_2a_swap, 2c Azure Run Command, KV portal updates). Deploy runners preserved at `deploy/2026-06-30_two_live_cutover/`.

## NEXT SESSION — priorities (operator-decided 2026-06-30, NOT actioned)
1. **★ KILL paper trade + remove SOL/XRP from active config.** Drop SOL+XRP from `bitunix_sfp.symbol_modes` (BTC+ETH stay `arm:trading`/live) → no more `arm:watch` paper rows. Then **expire/clean the 1 stuck SOL paper row** (`e450302a-…`, SOL/USDT.P buy, entry 71.01 / SL 69.609 / TP 73.812, `sfp_real_3m_bos`, opened 06-28). strategies.yaml edit + flat-guarded restart (runner). Operator does NOT want a paper resolver.
   - Root cause of the stuck row: paper-sim RETIRED in two-state collapse (`main.py:1743 _REPLAY_ENABLED=False`); reconciler resolves LIVE rows only → paper watch rows never resolve (all-time 1 fire / 0 resolved). Dead `paper_trade_replay.py` prunable.
2. **★ TUNE SOL SFP (careful, NO overfit) → add live per-coin.** The Mode-B detector SEES SOL SFPs fine (06-28 was a clean 2R fire that would've hit TP by eye) — detection isn't the issue. But that's n=1/eye-selected; the backtest showed no clear SOL edge. Revisit via systematic SOL SFP backtest with beats-null/no-overfit discipline (as the OU/momentum diag this session, [[ou-meanreversion-dead-momentum-skew]]); add SOL (then XRP) LIVE only when a robust edge is confirmed. Go-forward gate: add coins one at a time, each individually tuned. Own focused session.
3. Cosmetic: fix stale bitunix_sfp "conservative" comments (prod+main together to keep parity); scope `auto_book_server_side_close` audit actor.
4. Prune branches `phase2-config-cutover-2026-06-30` + `bitunix-two-live-phase1-2026-06-29` (in main).
5. Standing (pre-existing): first SFP→BOS A/B; cockpit Tier-B wiring; futures TP1→BE ratchet (paper, unbuilt).
