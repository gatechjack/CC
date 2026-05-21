# Next-session pickup — after Polymarket per-`condition_id` cap ship

*Written 2026-05-21 ~12:40 UTC at the end of the session that diagnosed the polymarket_arbitrage performance cliff and shipped the cap.*

*This file is the polymarket-specific pickup. The IC v1 pickup at [session_start_2026_05_22.md](session_start_2026_05_22.md) is separate and still current for that workstream.*

---

## TL;DR

- **Polymarket arbitrage per-`condition_id` position cap is LIVE on prod in paper mode as of 2026-05-21 12:28:07 UTC** (commit `c2b0e12`). Default `max_open_per_condition_id: 1` (true dedupe). Strategy stays paper-only — `auto_execute: false`, `enabled: true` — both UNCHANGED by this ship.
- **First `polymarket_dedupe_skipped` audit fired at 12:33:51 UTC** on the WTI HIGH $115 stack (12 priors → refused entry #13). Cap is biting against the in-flight overhang exactly as the addendum §1 predicted.
- **Underlying strategy has no demonstrated edge.** Stripped of the 4 stacks, the de-stacked sample is −$17.12 / 34 trades. Memo §4B endorsed: continue paper-only; gate Phase 3 live-execution on ≥50 clean post-cap trades.
- **One unrelated parallel-session change is in flight** on 4 bitunix files — DO NOT bundle with polymarket commits.

## Canonical sources

Read in this order:

1. **`runbooks/deploy_log.md`** — top entry `## 2026-05-21 12:28:07 UTC — polymarket_arbitrage per-condition_id position cap (commit c2b0e12)`. Single source of truth for what's running on prod. Includes the Observed firings line for the 12:33:51 WTI HIGH $115 skip.
2. **`runbooks/board_memo_polymarket_dedupe_2026_05_21.md`** — the full Board memo (proposal + addendum + approval). §3 (honest characterization), §4B (paper-only posture), §5 (explicitly deferred gates), Addendum §1 (in-flight overhang), Addendum §2 (correlated-underlying limitation), Approval section.
3. **`BACKLOG.md`** — three grouped polymarket entries at the top of the P1 section:
   - P1 cap (APPROVED + shipped — status reflects approval, but the entry hasn't been marked SHIPPED yet; consider updating after observing more skips).
   - P1 clean-data tracker (epoch `2026-05-21 12:28:07 UTC` baked in).
   - P2 underlying/series-level cap follow-up (blocked on per-`condition_id` data review).

## What's running, in one screen

| field | value |
|---|---|
| commit on prod | `c2b0e12` (cap) + `af27c4f` (approval doc) + `fcecbca` (memo addendum) — all on `origin/main` |
| service restart timestamp (clean-data epoch) | **`2026-05-21 12:28:07 UTC`** |
| `max_open_per_condition_id` | `1` (true dedupe) |
| `enabled` | `true` (unchanged) |
| `auto_execute` | `false` (unchanged; broker is `ReadOnlyBroker` regardless) |
| `polymarket_dedupe_skipped` first observed | `2026-05-21 12:33:51 UTC` on `0xdeb0a6…c29c0b` (WTI HIGH $115, n_open=12) |
| backup tag for rollback | `pre-dedupe-cap-20260521-1226` |
| cap helper | `_count_open_entries_by_condition_id` in `agents/strategies/polymarket_arbitrage.py` |
| risk gate touched? | **NO** — `agents/risk.py` unchanged |

## What to monitor (the only polymarket work the next session should do unprompted)

Diagnosis only — no code or config changes. All of these are read-only prod queries:

1. **Confirm continued cap firings.** Expected: as the pre-restart 6h cooldowns expire on stacked condition_ids (Iran 18, WTI HIGH $110 14, WTI HIGH $120 10, PSG 12, etc.), more `polymarket_dedupe_skipped` audits should fire. By ~`2026-05-21 18:30 UTC` most of yesterday's overhang should have cleared cooldown at least once and been refused by the cap.

   ```bash
   ssh azureuser@trading.jacksumner.com 'sqlite3 -readonly /home/azureuser/trading_corp/data/trading_corp.db "
   SELECT json_extract(payload_json,\"\$.condition_id\") AS cid,
          json_extract(payload_json,\"\$.market_question\") AS market,
          json_extract(payload_json,\"\$.current_open_count\") AS n_open,
          ts
   FROM audit_event
   WHERE actor=\"polymarket_arbitrage\" AND kind=\"polymarket_dedupe_skipped\"
     AND ts >= \"2026-05-21T12:28:07\"
   ORDER BY ts ASC;
   "'
   ```

2. **Count clean (post-cap) trades.** Build toward the 50-trade floor (memo §4B). These count only trades placed AFTER `2026-05-21 12:28:07 UTC`:

   ```bash
   ssh azureuser@trading.jacksumner.com 'sqlite3 -readonly /home/azureuser/trading_corp/data/trading_corp.db "
   SELECT COUNT(*) AS clean_resolved,
          SUM(won) AS wins,
          ROUND(100.0*SUM(won)/COUNT(*),1) AS wr_pct,
          ROUND(SUM(realized_pnl),2) AS pnl
   FROM polymarket_round_trips
   WHERE COALESCE(division,\"polymarket_arbitrage\")=\"polymarket_arbitrage\"
     AND entry_ts >= \"2026-05-21T12:28:07\";
   "'
   ```

   Until this hits **n ≥ 50**, do NOT characterize edge as established regardless of interim PnL direction.

3. **Watch the in-flight resolutions of pre-cap stacks** (the ~99 entries that already existed at cap-deploy time). These will resolve over the next ~10 days; their PnL will move the reported dashboard numbers but does NOT count toward the clean sample. **Don't update the memo's characterization based on these resolutions** — they're pre-cap noise.

## Hard rules (do NOT do without a new Board memo)

- Don't flip `polymarket_arbitrage.auto_execute: false → true` — gated on memo §4B (Phase 3 live execution requires ≥50 clean post-cap trades demonstrating edge).
- Don't flip `polymarket_arbitrage.enabled: true → false` — would stop accumulation of the clean-data sample.
- Don't change `max_open_per_condition_id` away from `1` without a separate Board memo.
- Don't propose the LLM-probability `[0.20, 0.80]` rejection gate — withdrawn in memo §5.
- Don't propose a category whitelist — withdrawn in memo §5.
- Don't propose a sports blacklist — deferred in memo §5 (n=10 sample too small under the same standard that retracted the others).
- Don't start work on the P2 underlying/series-level cap until BOTH the per-`condition_id` cap has shipped (done ✓) AND post-cap clean data accumulates enough to evaluate whether the correlated-underlying problem materially remains.

## Parallel session work in flight (DO NOT touch)

As of 2026-05-21 12:40 UTC, the working tree has 4 modified files belonging to a parallel session working on bitunix HTF regime — DO NOT bundle these into any polymarket commit:

- `tests/test_bitunix_htf_regime.py`
- `trading_corp/agents/strategies/bitunix_htf_regime.py`
- `trading_corp/brokers/bitunix.py`
- `trading_corp/web/templates/partials/bitunix_htf_panel.html`

Looks like a funding-units fix (Bitunix API returns funding as percent, not fraction). That session owns this; leave it alone. If polymarket work needs to land, stage files by name explicitly — no `git add -A`.

## Untracked, untouched

- `docs/Deployment notes.txt` — long-running scratchpad (7400+ lines back to May 1). Always untracked. Leave alone.

## Suggested first action in next session

Run the two read-only queries in "What to monitor" above. Compare against this file's recorded state:

- Were there >1 `polymarket_dedupe_skipped` audits between 12:28:07 UTC and now? Which condition_ids?
- Is `clean_resolved` count moving above 0?
- Did anything unexpected happen (cap failed silently → log scan for `polymarket_arbitrage: open-entry count failed`)?

Report numbers. Do not propose changes. Resume from there.
