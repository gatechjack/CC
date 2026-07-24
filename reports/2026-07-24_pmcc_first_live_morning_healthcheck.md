# PMCC engine — first live morning health check (2026-07-24)

READ-ONLY investigation. Nothing placed, modified, cancelled, or changed. Authoritative
fills pulled from the broker (Robinhood `get_option_orders`, acct 461391328); engine internals
from prod journal + `trading_corp.db` (`sqlite3 -readonly`); code from the `cc` repo @ `126d46a`.

Time context: engine PID 359846, up since 2026-07-24 00:33 UTC, NRestarts=0. Market open 13:30 UTC.

---

## TL;DR verdict

**No money was lost to bad pricing. The "two rolls filled far from mark" alarm is a real but
COSMETIC data-integrity bug — the engine mis-assigns per-leg fill prices on live combos, which
inverts a credit into a displayed debit.** Every roll today was a clean credit at/near the mark.

- The dashboard's `-$117` (RKLB) and `~$26 given up` (OPEN) are **wrong**. Broker truth: RKLB
  **+$117 credit**, OPEN **+$26 credit**.
- **2 of the 4 rolls were ENGINE-placed** (OPEN, RKLB — via your dashboard Approve), **2 were your
  MANUAL RH-app rolls** (IREN, BULL — the panic rolls; the engine had *aborted* both).
- Root cause of the false debit: `brokers/robinhood.py:1267` pairs order legs to broker fills **by
  array index**, but Robinhood returns the spread legs in its own order → prices bound to the wrong
  leg → the credit computes as a negative (debit-signed) net. **Live-only**; first surfaced today on
  the first-ever real combo fills.
- `reprice_combo` produced **sane** limits today (fills at mark) — but only because the Approves
  landed 8–12 min after open, after quotes normalized. It still has **no** price-sanity guards
  (all of G1–G5 absent). The vulnerability is real; today we were not exposed to it.
- Engine health is clean: auto_execute:false, scheduler fired, exec-alerts wired, no unexpected
  placements, only known/benign ERRORs.

**Dollars actually lost to bad pricing today: ~$0.** (Largest per-leg deviation ~$5 on the RKLB sell,
on a fast-falling option — market movement, not slippage.)

---

## PRIORITY 1 — Order reconciliation & the off-mark question (ANSWERED)

Four combo rolls filled today (all `placed_agent:"user"` — expected, because both the engine's
`robin_stocks` session and your app log in as the same user; `placed_agent` does NOT distinguish them).

### Broker truth vs dashboard, per roll

| Symbol | Time (UTC) | Actor | Sell-to-open (fill vs mark) | Buy-to-close (fill vs mark) | **Real net** | Dashboard showed |
|---|---|---|---|---|---|---|
| IREN | 13:37:12 | **MANUAL** | 07-31 $44.5C @0.96 (mark ~0.98) | 07-24 $43C @0.04 (mark 0.04) | **+$92 credit** | (credit) |
| OPEN | 13:38:24 | **ENGINE** | 08-07 $4C @0.29 (mark ~0.31) | 07-31 $5C @0.03 (mark 0.03) | **+$26 credit** | "~$26 given up" |
| RKLB | 13:42:32 | **ENGINE** | 07-31 $75C @1.20 (mark ~1.22–1.29) | 07-24 $74C @0.03 (mark 0.03) | **+$117 credit** | "-$117 debit" |
| BULL | 13:48:24 | **MANUAL** | 08-14 $8.5C @0.23 (mark ~0.20, *improved*) | 08-07 $9C @0.05 (mark ~0.06) | **+$36 credit (x2)** | (credit) |

Every leg filled within ~1–5¢ of the contemporaneous mark (RH minute historicals). Total credits
collected: **+$271** — note that is *exactly* the "+$271" you saw "recommended" for RKLB; the
dashboard likely surfaced an aggregate or an earlier eval on the RKLB card (see below).

### Engine vs manual — definitive

Engine placements leave a paper trail (`board/board_approved via:"web_button"` → `data_exec/combo_filled`
→ `proposed_order` rows). Manual RH-app rolls leave none.

- **OPEN** — `board_approved` (combo `5c9e347f`, web_button) 13:38:23 → `combo_filled broker_order_id
  6a636acf` 13:38:25. Two `proposed_order` rows. **ENGINE.**
- **RKLB** — `board_approved` (combo `360f4b92`, web_button) 13:42:31 → `combo_filled broker_order_id
  6a636bc8` 13:42:33. Two `proposed_order` rows. **ENGINE.**
- **IREN** (13:37) and **BULL** (13:48) — **zero** engine records (no board_approved, no combo_filled,
  no proposed_order). The engine had **ABORTED** both minutes earlier. → **MANUAL** (your panic rolls).

### The off-mark "debit" — root cause

The engine's stored per-leg `fill_price` is **transposed between the two legs** on the engine combos:

| | Sell leg (broker) | Buy leg (broker) | Sell leg (`proposed_order.fill_price`) | Buy leg (`proposed_order.fill_price`) |
|---|---|---|---|---|
| OPEN | $4C **0.29** | $5C **0.03** | 0.03 ✗ | 0.29 ✗ |
| RKLB | $75C **1.20** | $74C **0.03** | 0.03 ✗ | 1.2 ✗ |

Because the sell leg (large price) gets the buy's tiny price and the buy leg gets the sell's large
price, the net computes as `0.03 − 1.20 = −1.17` → a **debit-signed number**. The `combo_filled`
audit confirms the corruption end-to-end: `net_actual −0.26`/`−1.17` (should be **+0.26/+1.17**) and
`actual_vs_limit_slippage_dollars 0.5`/`2.31` (should be ~**0.02/0.03** price-improvement). Only
`direction:"credit"` (set from the proposal, not the fills) stayed correct.

**Mechanism — `brokers/robinhood.py:1264-1295`:**
```python
legs_result = (final or {}).get("legs") or []
for i, o in enumerate(orders):
    leg = legs_result[i] or {}          # <-- positional: RH's i-th leg, not order o's leg
    leg_price = leg.get("price") ...
    fills.append(FillEvent(order_id=o.id, side=o.side, price=price_f, ...))
```
Robinhood returns `final["legs"]` in ITS canonical order, which need not match our submitted
`orders`. For OPEN/RKLB it was reversed. `data_exec.py:846/859/884` then re-zip `orders`↔`fills`
positionally, propagating the misassigned price into `proposed_order.fill_price`, `net_actual`, the
slippage figure, AND the FILLED exec-alert preview.

**Why now:** paper (`PaperExecutionBroker`) builds fills in `orders` order, so `zip` always aligned —
the bug was latent through the entire paper history and surfaced on **today's first real Robinhood
combo fills**. It is **data-dependent** (only bites when RH's leg order ≠ our submission order), so
it may not reproduce on every combo.

### The "+$271 recommended → +$117 filled" gap

Not slippage — **proposal instability**. RKLB was crashing (the $75C fell 1.43→1.22 in the first 12
min). The engine re-evaluated RKLB twice a minute apart:
- 13:41:06 `pmcc_roll_gates mark_net 2.71` (=+$271) — an earlier/higher mark (likely a more-ITM
  strike selection or a stale quote instant).
- 13:42:29 `pmcc_roll_gates mark_net 1.22` — the actual $75C @ δ0.25 that was approved & filled (+$117).

So the card showed +271 from the earlier eval; the executed roll was the later +117. Compounded with
the leg-swap displaying it as **−117**, you saw "expected +271, got −117." Reality: **+$117 credit.**

---

## PRIORITY 2 — reprice_combo sanity under opening quotes

`reprice_combo_from_quotes` (`agents/strategies/_pmcc_combo.py:137-181`) computes
`natural = Σ bid(sell) − Σ ask(buy)`, then `limit = natural − give_up` (give_up $0.02 roll / $0.25
close_all), floored at one tick. Quotes are pulled **live at Approve** via `broker.get_option_quote`
per leg (`robinhood.py:1376-1410`) — fresh, but no freshness/width validation.

**Guard audit (all NO):**

| Guard | Present? | Evidence |
|---|---|---|
| G1 — any sanity bound/collar on the computed limit | **NO** | only a 1-tick floor `_pmcc_combo.py:177` |
| G2 — block a credit-proposed roll dispatching as a DEBIT (sign-flip) | **NO** | `_pmcc_combo.py:166-170` explicitly allows credit→debit |
| G3 — compare dispatch net to proposal net, bail on large adverse deviation | **NO** | proposal net is overwritten in place, never compared (`_pmcc_combo.py:179-181`) |
| G4 — stale / excessive-width leg-quote validation | **NO** | only a `bid is None or ask is None` null check (`:149`); `bid=0.01` passes |
| G5 — marketable-limit cap (how far through mark the limit may cross) | **NO** | give_up moves *toward* market; nothing caps it |

**Today's verdict:** reprice produced good limits (OPEN 0.24→fill 0.26; RKLB 1.14→fill 1.17) because
the Approves landed after 9:37, when opening spreads had tightened. It was **not exposed** to the
stale/wide-quote failure mode today — but the code has no protection if an Approve ever lands during a
wide-quote window. The proposal-time net is also **not preserved** in the audit, so slippage-vs-proposal
is unrecoverable post-hoc. This is the latent money-leak; recommend guards below.

---

## PRIORITY 3 — the opening aborts (explained; hypothesis CONFIRMED)

11 `pmcc_roll_aborted` events, all reason **`sparse_chain_no_weekly`** ("no liquid weekly contracts …
all failed liquidity gate"), 13:33–13:46:

- RKLB ×2 (13:33:47, 13:34:23), OPEN (13:34:05), IREN ×2 (13:34:06, 13:35:13), BULL ×2 (13:45:59, 13:46:43).

**All transient.** Every aborted symbol subsequently cleared and rolled (OPEN 13:38 engine, RKLB 13:42
engine, IREN 13:37 manual, BULL 13:48 manual). The gate is OI-or-volume liveness + spread≤10%. Open
interest is **static intraday**, so a fail→pass flip within 8 minutes can only be the **volume and/or
spread** component — i.e. exactly your hypothesis (a): same-day cumulative volume starts at 0 at 9:30,
so the volume floor cannot pass in the first minutes even for liquid names, and (b) opening spreads are
wide. As the session matured, both normalized and the chains qualified.

**BULL was NOT a distinct persistent-low-volume case** — it aborted later (13:45) and cleared later
(filled 13:48), but by the same transient mechanism. There is no persistent-reason abort today.

**Observability gap:** the log rolls the failure up to "all failed liquidity gate (N candidates)" and
does not record which sub-gate (OI / volume / spread) rejected — so the exact binding criterion can't
be read from the log; it's inferred. Worth logging per-gate counts.

---

## PRIORITY 4 — exec-alert audit

- **Wiring:** boot line `Execution alerts wired to Telegram (tiers=all-on)` 00:33:31. Tiers emitted by
  the combo path: `FILLED` / `NO_FILL` / `EXEC_FAIL` / `NAKED_LEG` (`data_exec.py:801-915`).
- **Volume:** `telegram_channel/telegram_notification_success x144` today (0 failures in that kind) —
  across all divisions, so PMCC's share isn't isolable from the DB alone.
- **Corruption bleeds into the alert:** the `FILLED` ping renders `f"{direction} filled {actual:g}"`
  with the **sign-corrupted** `actual` (−0.26/−1.17) → your "success" pings for OPEN/RKLB also showed
  negative numbers, compounding the panic. Fixing the leg-swap fixes the alert too.
- **Dedupe-bypass:** `FILLED`/`NAKED_LEG` carry `changed=True` (bypass path); this appears wired, but
  full verification of "Approve outcomes never swallowed" needs the alert-module dedupe logic + Telegram
  history, which are outside the DB — **not fully verified here.**
- **ABORT wording (the trigger for your manual panic):** the abort surfaces as
  `ABORTED roll/open on RKLB -- sparse_chain_no_weekly (missing new_short)`. "ABORTED … missing leg"
  reads like a mid-flight failure / position hazard, when it means "engine chose not to act; no order
  sent; position untouched; will retry." **The wording is misleading and warrants a reassuring rewrite**
  (recommendation below).

---

## PRIORITY 5 — position integrity after the manual rolls (CLEAN)

Reconciled the book to the broker (`get_option_positions`).

- **No double-rolls.** Exactly one roll per symbol today, each attributable to exactly one actor
  (RKLB/OPEN engine; IREN/BULL manual). One broker order per symbol; no symbol was rolled twice.
- **No naked / half-open legs.** Every short today is covered by a long LEAP: RKLB 2028, OPEN 2027×2,
  IREN 2028, BULL 2027×2 (and every other short — MSTR/TSLA/RIOT/SMR/CIFR/BLSH/HOOD — has its LEAP).
  All 4 combo fills were atomic (both legs, `combo_filled leg_count 2`).
- **No pending_* quantities** on any position.
- **Stale `board_approved` zombies (pre-existing, flag):** 4 rows — ASTS (2026-05-08 ×2, 2026-05-21) and
  CIFR (2026-07-08) — all ≥16 days old, **none from today**. Per prior analysis these are inert (no
  `combo_id`; PMCC rebuilds its book each scan). Today's 4 engine legs are all `status=filled` (not
  lingering). Recommend confirming the 4 zombies cannot re-dispatch and pruning them (the `c870a9e6`
  hygiene lesson) — low urgency.

---

## PRIORITY 6 — engine health (GREEN)

- PID **359846**, NRestarts **0**, up since 2026-07-24 00:33:16 UTC (~13.8 h).
- **auto_execute: false** confirmed in live `strategies.yaml` (robinhood_pmcc). HITL intact.
- Scheduler fired: `scheduler/scheduled_scan_done x1` (+ pead_scan_done, pead_reconcile_promoted).
- exec-alert wiring active (boot line above).
- **No unexpected placements.** Today's engine order activity = OPEN + RKLB combos (2) + 6 robinhood_pead
  equity buys (PECO/GOOGL/KALU/MEDP/NVEC/CSX — expected, separate live division). No stray PMCC orders.
- **Tracebacks:** the 3 known/allowed recur (fidelity playwright ENOENT, yfinance crypto-earnings,
  EODHD 404). Beyond them: 2× transient `pykalshi` HTTP 500 (11:08) and an `odds_api` 401 — external
  API hiccups, non-PMCC, non-fatal. No PMCC tracebacks.

---

## RECOMMENDED FIXES (propose only — nothing built)

Ranked by impact. The first is the one that caused the panic.

1. **[HIGHEST — data integrity] Fix live per-leg fill attribution.** `brokers/robinhood.py:1264-1295`:
   match `legs_result` entries to `orders` by **option identity** (RH leg's `option` instrument id /
   occ symbol, or strike+expiration+option_type+effect), not by index `i`. Assert each order matches
   exactly one leg. This simultaneously corrects `proposed_order.fill_price`, `combo_filled.net_actual`
   (sign), the slippage figure, the dashboard, and the FILLED exec-alert. No capital was harmed, but
   this is what turned a +$117 credit into a "-$117 debit" and drove the manual panic.

2. **[reprice sanity bound — the latent money-leak]** Add to `reprice_combo_from_quotes`:
   - **Sign-flip guard (G2):** never dispatch a credit-proposed roll as a debit — abort & re-surface.
   - **Adverse-deviation bail (G3):** compare dispatch-time net to the proposal `mark_net`; if the
     credit collapses beyond a tolerance (RKLB's 2.71→1.22 would have tripped it), abort and ask for
     re-confirmation instead of silently repricing.
   - **Stale/wide-quote guard (G4):** reject if the sell-leg bid ≤ 0 or `(ask−bid)/mid` exceeds a
     width cap before pricing.
   - **Preserve the proposal-time net** in the `combo_filled` audit so deviation is auditable post-hoc.

3. **[opening-settle window — prevents the scary aborts]** Suppress/soften PMCC roll attempts (or their
   alerts) for the first ~5–10 min after 9:30 ET, or gate on **OI-only** during that window (OI is known
   at open) and defer the volume/spread check. This removes the transient `sparse_chain_no_weekly`
   aborts that triggered the manual rolls, without weakening the steady-state liquidity gate.

4. **[abort UX] Reword the abort + log the binding gate.** Change e.g. to:
   `PMCC RKLB — no action: no liquid weekly yet (opening); no order sent, position unchanged, will retry
   next scan.` Consider demoting the abort exec-alert tier during the settle window. Separately, log the
   per-gate reject counts (OI / volume / spread) so the failing criterion is observable.

5. **[hygiene] Prune the 4 stale `board_approved` zombies** (3 ASTS, 1 CIFR) after confirming they
   cannot re-dispatch — closes the `c870a9e6` failure-mode surface.

---

### Evidence index
- Broker: `get_option_orders` acct 461391328 (orders 6a636a88 IREN, 6a636acf OPEN, 6a636bc8 RKLB,
  6a636d27 BULL); `get_option_positions`; `get_option_historicals` (minute marks 13:30–13:55).
- Engine: `trading_corp.db` audit_event (`combo_filled` 5c9e347f/360f4b92, `board_approved`,
  `pmcc_roll_gates`, `pmcc_roll_aborted`), `proposed_order` rows; journal since 00:30 UTC.
- Code (`126d46a`): `brokers/robinhood.py:1264-1295`, `agents/data_exec.py:846-916`,
  `agents/strategies/_pmcc_combo.py:137-181`.
