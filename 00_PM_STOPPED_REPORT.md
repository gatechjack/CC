# PREDICTION MARKETS — "IT STOPPED WORKING": FORENSIC HEALTH REPORT

**Date:** 2026-11-17 (03:00–03:25 UTC probes) · **Mode:** READ-ONLY (nothing restarted / deployed / armed /
disarmed / latch-cleared / written / promoted / pushed) · **Branch:** `pm-health-stopped-2026-11-16` (off
`origin/prod-live` `f82d5be6`) · **Box:** `tc-prod-vm`, engine PID **809209**.

All box access went through sanctioned read-only `.ps1` runners streamed over ssh-STDIN (`sqlite3 mode=ro`,
`systemctl show`, `journalctl`, `md5sum`, `df`). Evidence files: `analysis/pmh{1..5}.{sh,ps1}`.

---

## TL;DR — THE HONEST ONE-PARAGRAPH ANSWER

PM stopped from **two overlapping failures, and the bigger one is division-wide.**

1. **Kalshi location-attestation expired on a rolling schedule.** `kalshi_trey` threw `403: Your location
   attestation for API trading is missing or expired` on the order path at **2026-10-22 09:31:47Z** and
   auto-latched all 22 of its sub-divisions; `kalshi_marc` did the identical thing **six days later at
   2026-10-28 09:01:29Z**. Both accounts are `latched / manual_exit_required`. `kalshi_jack` and `kalshi_karen`
   never 403-latched, **but their attestations very likely expired too** around 11-03 / 11-09 (the ~6-day
   stagger continued and lines up exactly with their last fills) — **unconfirmed**, because no order has been
   attempted since to trigger the 403, and the code has *no* way to read key expiry. Jack must check all four in
   the Kalshi UI.

2. **Polymarket retired its Data API v1 (HTTP 410) in early November,** killing the whale `/positions` feed that
   is the **only** entry-signal source. Every `/positions` fetch now returns
   `410 {"error":"Data API v1 has been retired","migration":".../v2/"}` — **~19 failed calls/second, continuous**.
   This is why even the two *healthy, armed* accounts (jack, karen) produce **zero** copies: there are no signals
   to copy. **Nothing trades until the signal client is migrated to Data API v2**, regardless of attestation.

The engine itself is **healthy** (PID 809209, NRestarts 0, prod-live PM code byte-identical, disk/WAL fine, zero
ERROR-priority log lines). **All four accounts are completely flat** — every contract entered has been
exited/settled, **zero open positions, zero unbooked settlements** — and **cash is safe and slightly up**
($297 / $390 / $44 / $54). The freeze has cost ~nothing in stuck capital; the division is a known small net-loser,
so being offline has, if anything, saved fees.

**Shortest safe path back:** (a) Jack renews the attestation on **all four** accounts in the Kalshi UI and
records each expiry; (b) an agent builds + tests a **Polymarket Data API v2 migration** of the signal client —
the real blocker — for Jack to deploy; (c) from rebuilt pre-incident arm lists **Jack chooses**, clear-latch +
re-arm marc/trey via `pm_cli`. **No emergency restart is needed** (all flat); the only restart is the v2 deploy,
which is safe precisely because the book is flat.

---

## PHASE 1 — "STOPPED," PRECISELY, PER ACCOUNT

| Account | Classification | Last FILLED entry | Last order attempt | Arm state now | Loop |
|---|---|---|---|---|---|
| **kalshi_jack** | **running, armed, NOT placing** (no signal) | 2026-11-02 20:07 | 2026-11-08 06:37 | 18 armed · 8 stale-boot-latched (10-06) · 0 auth-latched | ALIVE, evaluating |
| **kalshi_karen** | **running, armed, NOT placing** (no signal) | 2026-11-02 17:34 | 2026-11-03 06:01 | 16 armed · 6 stale-boot-latched (10-06) · 0 auth-latched | ALIVE, evaluating |
| **kalshi_marc** | **running but LATCHED** (auth) | 2026-10-28 03:40 | 2026-10-28 09:01 | **22 latched auth_failure** (10-28) | ALIVE, evaluating |
| **kalshi_trey** | **running but LATCHED** (auth) | 2026-10-22 04:48 | 2026-10-23 03:34 | **22 latched auth_failure** (10-22) | ALIVE, evaluating |

No account is "placing but not filling" and no account's loop is dead. Zero signals currently (`n_signals=0`
everywhere, probe 1 §9) — **because the signal feed is dead** (Phase 2 / H5), not because the loop isn't running.

**1.2 Arm state, raw (legacy `agent_state`, `agent='pm_live'`, 93 keys)** — probe 1 §7:
- `arm:global` = **armed:true** (since 2026-08-31). Global master is ARMED.
- **Incident timeline, latches grouped by trigger × timestamp:**

| When (UTC) | Trigger | Scope | Count |
|---|---|---|---|
| 2026-10-06 02:57:01–03 | `boot_reconcile_mismatch` | jack×8 + karen×6 | 14 (stale; never re-armed) |
| 2026-10-07 04:18:55–04:19:06 | *(armed)* | jack×18 + karen×16 | 34 ARMED (current live set) |
| **2026-10-22 09:31:47** | **`auth_failure`** `manual_exit_required` | **kalshi_trey × 22** | 22 |
| **2026-10-28 09:01:29** | **`auth_failure`** `manual_exit_required` | **kalshi_marc × 22** | 22 |

Totals: **34 armed / 58 latched** of 92 subs (+ global). The 58 latched = 14 stale-10-06 + 22 trey + 22 marc.

**1.3 Error rows since 2026-10-16** (probe 2 §2/§3). All are standard HTTP codes on the **V2** endpoint
`POST /portfolio/events/orders`; grouped verbatim heads:
- `403: Your location attestation for API trading is missing or expired` — **trey 10-22 (KXITFMATCH…), marc 10-28
  (KXCS2GAME…) ONLY.** These two are the latch triggers.
- `400: insufficient balance` — marc (10-18, 10-26). `404: market not found` — all accounts, stale markets
  (10-21 MTUFIU; marc 10-25). `409: trading is paused` (10-15, jack atp 10-22). `503: service unavailable` —
  jack+karen 10-29 (Kalshi transient). **jack & karen have NO attestation 403.**

**1.4 Heartbeats** (probe 1 §8/§9): `pm_driver_task_heartbeat` fresh for all four (age 0–12 s) → the per-account
driver task is alive for all, including latched marc/trey. `pm_driver_heartbeat` `state=evaluated`, age <15 s,
`n_signals=0`, `placed=0` across the board → **loops run and evaluate; they place nothing because there are no
signals.** One fossil: `kalshi_jack:nba` last evaluated 2026-10-23 (24.5 d stale) — see Phase 4.

---

## PHASE 2 — CAUSE, TESTED AGAINST EVERY HYPOTHESIS

| # | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| **H1** | Attestation lapsed on other accounts too | **CONFIRMED (marc); LIKELY (jack/karen), UNCONFIRMED** | marc verbatim `403 … location attestation … missing or expired` @ 2026-10-28 09:01 (probe 2 §1/§3), account-wide `auth_failure` latch (`arm.py:54`, `live_driver.py:1056`). jack/karen: no 403, last fills 11-08/11-03 align with the ~6-day stagger (trey 10-22→marc 10-28→~11-03→~11-09) but **no order attempt since to prove it; no `/api_keys` read exists in code** → only the Kalshi UI resolves it. |
| **H2** | An engine restart tripped `boot_reconcile_mismatch` | **RULED OUT** | Engine WAS restarted 2026-10-27 05:16:42Z (MACE entry-partialfix deploy; PID 678426→**809209**, NRestarts 0). But **zero new `boot_reconcile_mismatch` latches after 2026-10-06** (probe 1 §7). The restart created no PM latch. |
| **H3** | Kalshi deprecated v1 `/portfolio/orders` / `taker_side` | **RULED OUT** | Broker already on **V2** `/portfolio/events/orders` with `side` (not `taker_side`) since 2026-06-30 (`kalshi_live.py:68,177-188,381`). All order errors are standard HTTP codes on V2; no 410/v1 (probe 2 §2). |
| **H4** | Engine / box unhealthy | **RULED OUT** | PID 809209 active/running, NRestarts 0; disk 86 % / 8.8 G free (flat); legacy WAL **4.1 MB** (was 3.47 G — resolved), PM WAL 0.8 MB; **zero ERROR-priority journal lines** (probe 4 §1); Kalshi balance reads succeed every cycle → Key Vault auth fine. |
| **H5** | The Polymarket side stopped | **CONFIRMED — THE DIVISION-WIDE BLOCKER** | Whale `/positions` returns `HTTP 410 "Data API v1 has been retired"` — continuous, **~19/sec, 2.97 M in 44 h**, every wallet/category (probe 3 §3, probe 4 §3). Code hits the retired v1 host with no 410 handling (`polymarket_data_api_client.py:48,489`). `n_signals=0` everywhere; paper captures ceased 2026-11-02 (probe 5); live fills cliff 11-02→11-08. |
| **H6** | Funding too low (gate 6b) on marc/trey | **RULED OUT as cause** | marc had `insufficient_balance` 400s (10-18, 10-26) and low balance ($44), but marc filled fine through 10-28 03:40 and stopped on the attestation 403, not funding. Note as a *downstream* constraint when marc resumes. |
| **H7** | Something else | **n/a** | The two confirmed causes (H1 attestation, H5 Polymarket v1 retirement) fully explain all four accounts. |

**Root-cause summary:** *marc + trey* are stopped by **expired Kalshi attestation** (order-path 403 → account-wide
auth latch). *jack + karen* are stopped by the **dead Polymarket signal feed** (and their attestation may also be
expired but it's moot while there are no signals). The Polymarket v1 retirement blocks **all four**.

---

## PHASE 3 — WHAT THE FREEZE DID TO THE MONEY

**3.1 Cash now** (probe 2 §7 / probe 3 §2, snapshots fresh @ 2026-11-17 03:12, `has_breakdown=1`, 284
snapshots/24h each — reads succeed for **all four, incl. the dead-key accounts**):

| Account | Total | shard0 | shard3 | (shards 1,2) | vs 10-22 baseline |
|---|---|---|---|---|---|
| kalshi_jack | **$296.63** | 146.81 | 149.82 | $0 | $232.87 → ↑ |
| kalshi_karen | **$390.08** | 208.94 | 181.14 | $0 | $358.37 → ↑ |
| kalshi_marc | **$44.29** | 2.77 | 41.52 | $0 | $33.45 → ↑ |
| kalshi_trey | **$53.53** | 2.02 | 51.52 | $0 | $51.53 → ↑ |

**3.2 Open positions & unbooked settlements — NONE.** Engine `_HELD_SQL` grain (probe 3 §1):
`ct_entered == ct_exited` **exactly** for all four (jack 9690/9690, karen 6355/6355, marc 1562/1562,
trey 1451/1451); zero held legs. **Every contract entered has been exited or settled.** → No settled-but-open
positions; nothing waiting to latch at the next restart.

**3.3 Reconcile:** journal signed-net = 0 for every account (flat). A venue reconcile needs the service key
(engine-only, not RO-runnable here), **but** Kalshi reads succeed for all four (balances fresh), the accounts are
flat, and the last engine boot-reconcile (2026-10-27) latched nothing → **a fresh reconcile would be CLEAN.**

**3.4 Settlement watch (since 2026-10-16):** only `settlement` (1201) and `opposed` (268) close sources —
**zero `settlement_<nonstandard>` classes, zero `n_nonstandard`** (probe 2 §10). The 2026-10-08 non-standard-class
fix never had to fire; no mis-booked settlement.

**Realized P&L since 10-16** (probe 2 §11b): jack −$152.86 / karen −$67.80 / marc −$21.66 / trey −$21.53 ≈
**−$264 total.** Consistent with the division being a known fee-dragged net-loser (10-16/10-17/10-22 backtests).
**Net cost of the freeze ≈ zero** — capital is flat and safe; being offline likely *saved* fees.

---

## PHASE 4 — WHAT CHANGED WHILE JACK WAS AWAY

- **Engine:** restarted **2026-10-27 05:16:42Z** (PID 678426 → **809209**, NRestarts 0) — the MACE
  entry-partialfix deploy bounced the shared engine. Benign for PM (no latch). `pm_web` unchanged (PID 698062,
  2026-10-11).
- **`origin/prod-live`:** `2ffa1d20` (baseline) → **`f82d5be6`** (MACE entry-partialfix, 10-27). A MACE-only
  advance — **no PM file changed.** Box PM files are **byte-identical** (CR-stripped md5) to prod-live for all 11
  checked (execution `ae7eda1f`, live_driver `0a9d2b2c`, settlement `5e019591`, arm `2f4a81db`, boot_reconcile
  `4d68e094`, heartbeat `d1dfd8e8`, db `1c13c64e`, driver_roster `ccd70d31`, kalshi `c21c48d0`, kalshi_live
  `5c1a3551`, pm_cli `fabe1e5f`) — **no PM drift** (probe 3 §4). Box is not a git checkout (file-graft deploys).
- **Schema head:** 24 (migration 024 `pm_subdivision_attachment_event`).
- **Attachments** (probe 2 §12): 10-23 trey:nfl & jack:nba detaches; 10-26 a coordinated nfl-whale cleanup
  (jack/karen/marc/trey detach `0xd38b71f3…`/`0x226bf1…`); 10-28 jack:nba +2 whales; 11-02 jack:cs2 +1,
  karen:cfb −1. Active now: jack 55 / karen 32 / marc 32 / trey 32. None of this stopped the division.
- **Other divisions (one line, no recommendation):** MACE entry-partialfix was deployed+FF'd to prod-live on
  2026-10-27 (the restart above); that is the only shared-engine change in the window.

---

## PHASE 5 — RECOMMENDATIONS (ranked; act on nothing)

> **Sequencing matters:** attestation and the v2 migration are *independent* blockers. marc/trey need **both**;
> jack/karen need the v2 migration (and probably attestation). Re-arming is the LAST step and only after both.

**R1 — [JACK] Renew the Kalshi location attestation on ALL FOUR accounts, and record each key's expiry.**
*Why:* trey+marc are CONFIRMED expired; jack+karen are likely expired and re-doing it is harmless. Nothing marc/trey
does works until this is renewed (re-arming before renewal re-latches on the first order). *Where:*
kalshi.com → Account & security → API Keys (location attestation). *Also record* each key's
`api_key_region_expiration_ts` to calendar the next renewal — the engine is **blind** to it (no `/api_keys` read
exists). *Risk:* none (UI action). *Order:* first.

**R2 — [AGENT builds + tests → JACK deploys/restarts] Migrate the Polymarket signal client to Data API v2.**
*Why:* **the division-wide blocker.** `polymarket_data_api_client.py` calls the retired v1 host
(`data-api.polymarket.com` `/positions`, `/v1/leaderboard`, `/activity`, closed-positions, resolutions) → HTTP 410.
Until migrated to `data-api.polymarket.com/v2/`, **no account produces a single copy, armed or not.** *Scope
(build, don't guess):* map each v1 call to its v2 equivalent + response schema, add explicit 410 handling, re-run
the Kalshi matchers against v2 payloads, box-scratch the ingest path. *Risk:* medium — touches the whole
division's signal ingest; needs careful schema mapping + tests. This is a real engineering task, not a one-liner.
*Order:* second (longest pole; start in parallel with R1).

**R3 — [AGENT authors runner; JACK chooses the list] Rebuild the pre-incident armed set for marc & trey, then
clear-latch + re-arm.** The pre-latch armed set is *not recorded* (a latch overwrites it, `arm.py:367-371`), so
reconstruct it from fills before each latch and the attachments. Present per account as **(traded pre-incident /
stale 10-06 boot-latch / never armed)** lists for **Jack to choose** — *arm state is a board decision; no agent
judgment on which category to arm.* Author (do not run) a `pm_cli live-arm --clear-latch` runner over Jack's
explicit list that reads arm state back afterward. *Precondition:* R1 done (else re-latch) **and** R2 live (else
armed subs still get no signal). *Order:* third.
  - marc/trey **traded pre-incident** (fills before the latch) = effectively all 22 categories each (they filled
    daily through their latch date). The stale-10-06 boot-latches are jack/karen only. Final list is Jack's.

**R4 — Restart only as part of the R2 deploy; verify flat first.** A restart is **safe right now** (zero open
positions, reads work, reconcile would be clean) but **not needed** — R1 + the running loop self-heal without one
(the 600 s in-loop settlement scan and per-order arm re-read need no restart). The v2 deploy *will* require a
restart; it is safe **because the book is flat**. ⚠️ Still re-verify **zero open positions and zero settled-but-open**
immediately before any restart (re-run probe 3 §1) — that is the standing precondition that prevents the next
account-wide boot-reconcile latch.

**R5 — Prevention (scope each; build none here):**
- **(a) Daily attestation-expiry check** — read each key's `api_key_region_expiration_ts` via `GET /api_keys` and
  alarm at T-7 / T-3 / T-1 days. Currently **zero** visibility; this is exactly what let trey→marc→(jack/karen)
  lapse one by one unnoticed.
- **(b) Per-account no-fill alarm** — one account at zero fills while siblings fill, OR all accounts at zero for
  > N hours. Would have flagged both the staggered attestation lapses and the Polymarket feed death within hours.
- **(c) Feed-health alarm on `/positions`** — consecutive 410/failure streak → alert. Would have caught the v1
  retirement on day one instead of ~2 weeks later (and would stop the current ~19 calls/sec against a dead
  endpoint).

---

## ANOMALIES SURFACED (not auto-resolved)

1. **trey not re-latched at the 10-27 restart.** Code says a dead-key account's boot-reconcile 403s → latches
   `boot_reconcile_mismatch` (`boot_reconcile.py:217-224`; no "already-disarmed" early-out), which would have
   *overwritten* trey's auth_failure row with a 10-27 timestamp. trey still shows `auth_failure` @ 10-22, so its
   boot-reconcile did **not** run/latch at that boot. The 10-27 boot journal is beyond the ~2-day retention
   (earliest retained line 11-15 07:46), so logs can't confirm why. Not material to the diagnosis (zero new PM
   latches either way) — flagged for awareness.
2. **`kalshi_jack:nba` driven-stale.** Last evaluated 2026-10-23 (the day its original whale was detached), yet
   two new whales were attached 2026-10-28 — but it has not been evaluated since. The driver's per-account
   category set appears fixed at task spawn / last restart, so a category that *gains* attachments after the last
   restart is not driven until the next one. jack:nba is also boot-latched (disarmed), so benign now — but it is a
   latent "new category won't trade until restart" gap.
3. **~19 Polymarket-410 calls/sec (2.97 M/44 h).** Harmless to the engine (WARNING-level, fail-soft) but wasteful
   and a mild risk of the shared prod IP being rate-flagged. Resolved by R2.
4. **karen `exit_rows` 1353 vs `entry_rows` 1352** (one extra exit row; contracts still net to zero) — benign
   bookkeeping artifact, no open position.

---

*Read-only investigation. Nothing on the box was changed. Report + probes committed to branch
`pm-health-stopped-2026-11-16`; not pushed.*
