# Next-session handoff — 2026-06-08/09 prod-health investigation (Threads A+B+C complete)

**Verified UTC at write:** 2026-06-09T01:39:29Z (via `date -u`).
**Session:** operator-supervised, read-only prod, `az vm run-command` control plane (SSH:22
blocked on hotel WiFi+VPN; operator still travelling). No prod writes, no code changes.

## Branch state
- **origin/main = `70d50f7`** at investigation close (3 findings filed below); this handoff is
  the next commit on top.
- **Investigation branch `bitunix-silence-investigation-2026-06-08` = `10a8bfd`** on origin
  (audit trail, unmerged per CLAUDE.md). Full detail + read-only probe scripts + paste-safe
  `.cmd` az wrappers in `reports/2026-06-08_bitunix_silence_investigation/` (FINDINGS.md is the
  narrative).

## Three findings filed tonight (all MERGED to origin/main; all FIXES unstarted)
1. **P1 — Bitunix volatility classifier bug** (`81e6169`). `_atr_pct_to_tier`
   (`bitunix_htf_regime.py:725-737`) ignores config `extreme:5.0%` and treats BTC 1D ATR ≥3.0%
   as Extreme → `size_multiplier=0.0` → **zero fires since 2026-06-02 22:15 UTC**. Pre-existing
   (`9e1b527`, 2026-05-14), NOT a deploy regression. **Blocks the live-flip decision.**
2. **P2 — Robinhood session auth dead** (`b2259a0`). `~/.tokens/robinhood.pickle` stale since
   2026-05-29; RH API 401 across PMCC/IRA/joint (seen 06-09 00:09Z) → $0 dashboards via
   `broker_fallback_to_paper`. NOT a restart (uptime ~7d, NRestarts=0). Paper-exec → no capital
   risk. This was the real story behind the "Robinhood pickle reset" concern.
3. **P3 — Reconciler intrabar advanced-SL variance** (`70d50f7`). Chronic 1m-OHLC path
   ambiguity (advanced SL checked next-bar, `paper_trade_replay.py:503-574`); 3 trades flagged
   (c8f25d17/ac5f9c59/c2eb7cda). NOT a regression; recorded is authoritative; low priority.

## Open items requiring operator action
- [ ] **Bitunix volatility classifier fix** — scope + **backtest first** (CLAUDE.md §4 strategy-
      parameter gate). Decide intended `extreme` threshold (config says 5.0%; code uses 3.0%).
- [ ] **Robinhood interactive re-login** to regenerate `~/.tokens/robinhood.pickle` (~5 min,
      requires MFA — cannot be agent-resolved).
- [ ] **sharp_edges.md** — add the advanced-SL intrabar reconciler-variance entry (only the
      original-SL tie is documented today).
- [ ] **Mark the 3 reconciler-mismatched trades `audit_corrected`** (recorded authoritative;
      `audit_reality_reconciler.py:189-202`) so the dashboard RECONCILER-MISMATCH tile clears.
      Prod write.

## Phase 3 paper observation window — INVALIDATED
The 2026-06-02 → 2026-06-09 window is **invalidated by the P1 finding** (Bitunix dormant 6 of 7
days). The Day-7 close-out (2026-06-09) **cannot** produce a flip-readiness verdict. A **fresh
observation window is required post-fix** before any `execution_mode: paper → live` decision.

## Operational notes
- **NSG rule `temp-vpn-trip-until-2026-06-19` still active** — delete when operator's travel
  ends (target ~2026-06-19). Operator/infra action, not from this repo.
- **Incidental, NOT filed** (surfaced in Thread B, out of scope): recurring external-feed
  WARNINGs ~every 10 min — `apify` 403 (bad/missing `APIFY_API_TOKEN`), `odds_api` 401
  (and the API key is printed in plaintext in the log URL — redaction-filter gap), polymarket
  `fetch_activity` timeouts. Operator to decide whether to file.

## Recommended first action next session
**Operator-led scoping of the Bitunix volatility-classifier fix** (P1) — start with the
Backtester approval gate: backtest the intended `extreme` threshold (≥5.0%) against historical
BTC to confirm a sane trade-eligible regime distribution, THEN make the code change. This is the
structural blocker for the live-flip; everything else is independent.

---
*Threads: A (Bitunix silence → P1), B (no restart; RH auth dead → P2), C (reconciler variance →
P3). All read-only, operator-run prod queries. Investigation branch `10a8bfd` on origin.*
