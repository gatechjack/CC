# TASK 4 — Futures-only maker-entry window: one-page proposal (2026-08-05)

**Proposal only. The B2 maker flip stays HELD.** This proposes a bounded, measured maker-entry
window on the **`bitunix_futures` division only** — the live micro "correctness harness" — to gather
maker mechanics **without touching the SFP OOS sample**.

## Why this is isolated from SFP (verified)

- The B2 maker code (`ef6fa5f`) places entries as `POST_ONLY` LIMIT at a passive offset, crossing to
  taker on non-fill/would-cross; **entries only, B1 stop stays taker** (`brokers/bitunix.py` maker
  dispatch is skipped for `reduce_only`/exits and requires the observer to stamp `extra['maker_entry']`
  per division, LIVE-only).
- The `maker_entry_*` config exists **only** in the `bitunix_futures` block
  (`config/strategies.yaml:1372-1375`, `fees.maker_entry_enabled: false`). The **SFP** block has no
  maker config and the SFP observer has **0** "maker" references — SFP entries are taker regardless.
- `bitunix_futures` is live on its **own funded account** (`execution_mode: live`, `auto_execute: true`,
  "Phase 2c 2026-06-30: futures live on new funded account"), separate from the SFP account. ⇒ flipping
  the futures maker flag changes **only** futures entries; the SFP live sample stays structurally
  unchanged (clean taker baseline) through its n≥30 OOS window.

## The window

- **Flip (hot-reload, no restart):** `bitunix_futures.fees.maker_entry_enabled: true`. Start
  `maker_entry_offset_pct: 0.0` (join-the-touch), `rest_timeout_s: 2.0`, `fallback_mode:
  cross_to_taker` (signal never dropped). **B1 catastrophic stop stays taker/market — unchanged.**
  Size unchanged (micro).
- **Duration = entry-count-gated, not calendar:** run until **≥30 futures maker entries** OR a
  **3-week** calendar cap, whichever first (30 entries is the floor for a meaningful fill-rate CI).

## What gets measured (per entry, from audit events)

| Metric | Source | Question |
|---|---|---|
| **Maker fill rate** | maker-fill vs `BitunixMakerEntryUnfilled` / taker-fallback count | Does `offset=0.0` actually rest as maker, or cross? |
| **Slippage vs taker** | maker fill px vs signal-ref/touch (taker reference) | Realized maker saving/side (≈0.0004→0.00014 ≈ 0.00026) |
| **Missed / late entries** | rest-timeout expirations + entry-timing delta | Late-entry cost when the maker rest delays a fast fill |
| **Net-R effect** | fee saving − non-fill/late-entry cost, in R | Does the maker saving survive the entry-quality cost? |

## Abort criteria (pre-registered — any one trips → flag OFF immediately)

1. **Taker-fallback rate > 40%** → `offset=0.0` crosses too often; either raise `offset_pct` and
   restart the count, or abort (economics don't hold if most entries cross anyway).
2. **Any maker-path fault:** an unhandled `BitunixMakerEntryUnfilled`, the cancel-FAILED
   double-fill guard firing, or a reconciler orphan-stop / `_halt_new_orders` event.
3. **Net-R below the taker baseline** by more than the measured noise band after ≥30 entries.
4. **Any drawdown-cap / halt event** on the futures account.

## How results feed the n≥30 review

- The window yields a **measured maker fill-rate + slippage + net-R-delta table on live futures** —
  the evidence B2's PROPOSALS entry said was needed ("measure fill rate before trusting the
  economics"). Fees are the dominant P&L drag (18–50% of R on futures), so a clean positive net-R
  with acceptable fill rate would **de-risk** an eventual SFP maker flip *after* SFP's own n≥30 OOS
  review; a high-fallback / negative result keeps the SFP flip parked.
- Crucially it does this **without perturbing the SFP OOS sample** — SFP stays taker, so its n≥30
  edge measurement remains a clean read.

## Status / gate

Config flip on a **live** account ⇒ **operator-gated** (this is a proposal, not a deploy). It is an
execution-mechanics probe (entry side/size/tier/signal all unchanged), not a strategy-parameter or
signal change — lower-stakes than a Backtester-gated signal change, but still an explicit operator go.
Rollback = flip the flag back to `false` (hot-reload). No `require_approval_for` trigger is touched.
