# D4 — Concurrent-position guard: ground truth + design pass (read-only)

2026-06-20. Read-only investigation + DESIGN ONLY. No code, no config, no deploy. Board-gated; operator ships.
Engine PID 3065623 (P2-combined, deploy 2026-06-19 22:13 UTC). All evidence = prod audit_event / reconciler /
operator's BitUnix Trade-History screenshots. Code citations = `cc-tpsl-rebuild-wt/trading_corp/` (== prod code).

---

## STEP 1 — GROUND-TRUTH TIMELINE (corrected)

Operator correction applied: the close was **operator-manual** (you moved the stop(s) down to current price on the
shared account), NOT a bot trail-to-breakeven. The venue timestamps resolve the order the operator was unsure about.

| # | UTC | event | who | source |
|---|---|---|---|---|
| 1 | 01:10:09–13 | entry #1 `125b6f9e` otter_sell PREMIUM, fill **63,413.6**, bracket: 3 TP + posSL@63,610.53 | **BOT** | audit (live_order_placed/filled/bracket_placed) — CONFIRMED |
| 2 | **02:06:01–04** | entry #2 `81f5427a` mc_a_redx STANDARD, fill **63,467.6**, bracket: 2 TP (degraded) + **shared** posSL, structural 63,591.23 | **BOT** | audit — CONFIRMED. **← THE DEFECT** |
| – | 02:06:28→02:19:04 | reconciler `match_count=2` every tick; netted to ONE venue position (id 4162115278798051881, avg **63,430.9**, qty −0.00288) | – | audit + venue — CONFIRMED |
| – | 02:15:01 | 3rd signal mc_a_red_diamond scored tier=**SKIP** → not placed | BOT | audit — CONFIRMED (no 3rd stack) |
| 3 | **02:18:57** | **TP leg auto-fills**: TP Buy 0.0004 @ **63,356.8**, +0.02966 (price reached tp1 ~63,350) | **BOT bracket (venue-managed)** | venue Trade-History — CONFIRMED venue-side; **bot did NOT observe it** (no fill audit; `filled_legs=[]`) — CONFIRMED gap |
| – | 02:19:04 | reconciler STILL `match_count=2` (blind to the partial — matches on (symbol,side) presence, not qty) | – | audit — CONFIRMED |
| 4 | **02:19:47** | **operator-manual close**: SL moved to ~current price → SL Buy 0.0024 @ **63,391.6**, +0.09446 | **OPERATOR** | operator statement + INFERRED (bot `moved:false`; the orig 63,591 buy-stop could NOT fire at a ~63,390 mark — only an operator move-to-price closes it here) |
| 5 | 02:20:05 | reconciler **divergence** (missing_on_broker=2 → flat); bot `bracket_sl_move` runs → **moved:false** (current_qty=0, reason "TP1+TP2 filled" = post-hoc GUESS, position already gone); halt latched | BOT | audit — CONFIRMED |
| 6 | 02:21:06 | bot **auto-books both** → `win`, `real_fill`, n_fills=2 — but EACH books the FULL netted close (qty 0.0028) | BOT | audit — CONFIRMED |
| 7 | 02:22:07 | `halt_released` "two_consecutive_clean_ticks" → self-resume, no restart | BOT | audit — CONFIRMED |

**Trigger chain (the thing to guard against):** a vanilla 2nd TV signal (mc_a_redx) at 02:06 entered a 2nd live
short while position #1 was fully open. **No TP fill was involved in the stacking** (the first TP didn't fill until
02:18); the operator's "in some order" uncertainty is resolved — 2nd entry (02:06) ≪ TP fill (02:18:57) ≪ manual
close (02:19:47). The stack was a pure additive entry with nothing stopping it.

**Why the existing safety net missed it (critical for the design):** stacking a same-symbol same-side entry does
**not** create a reconciler divergence — two tracked live rows both "match" the one netted venue position by
(symbol,side) → `match_count=2`, `divergence=false`. So `_halt_new_orders` was never set. The reconciler/halt
machinery structurally **cannot** catch same-symbol stacking; only a dedicated entry-time guard can.

**Consequences (the D-series, re-confirmed):** D1 PnL double-booked (~6× — each record claimed the whole 0.0028
close); D2 `filled_legs=[]` (TP fill never registered, bot blind 02:18:57→02:20:05); D3 role mis-record (venue
Taker, engine maker); **D4 = the root cause: no concurrent-position guard.**

---

## STEP 2 — GUARD DESIGN (for Board review; not built)

**Goal:** prevent the bitunix bot from opening a NEW entry when **the bot already holds an open same-symbol
SAME-SIDE position IT opened**, synchronously at entry time — the case the existing reconciler orphan-rule
structurally cannot see. The operator-manual / not-bot-opened case is **already** handled by the existing orphan-halt;
**D4 does NOT touch it.**

### Existing rule (confirmed in code) — what D4 composes with
The manual / not-bot-opened position is ALREADY guarded by the reconciler's orphan-halt
(`bitunix_position_reconciler.reconcile_position_state`, L850–1055): it pulls broker truth `get_pending_positions()`
(L887) + bot-tracked open rows `_load_tracked_live_rows` (L895), matches by **`(symbol, side)`** (L905–932); any
broker position with no matching bot row = `orphan_on_broker` (L934–944) → `broker._halt_new_orders = True`
(L1014–1017), enforced in `place_order` (bitunix.py L1092). It is **reactive** (~60s reconciler tick), not synchronous.
**Why it misses the D4 case (the gap):** the `(symbol,side)` match is **many-to-one tolerant — it does not count
positions.** A 2nd bot short creates a 2nd tracked row that ALSO maps to the same `(BTC,sell)` key → both rows
"match" the one netted broker position → **no orphan, no divergence, no halt** (exactly the incident: `match_count=2`,
divergence=false). So the existing rule **owns "not-bot-opened"; it is blind to "bot's-own stacking."** → zero overlap.

### What D4 checks, and WHERE it reads state (CORRECTED — manual handling DROPPED)
**D4 = block iff the BOT already holds an open same-symbol SAME-SIDE position IT opened.** Manual is the existing
rule's job; D4 must NOT duplicate it.
- **AUTHORITATIVE (necessary) = VENUE.** Read `snap.positions` (already fetched in the entry path
  `_score_and_maybe_propose_locked` ~L1588-1609 / `_maybe_propose` ~L3755; sourced from `/get_pending_positions`,
  zero extra call). Block only if the VENUE actually shows an open same-symbol SAME-SIDE position **right now.**
  *Rationale (the correction):* after you manually close a bot position at the venue (SL-to-price), the engine DB row
  can still read `result IS NULL` until the reconciler catches up (≤60s). Gating on engine-belief would **wrongly
  block a legitimate new entry post-manual-flatten.** Venue presence is the necessary gate → venue flat ⇒ never block.
- **CORROBORATES ONLY (scopes to bot's-own) = engine open live row.** A tracked open same-side row
  (`_load_tracked_live_rows` L434 / `list_open_positions` bitunix.py L1832) confirms the venue position is the bot's
  own (vs a manual one the existing rule owns). **Engine-belief alone NEVER blocks** — it only narrows a
  venue-confirmed-open position to the bot's-own case.
- **Net rule:** block iff **(venue shows open same-symbol same-side) AND (bot has a tracked open same-side row).**
  Fires on the bot-on-bot stack (venue shows the open short + bot has its row); does NOT fire post-manual-close
  (venue flat), nor on a manual position (no bot row → existing orphan-halt owns it).

### Fail-safe direction (per constraint)
**UNKNOWN ⇒ fail CLOSED (do not open).** If the snapshot raised, `equity_complete` is False, `positions` is
None/unavailable, or the signed query errored → treat as "state unknown" → **block the entry.** Never fail open into a
stack. (Opposite of the snapshot path's equity-fallback-to-100k, which must NOT be reused here.) **Critical:** do NOT
inherit the reconciler's `get_pending_positions`-error→`[]` convention (L887-893) — for D4 an **errored/incomplete**
venue read is UNKNOWN (⇒ block), NOT "confirmed flat." Only a successful, complete venue read showing no same-side
position counts as flat.

### Shared-account / don't-fight-the-operator (CORRECTED)
D4 does **not** handle the manual position (existing orphan-halt owns it) and is **venue-gated**, so it cannot fight
you two ways: (1) it only BLOCKS new entries — never closes/modifies/flattens; (2) the moment you manually flatten a
bot position at the venue, D4's authoritative read (venue) goes flat → D4 stops blocking → the bot can take the next
legit signal even though the engine DB row may still lag `result IS NULL`. (An engine-belief gate would keep the bot
locked out until the reconciler caught up — the wrong-block this correction removes.)

### Interaction with existing halt/breaker — strict partition, zero overlap
- **Existing orphan-halt owns "not-bot-opened"** (manual/orphan): reactive, `(symbol,side)`-matched, ≤60s lag. D4 does
  NOT touch this case (the dropped duplication). Its pre-existing ≤60s manual lag is out of D4's charter.
- **D4 owns "bot's-own same-side stack"**: synchronous at entry — the case the orphan-match structurally can't see.
- D4 does not replace `_halt_new_orders` (still enforced in `place_order` bitunix.py L1092); it adds a synchronous
  pre-entry check for the gap, short-circuiting before the snapshot→propose→risk work.
- Together they partition the space: any broker position is either bot's-own-same-side (D4) or not-bot-opened
  (existing). No case is double-covered.

### B1 + native bracket — UNTOUCHED (per constraint)
The guard gates BEFORE `_place_live`/`data_exec.place()` (observer L3122). If the entry is blocked, the B1 slPrice
(bitunix.py L1323-1326, attached to the entry order) and the native `/tpsl/` bracket (`_place_bracket_exits` L3345,
`place_tpsl_order` L1944 / `place_position_tpsl` L2042, all post-fill) are simply never reached. Zero changes to any
of them.

### Insertion points (two, mirror each other)
- Score path: in `_score_and_maybe_propose_locked` after the snapshot/abstain block (~L1609) and before
  `_build_proposal_v2` (~L1628) — OR after the risk gate (~L1716) before `_record_placement_outcome` (L1748).
  Earlier-after-snapshot is cheaper (skips proposal+risk work).
- Phase 3.1 path: in `_maybe_propose` after snapshot (~L3766) before `_build_proposal` (~L3791).
- Emit `concurrent_position_guard_blocked` audit (reason, detected position qty/side, source=venue|engine|unknown)
  BEFORE returning — audit-before-branch per CLAUDE.md — **AND a Telegram notify** (ruling 4: a silent block reads
  as no-signal; the operator must see the guard fired). Notify carries the skipped trigger + the held position.

### Reduce-only / flip exemption — RULED: same-side-only (Board recommendation; final operator go pending)
The guard gates only **additive same-side** new entries. v1 rule: **block a new NON-reduce-only entry iff a
same-symbol SAME-SIDE position is open** (block a 2nd short while short). **ALLOW close-and-reverse** (a deliberate
flip) and any reduce-only/flatten order — the flip/flatten path (`_maybe_flatten_on_risk_verdict` L2575, flatten
dispatch L1708/L3841, `reduce_only` exempt in place_order L1091) proceeds untouched. Opposite-side decisions route
through the existing flatten-first logic. This is the precise fix for the actual defect (2nd short while short)
without breaking legitimate reversals.

### Config flag (matches the staleness-gate pattern)
- `config/strategies.yaml` under `bitunix_futures`: `concurrent_position_guard: { enabled: <bool> }` — **ship OFF**,
  flip ON after one clean validation (mirrors staleness gate, strategies.yaml ~L1031-1033).
- `main.py` ~L420-445 read pattern → pass to observer ctor ~L511-512.
- Observer ctor: `concurrent_position_guard_enabled: bool = False` (default OFF), stored on self.

### Files a later (Board-gated) deploy would touch
1. `config/strategies.yaml` — add flag (ship OFF).
2. `trading_corp/main.py` — read flag + wire to ctor.
3. `trading_corp/agents/divisions/bitunix_futures_observer.py` — ctor kwarg + 2 guard sites + audit emit.
- **NO** changes to `brokers/bitunix.py`, `agents/risk.py`, the bracket, or B1.

### Coupling flags
- Reads `AccountSnapshot.positions` shape (additive read; base.py).
- If it also reads the DB view, couples to the `_load_tracked_live_rows` query shape (reconciler L434) — factor a
  shared helper or replicate the exact filter (`result IS NULL` + `execution_mode=live`).
- **Main behavioral risk = the flip/reduce-only exemption** — getting this wrong would block intentional reverses or
  (worse) leave a stacking hole. Needs the Board decision above + a test that a flip still flattens.
- Must validate snapshot freshness/`equity_complete` at the guard site and fail-closed on incompleteness — do NOT
  inherit the entry path's equity=100k fallback.

### Board rulings (recommended by operator; final explicit go PENDING — NO build until then)
1. **Flip handling → SAME-SIDE-ONLY block.** Block a 2nd short while short; ALLOW close-and-reverse; reduce-only/flip exempt.
2. **Coupling → CONFIRMED.** Bot declines BTC entries while the operator manually holds BTC (a missed entry ≪ another netting/ledger corruption).
3. **Symbol scope → same-symbol-only today** (bitunix=BTC), but STRUCTURE the check symbol-keyed so broadening later is an extension, not a rewrite.
4. **Guard-block → Telegram NOTIFY** (plus audit). A silent block is indistinguishable from no-signal; the operator must see it fired.

### D1 sequencing (Board)
D1 (netted-close PnL double-booking) stays **SEPARATE**, sequenced **immediately after** D4. Shared root cause
(netting), different fix: D4 prevents the stack at entry; D1 corrects how a netted/multi-fill close is booked when one
occurs. D1 bites on ANY multi-fill close (not only the D4 stack) → needs its own design regardless. Single-purpose
ships: D4 first, then a separate D1 design pass. No bundling.

---

**Recommendation / status:** design **RE-FROZEN (corrected: manual handling DROPPED — owned by the existing
orphan-halt; VENUE-authoritative with engine corroboration-only; composition with the existing rule confirmed in
code)**; **no code until explicit operator go.** When greenlit: build the scoped files (strategies.yaml flag OFF +
main.py wire + observer ctor / 2 same-side guard sites reading venue `snap.positions` ∧ engine open-row /
Telegram+audit on block), structured symbol-keyed, ship behind the OFF flag, flip ON after one clean single-trade
validation. The guard is small (1 observer + main + yaml), additive, leaves every sacred path untouched. D1 follows
as a separate pass. Agent SSH this session = READ-ONLY throughout (sqlite3 -readonly, no writes/restart/config).
