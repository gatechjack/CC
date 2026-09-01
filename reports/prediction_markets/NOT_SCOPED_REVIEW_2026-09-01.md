# NOT-SCOPED REVIEW — what "multi-account" honestly means right now (2026-09-01)

Reviewing the audit's NOT-SCOPED set BEFORE building pages, because it defines whether Karen is a trading account
or a display entity. If the driver/caps/creds assume one account, a page implying per-account trading asserts a
capability the system lacks — worse than no page.

## The NOT-SCOPED items — assumes / breaks-if-2nd-trades / prerequisite-or-tidy

### N1 — Driver credential resolution (main.py:1546)
- **Assumes:** exactly ONE PM trading account, whose keys are the `KALSHI` (jack) keypair. The broker is built once
  from `secrets.kalshi_api_key_id/private_key_pem`; `pm_account.secret_ref` is never consulted on the PM path.
- **Breaks if a 2nd account trades:** orders for any non-jack account would be placed with JACK'S keypair — on
  jack's Kalshi account, wrong balance, wrong shard. The `secret_ref='kalshi_karen'` we set would be silently
  ignored. (Latent, not live: it only fires if N2 is also changed to iterate a 2nd account — see N2.)
- **Verdict: PREREQUISITE for 2nd-account trading** (a real money-misrouting correctness bug), NOT for pages.
  Cheap in isolation (~5 lines, mirrors main.py:3048's division resolution) but USELESS without N2.

### N2 — The single PM driver task (main.py:1560-1568)
- **Assumes:** one PM division, `account_id=kalshi_jack` from `strategies.yaml`. There is no per-account loop.
- **Breaks if a 2nd account trades:** it can't — karen is never iterated, so her subdivision/attachment (if any)
  would never be copied. A page calling karen "a trading account" would be false. Making karen trade needs a
  second driver task (or a multi-account driver) wired at engine boot = an ENGINE change + restart.
- **Verdict: PREREQUISITE for 2nd-account trading; DEEP** (driver architecture + engine restart). Not for pages.
  ★ This is the load-bearing reason attach-no is safe: the driver structurally cannot trade karen.

### N3 — boot_reconcile's Kalshi/venue side (R-c full-account)
- **Assumes:** the reconciled account is PM-EXCLUSIVE — the venue read returns only PM's positions.
- **Breaks if KAREN trades:** karen's keypair is SHARED with legacy poly_kalshi_mlb. boot_reconcile would compare
  PM's journal vs the whole keypair book (PM + legacy) → every legacy position reads KALSHI_ONLY → mismatch →
  latch on every boot. An armed karen PM subdivision would false-latch immediately.
- **Verdict: PREREQUISITE for KAREN specifically** (co-tenant); for a hypothetical PM-EXCLUSIVE 2nd account it's
  already fine. DEEP + karen-specific (needs legacy off karen's account, or a PM-filtered venue read). Not for pages.

### (related SCOPED-BUT-UNVERIFIED) exposure cap PM-exclusivity
- The caps sum PM's journal (account-scoped, verified) but are BLIND to legacy co-tenant positions on karen's
  keypair. If karen traded, PM would under-count her true venue exposure. Another karen-trading prerequisite.

## What the account pages may HONESTLY claim in this state
- **For jack:** he IS a real PM trading account — the page truthfully shows his PM trading (realized, win/loss,
  open-at-cost, sample size).
- **For karen:** a DISPLAY ENTITY only. No subdivision, no attachment, no driver, no PM orders — and the system
  structurally cannot make her trade via PM (N1+N2), plus she is co-tenant with legacy (N3). Her page must say so
  plainly — e.g. *"0 PM sub-divisions. This account is not traded by Prediction Markets (legacy trades it); the
  view here is display-only."* — NOT an empty P&L frame implying it would fill.
- **The pages must NOT** carry any control that implies you can make an account trade (no arm/attach on the page;
  arm CONTROL is admin-only per R4 and a separate phase anyway). Read-only display + honest-empty is the truthful claim.
- **Honest P&L (Jack's carry):** show realized, the win/loss split, the SAMPLE SIZE, and open-at-cost SEPARATELY,
  with a thin-sample caveat — the same discipline as the farm league's thin-sample flag. jack today: realized
  −$20.24 · 23 settled (7 W / 16 L) · open $13.40 at cost · ⚠ n=23 says nothing about edge. Never let the negative
  hide behind an aggregate; never imply significance a 23-close sample lacks.

## Order of work implied — RECOMMENDATION (Jack picks)
None of N1-N3 is a cheap fix that makes the pages honest — they are all TRADING-path work, and page honesty is
achieved by COPY, not code. Therefore:

**RECOMMEND: ship M2 as DISPLAY-ONLY pages now, with the limitation stated ON them, and make per-account TRADING a
separate later phase.** Rationale:
1. The pages rest entirely on SCOPED-AND-VERIFIED reads (audit) — safe against two accounts today.
2. What makes them honest is the page copy (karen = display-only / not-PM-traded), which is in M2 itself, free.
3. N1-N3 are deep + partly karen-specific (co-tenant); building them now would be trading infrastructure for a
   capability nobody has asked to turn on, ahead of the pages that motivate it.

**The "per-account trading" phase (later, its own authorization), if ever wanted:** N2 (per-account driver) +
N1 (secret_ref→keypair resolution) together, then N3 (karen off legacy OR PM-filtered venue read) before KAREN
specifically — plus the exposure-cap venue-rebase (PM_REQUIREMENTS R7). File; do not build with the pages.

**If instead you want zero chance of a page implying trading:** the display-only copy already prevents that, so no
driver work is needed first. The recommendation stands: pages now (display-only, honest copy), trading later.
