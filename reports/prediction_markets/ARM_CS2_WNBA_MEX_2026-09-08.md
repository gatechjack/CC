# Arm cs2 / wnba / mex — session ledger (2026-09-08)

**Branch:** `pm-arm-cs2-wnba-mex-2026-09-08` (base `fb3f858`, the deployed-PM-code tip).
**Task:** Jack attached whales to three more categories (cs2, wnba, mex) and wants them live + armed.
**Sequence (each a separate authorization):** verify attachments → close/override the arm-gate → normalize sizing → engine restart (Jack) → pre-arm signal counts → arm.

## ★ GIT-TRUTH STATUS — no prod-live advance (correct, not skipped)
This session changed **zero box code files**. The cs2/wnba/mex matchers + wiring were deployed in the
09-07 rungs and already reconciled onto `prod-live` (`86204ef8` "1d PM"). The only live changes are
**DB writes** (sizing flip done; arm rows pending) — read-per-cycle state, **not git-tracked**, explicitly
outside both the git-truth reconcile and the code freeze. `prod-live` stays `a24b8bf`, `main` stays `61de372`.
Runners are read-only tooling in `cc\` (ruled not part of git-truth). Durable record = this ledger on this
branch only; **not merged to main/prod-live**.

## ★ THE ARM-GATE OVERRIDE (recorded, by Jack, 2026-09-08)
mex and wnba were held behind an ARM-GATE (their build-time real-market dry-runs were INCONCLUSIVE — never
exercised, no live-window whale position). Jack chose to proceed knowingly today. Outcome per category:
- **wnba — gate CLOSED PROPERLY by a fresh dry-run** (not overridden). See below.
- **mex — armed as a guard-backed OVERRIDE** (whale currently holds no live Liga MX; nothing to dry-run today).
- **cs2 — never under the gate** (dry-run-proven at build).

## 1. Attachments verified (read-only snapshot, engine PID 232440, boot 2026-09-07 15:56:01Z)
| Account/cat | Wallet | Active | Book verdict |
|---|---|---|---|
| kalshi_jack/cs2 | 0xd27cc742… | yes | 14 LIVE cs2 (Vitality/FURIA, TYLOO, PARIVISION/MOUZ) — **Counter-Strike CONFIRMED** |
| kalshi_karen/cs2 | 0x1d2efa18… | yes | 23 cs2 (BIG/B8, TheMongolz, BetBoom) — **Counter-Strike CONFIRMED** (snapshot ~8d stale) |
| kalshi_jack/wnba | 0xe8c4d68a… | yes | 15 live wnba (Mystics/Sparks, GSV/Lynx) — confirmed |
| kalshi_karen/wnba | 0x684baa57… | yes | 54 live wnba (Sky/Storm, Lynx spreads) — confirmed |
| kalshi_jack/mex | 0x629c2844… | yes | **0 open mex, 16 closed mex** — real by history, nothing live now |
| kalshi_karen/mex | 0x629c2844… | yes | same wallet — 0 open mex, 16 closed |

Book verdict is a distinct fact from "attached" (the promote UI does not check it). cs2 is genuine
Counter-Strike esports on both accounts — not something else with a similar name.

## 2. Roster / restart (CORRECTED mid-session)
`active_driver_subdivisions` (a *live* query) showed all 6 spawned — misleading. The RUNNING task's category
list is fixed **at boot**. Attachment `added_ts` vs boot epoch (1788796561):
- **cs2**: jack 1788872398 / karen 1788874487 → **AFTER boot → NOT in running cats → CATEGORY_STARVED (no heartbeat) → RESTART REQUIRED.** Not a matcher/catalog fault: 138 open KXCS2GAME markets + whale holds live cs2.
- **wnba / mex**: all `added_ts` < boot → already cycling → **no restart needed for them.**

## 3. wnba dry-run — GATE CLOSED (proven, not overridden)
Attached whales' wnba bets vs live KXWNBAGAME (316 markets fetched; **0 currently open** → wnba won't *fill*
until games are listed, but the map is provable vs finalized): **43 matched, 0 wrong-game, 0 wrong-market-type.**
Cross-venue aliases verified on real data: `wnba-gsv-min → …GSMIN-GS`, `wnba-por-dal → …PDXDAL-DAL`,
`wnba-por-tor → …PDXTOR-TOR` (GSV→GS, POR→PDX all map to the right club).

## 4. n_signals interpretation (correction to the brief's framing)
`n_signals` = the attached whale's **whole-book genuinely-open count** (a `/positions` fetch is wallet-wide),
identical across a whale's categories, NOT the per-category matched count. Proof: mex `n_signals=69` on both
accounts (same whale) despite **0 mex positions**. So nonzero `n_signals` proves the whale is active, not that
the category matched — the authoritative "matcher+map works" measure is the dry-run matched count (wnba: 43).

## 5. Sizing normalized (DB write, verified)
Flipped **4 rows** (mex+wnba × jack/karen) `fixed → contracts=5`. **cs2 was already `contracts`** (only mex+wnba
were `fixed` — the brief's "all three fixed" was stale; POST-vs-PRE not POST-vs-DEFAULT caught it). POST-vs-PRE:
all 4 resolve contracts=5; every other row (incl cs2 + originals) byte-identical; snapshot-nontarget sha
`1ef99e94df451fc2` unchanged. Backup `~/pm_sizing_backup_20260908T195617Z.json`. Resolved caps: per-order $25 /
daily $150 / open $350.

## 6. Read-back guard (fill-watch) extended
`cc/pm_fill_watch_ro.sh`: added **wnba** to the STRUCT dict `{GSV,POR,GS,PDX}` (both-teams `teams_ok` + the
cross-venue-alias flag). cs2 (exact-normalized orgs + academy/fe/NXT/Ares/ex- flags) and soccer/mex (leg + -TIE
+ both clubs + PARIS) were already covered — no redundant guard.

## Runners (read-only tooling, `cc\`)
- `pm_arm_snapshot_ro.{ps1,sh}` — attachments + whale book + roster + liveness(n_signals) + arm + sizing PRE. Re-runnable.
- `pm_wnba_dryrun_cs2diag_ro.{ps1,sh}` — wnba dry-run (gate) + cs2 attach-vs-boot root-cause + KXCS2GAME probe.
- `pm_sizing_mexwnba.{ps1,sh}` — the sizing write (backup + 4-row guard + POST-vs-PRE proof).
- `pm_fill_watch_ro.{ps1,sh}` — fill-watch, wnba guard added this session.
- `pm_global_disarm.{ps1,sh}` — emergency master kill (pre-authorized on a wrong fill).

## 7. Engine restart (Jack) + post-restart verify — DONE
Jack restarted (PID 232440 → 276575, boot 2026-09-08 20:24:38Z). Post-restart snapshot: **alarms=0**
(cs2 starvation RESOLVED — jack/cs2 RUNNING n_signals=2, karen/cs2 IDLE n_signals=0=whale-empty). All 6
targets SPAWNED + RUNNING/IDLE, all now contracts=5, arm rows ABSENT/no-latch, non-target sha unchanged.

## 8. ARMED — DONE (final write)
All 6 subs (cs2/wnba/mex × jack/karen) armed via `arm.arm(by=jack)`. POST: each effective_armed=True,
latched=False; GLOBAL armed=True. Backup `~/pm_arm_backup_20260908T203633Z.json`. Armed order wnba+mex
(won't fill) then cs2 (hot). **Expected fills:** cs2/jack is the only one likely to place soon (live markets
+ whale holds live cs2); wnba (0 open markets) + mex (whale empty) won't fill until their conditions return.

## STANDING SAFETY POSTURE (post-arm)
- **First fill from any new cat = read-back BEFORE anything:** `cc/pm_fill_watch_ro.ps1` (cs2 exact-org +
  academy/fe/NXT/Ares/ex- flags; wnba GSV/POR added this session; mex leg/-TIE/both-clubs).
- **Wrong org/team/club/leg on a fill = FIRE FIRST:** `cc/pm_global_disarm.ps1` (pre-authorized), report second.
- **A guard that CANNOT READ the whale's bet is INCONCLUSIVE, not a mismatch** — do not disarm on an unreadable read.
- Nothing polls; Jack runs the reads when he wants them.

## GIT-TRUTH (final)
No prod-live/main advance — zero box code changed; sizing + arm are DB writes (read-per-cycle, outside git-truth
+ the code freeze). `prod-live` a24b8bf / `main` 61de372 untouched. Record = this ledger on branch
`pm-arm-cs2-wnba-mex-2026-09-08` only.
