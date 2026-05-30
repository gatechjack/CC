# Next session prompt — paste into a fresh Claude session

Use this as the opening message in a new Claude session. It carries forward
everything the next session needs without re-deriving prior context.

---

## Prompt

```
Resume Stage-1 N+2 Phase 1b — complete the live-exit-path sub-diagnostic + scope decision

Prior session shipped Stage-1 N+1 (entry-path, 8 commits) + the exception-class
move on broker-write + Stage-1 N+2 Phase 1a (read-only sub-diagnostic, 2 commits
on bitunix-live-exit-path-2026-05-29, pushed). This session completes Phase 1b
then stops at the Phase 2 scope decision for operator confirmation.

READ FIRST (in this order):

1. cd "C:\Users\AA Incorporado\cc"
2. reports/2026-05-29_bitunix_live_exit_path_diagnostic_phase1a.md
   — Phase 1a ground truth. Covers structural questions #1, #2, #3, #8, #9
     with code refs + recommendations. The Phase 1b handoff prompt is
     embedded at the end; treat that as the authoritative scope spec for
     this session.
3. Memory [[bitunix-live-exit-path-phase1a]] — Phase 1a outcome summary +
   operator decisions required.
4. Memory [[bitunix-live-entry-path-pattern]] — N+1 work that landed before
   Phase 1a; the "no paper_trade_record on live" decision in commit 3 is
   the source of the Path C blocker Phase 1a surfaced.
5. Memory [[bitunix-order-path-safety-pattern]] — Session N safety
   scaffolding + the cross-branch class-identity fix.
6. Memory [[bitunix-live-engine-build]] — overall Stage-1 status + remaining
   items.
7. Memory [[telegram-audit-success-is-confirmed-delivery]] — discipline
   principle for all side effects.
8. Memory [[verify-premises-against-ground-truth]] — expect premise
   corrections; Phase 1a found one large gap (Path C reverses commit 3).
9. runbooks/2026-05-29_bitunix_live_readiness_audit.md (Stage-1 items
   #3 restart-resume, #4 reconciliation, #5 cost accrual, #7 alerts).
10. runbooks/2026-05-29_bitunix_live_reuse_audit.md (lumibot do_polling
    reference + get_history_positions for fees+funding).

VERIFY CURRENT STATE before assuming:

- Current branch should be bitunix-live-exit-path-2026-05-29 (off main, 2
  commits, pushed). If working tree shows uncommitted modifications to
  config/strategies.yaml (kalshi_weather_arb + kalshi_crypto_arb disable):
  that's pre-existing drift carried over from a prior session — see
  BACKLOG P3. NOT yours to touch in this session.
- 7 unmerged Session-29 feature branches on origin (6 from N+1 wrap +
  this N+2 Phase 1a branch).
- prod source code on main is unchanged.
- Default execution_mode: paper everywhere.

PHASE 1B SCOPE (read-only, no code changes):

Per the handoff prompt in the Phase 1a report § "Phase 1b handoff prompt",
investigate the remaining four structural questions:

#4 Post-trade reconciliation (Stage-1 readiness audit item #4)
   - Lumibot do_polling diff-engine reference
   - Does fill-attribution logic exist on broker-write 87dac50? Quote.
   - Recommend reconciliation policy (tolerance threshold + divergence
     handling + idempotency)
   - Identify any schema changes required

#5 Cost accrual (Stage-1 readiness audit item #5)
   - get_history_positions + WS position channel
   - Where do fees + funding get booked? (new audit kind, new column,
     extra_json?)
   - Fold into N+2 or defer to N+3?

#6 Restart-resume from broker truth (Stage-1 readiness audit item #3)
   - Lumibot _first_iteration sync pattern
   - Edge case: restart between TP1 + TP2 fills — broker shows reduced
     position; paper_trade_record.extra_json shows filled_legs=['tp1'].
     Reconciliation strategy: trust broker for open qty; trust extra_json
     for lifecycle state.
   - Fold into N+2 or defer (with explicit "no restart during live position"
     operational constraint)?

#7 Operational alerts surface
   - Beyond Phase 1a's elevated-(live,exit) suffix on first N exits:
     audit kinds + telegram shapes for exit_order_placed/filled/rejected/
     partial/position_closed
   - bitunix_lifecycle_notifier.py is the natural home for some of these.
     Quote its current surface; recommend additions.

PHASE 2 SCOPE DECISION (operator-gated after Phase 1b):

Based on Phase 1a + Phase 1b combined:
  (A) Full N+2: exit path + reconciliation + cost accrual + alerts +
      restart-resume — big session, likely 10+ commits
  (B) Narrowed N+2: exit path + alerts only; defer reconciliation/cost/
      restart to N+3/N+4 — most likely outcome if scope is genuinely heavy
  (C) Refactor surfaced as blocker

PATH C CONFIRMATION (operator decision, load-bearing):

Phase 1a found that N+1 commit 3 (e04b192) wrote "no paper_trade_record on
the live path", but paper_trade_replay walks `paper_trade_record WHERE
result IS NULL` — so live positions are STRUCTURALLY INVISIBLE to the exit
detection loop. Phase 1a recommends Path C: live entries write the row
with extra.execution_mode='live' + extra.broker_order_id. REVERSES commit
3's decision. Required for the entire N+2 build to work.

Surface this decision EARLY in the session for operator confirmation. If
Path C is NOT confirmed, Phase 1b scope shifts toward parallel-table
designs (Path B) and the recommended structural shape changes significantly.

HARD CONSTRAINTS carried forward:

- No code changes in Phase 1b (read-only diagnostic only)
- No deploy
- No restart
- Branch unmerged
- Secrets never touch the session
- Stop-and-report at Phase 1b completion AND at Phase 2 scope decision
- Verify premises against ground truth — Phase 1a found one large gap +
  several smaller corrections; expect more in Phase 1b
- Tight commits if Phase 1b produces report artifacts: one commit per
  structural question OR one bundled commit, your call

OUTPUT EXPECTED:

- Phase 1b investigations for #4, #5, #6, #7 with code refs + recommendations
- Path C operator decision (surfaced early)
- Phase 2 scope recommendation (A/B/C) with rationale
- STOP for operator confirmation before any Phase 3 implementation
- Update BACKLOG P2 entry with Phase 1b findings
- Update memory [[bitunix-live-exit-path-phase1a]] (or file
  [[bitunix-live-exit-path-phase1b]] — your call) with the Phase 1b outcome
- Push the branch when commits are stable

Operator-set defaults:
- Default scope option if everything is clean: A (full N+2)
- Default Path C: confirmed (proceed with revert of N+1 commit 3 folded
  into N+2 code branch)
- BUT — DO NOT auto-resolve either without surfacing the decision first.
  Stop-and-report discipline applies.
```

---

## Operator quick-pass checklist before pasting

- [ ] Decide on the uncommitted `config/strategies.yaml` change (BACKLOG P3) — commit on main + deploy, or leave alone? Phase 1b session should NOT touch it.
- [ ] Pre-confirm Path C (or surface a counterargument). If confirmed, the next session can move faster.
- [ ] Pre-confirm Phase 2 default = A or B. If you're already certain it should be (B) narrowed, say so in the prompt to save a stop-and-report cycle.
- [ ] Decide if you want Phase 1b + Phase 2 in ONE session, or split (Phase 1b only this time, Phase 2 in a third session).

If you want to amend any of these, edit the prompt block above before pasting.
