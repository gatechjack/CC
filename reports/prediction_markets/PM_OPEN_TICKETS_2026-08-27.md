# PM open tickets / findings filed 2026-08-27 (NOT to fix now — recorded so they aren't lost)

## TICKET T1 — `/positions` page cap under-represents the highest-volume whales (SAME CLASS as the loss-omission)
**Filed 2026-08-27 from the Rung-3 step-2 poller run.** `cap_suspect` fired on **6 of 14 whales** — each returned
**exactly 100** `/positions` rows (SDTrading, 4751346, 0x71edffd0, MadeiraIsland, BetMechanic, kutsumiakia). The
shared `fetch_positions` is **un-paginated** (Ruling H), so for any whale with >100 open positions the poller sees
only the first 100 → **paper captures for the highest-volume accounts are a LOWER BOUND.**

**★ Framing (Jack, 2026-08-27):** this is the **same class of defect as the `/closed-positions` loss omission that
started this rebuild** — a truncated feed silently under-representing exactly the accounts we most want data on. A
completeness gate that measures per-whale coverage (does the feed's count look capped?) belongs here, the same way
P1 added the `INCOMPLETE-NOT-RANKED` / `--cap` machinery for `/closed-positions`. **Its own ticket. Do not chase now.**

## TICKET T2 — tier-2 poller categorization gap: PRIORITY LOWERED (measured ~0 miss in practice)
**Revision, 2026-08-27.** The tier-1-only poller gap was earlier estimated at **~6.3%/7d** (soccer/tennis game
lines wrongly → unknown). The Rung-3 step-2 run **measured it at ~0 this snapshot**: of 90 skipped positions, **85
futures/novelty + 4 novelty + 1 false-positive (cycling *tournament-winner* futures) → ZERO trackable game/match
lines skipped.** The whales' entire off-tile open book is long-dated futures/politics.

**Effect:** the 6.3% was an **upper bound over resolved 7d activity**; live open-book snapshots run far below it. This
**LOWERS the tier-2 ticket's priority** — it is not silently losing trackable game lines at the estimated rate.
**Record so the 6.3% is not treated as current.** (Ticket stays open — a poller tier-2 fallback / missing prefixes
would still be correct — just de-prioritised.)

## DESIGN QUESTION D1 — whale-exit disposition (AWAITING JACK'S RULING)
**Raised 2026-08-27, Rung-3 step-3.** `adjudicate()` scores a vanished (whale-exit) position off **gamma's market
resolution**, never off the whale's exit — so a whale who sells pre-resolution is booked on the eventual **market
outcome** (or `stale`/no-P&L if the market never resolves past `end+72h grace`). The copy-trade thesis ("follow the
whale in AND out") would instead book at the **exit price** when the whale sells. **This is a design change and Jack's
to rule** (full cost/alternative in `STAGE1_RUNG3_ADJUDICATE_ROLLUP_2026-08-27.md`). Not implemented.
