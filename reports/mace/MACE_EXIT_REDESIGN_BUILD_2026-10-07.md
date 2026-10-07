# MACE Exit/Close Redesign — Build Report (2026-10-07)

**Status: BUILT, TESTED, COMMITTED — READY FOR JACK'S FRIDAY 2026-10-09 POST-CLOSE DEPLOY.**
Nothing deployed/restarted/pushed. Branch `mace-exit-redesign-2026-10-09` (worktree
`.claude/worktrees/mace-exit-redesign-2026-10-09`). The three-incident replay + byte-parity gate is
GREEN; per the deploy gate, this is deploy-ready.

---

## 1. What shipped (4 phased commits + 1 design fix, each box-scratch-gated before stacking)

| Commit | Title | Core |
|---|---|---|
| `a2c838a0` | BASE | coherent box-truth tree (fork resolution, §6) |
| `30515d9f` | C1 — taxonomy + un-latch + exit-order-id | **the loop-stopper**: `MaceOrderRejected` exit arm → defer not latch; CLOSING leave-able (reason persistence, re-drive cap→park, guarded reopen); exit-order crash-recovery |
| `eed67ced` | C2 — QuoteSnapshot + closeability gate + stop-basis | **the durable root fix**: one snapshot/tick; positive executable-price gate governs PT/TIME; stop fires on `stop_mark` + prices floored-natural |
| `ce231dd0` | C3 — ride-to-expiry + expiry sweep | first-class RIDE disposition (extra_json, not a status); reconcile expiry settlement |
| `98371435` | C3-fix — sticky ride | C4's replay surfaced a liveness-revive flap; ride is now sticky (clears only by resolving the position) |

**Cumulative source diff vs box-truth `e37fb3cd`:** `config +42 / domain +86 / execution +324 / manager +197 / strategy +80` = **684 insertions, 45 deletions, 5 files**. MACE-scoped only. **No shared files.** No DB migration (uses the existing `mace_rung.extra_json`). Statuses unchanged.

---

## 2. The root flaw and the fix (first principles)

The three incidents were all the same class: the close pipeline **decided on a mid that is unreliable by construction** (a winning condor's far-OTM wing decays to a garbage one-sided quote — that is the *success state* of the trade), **bolted a point-in-time blacklist guard** onto only the PT path, and **committed irreversibly** (CLOSING latch) before any fill. Each patch blacklisted one corruption signature; the next quote wiggle slipped through.

The redesign replaces that with a **positive, executable-price closeability gate** (`strategy.assess_closeability`): a PT/TIME close is attempted only if the long wings are two-sided (sellable) AND the natural debit is within the ladder's cap. A dead wing fails this **deterministically every tick** — no wiggle can slip it. An uncloseable OTM winner **rides to expiry** (Jack's ruling) instead of looping; the reconcile sweep books it worthless (full credit kept). CLOSING is no longer absorbing: a reject defers, a committed close caps+parks, a wedged rung reopens. The old mark-trust guard is **retained** as defense-in-depth for a corrupt *short*-leg mid the gate cannot see.

---

## 3. Three-incident replay + byte-parity (THE DEPLOY GATE — all GREEN)

`tests/test_mace_exit_redesign_replays.py`:

| Replay | Assertion | Result |
|---|---|---|
| **2026-09-18** stale/frozen mark | mark-trust guard HOLDS (`reason=frozen`), no close, OPEN | ✅ |
| **2026-10-02** inverted wing, no sibling | guard HOLDS (`reason=leg_inversion`), never CLOSING | ✅ |
| **2026-10-05** wiggle slips the guard | guard PASSES → **gate blocks** (`natural_above_cap`) → RIDE tick 1; 6 ticks: exactly ONE `mace_ride_enter`, **zero place calls** (loop structurally gone), stays open+riding | ✅ |
| **2026-10-05** un-latch alone | gate DISABLED + `MaceOrderRejected`×5 on a winner → `deferred`, **never CLOSING** (proves C1 kills the latch without the gate) | ✅ |
| **byte-parity** liquid winner | winner-ladder limit walk == deployed formula `[0.50,0.53,0.55,0.58,0.60]` (anchor 0.50, 5×, band 0.10, tick 0.01) — normal closes byte-undisturbed | ✅ |

Byte-parity is also proven at the source: `git diff e37fb3cd HEAD -- execution.py` shows the winner-ladder formula lines **unchanged**; only the committed *marketable* branch swapped strict `_natural_debit` → `_natural_debit_floored` (byte-identical on two-sided wings, floors only dead wings on the ungated risk path).

**Full local suite: `348 passed, 4 failed`.** The 4 are PRE-EXISTING stale asserts (config `max_contracts==1` vs box's 2; sizing/entry `SIZING_BASE_BP_PCT`) — identical on pristine 10-02, **0 new**. (The build also *fixed* a 5th pre-existing failure, `test_p14`, a stale `_MarkExec.close_rung` stub.) New tests: `test_mace_exit_unlatch` 11, `test_mace_closeability` 18, `test_mace_ride_expiry` 7, `test_mace_exit_redesign_replays` 5 = **41**.

Local env: a dedicated py3.12 venv (`.venv-macetest`, matches box). The 6 web-layer mace tests (`*_web_*`, `halt_button`, `pulse`, `view_enrich`, `division_shell`) need the full web/broker stack absent from the source-only snapshot lineage; they are out of the exit/close blast radius and run on **box-scratch** (full tree) as the Friday gate's final step.

---

## 4. Config / safety / rollback

- **config_hash `931a8214be50` PRESERVED** — all knobs (`CloseabilityConfig`) default in code; **zero `config/mace.yaml` change**. The graft ships the 5 `.py` source files only; yaml/tests/web never graft. Verified.
- **Safety model (corrected per the Halt analysis):** `/mace` Halt stops **ENTRIES ONLY** — manage/exit always run, so Halt is **not** a close-path kill-switch (do not frame it as one). Rollback of first resort = **backup-restore graft + restart**. `closeability.enabled=false` = **SELECTIVE revert** (boot-time config: yaml add + restart; disables only the gate/ride, keeps the un-latch) — not a live switch.
- **Failure direction is HOLD-shaped** (safe): the redesign's error is "didn't close when it maybe could" → a defined-risk OTM rung rides. It never opens risk or force-closes blindly.

---

## 5. Friday deploy (Jack-reserved: DEPLOY + RESTART)

Graft **5 files** `trading_corp/mace/{domain,execution,strategy,manager,config}.py` + restart. Box-only, **no FF-push** (prod-live divergence → reconcile later).

- **Graft target md5s** (CR-stripped, what the box files must BECOME): manager `ccd02002` / strategy `b147e59d` / execution `9cc1c165` / config `d441957f` / domain `8522dc3a`.
- **Drift gate** (what the box files must BE before the graft): manager `31357a78` / strategy `50dd6984` / execution `5b2307bc` / config `01117cca` / domain `877a2c72` (box-truth). Runner aborts on any mismatch (scp+tar, staged-hash==target gate, backup→apply→verify CR-sha→py_compile→rollback-on-mismatch).
- **Acceptance** (first manage tick post-restart): the wedged XLE reset rung self-heals — `mace_closing_reopen` fires (or it rides), **zero new Tracebacks**, and a liquid close shows byte-parity ladder limits. Interim XLE reset is NOT recommended (it re-wedged once; the design self-heals it).
- Runners staged alongside (present as one-line `powershell -ep bypass -f .\NAME.ps1`; **I do not run DEPLOY/RESTART**).

---

## 6. Build note — the base-commit fork (resolved, logged)

The plan said "branch off `e37fb3cd`", but that commit is the mace-dedupe DEPLOY LEDGER sitting on the `5a2023ed` box-truth **source snapshot** — which captured only `trading_corp/mace/*.py` (17 files) onto an old PM-logos commit. That tree is incoherent for building/testing (`db.py` SCHEMA lacks `mace_rung`, `config/mace.yaml` + the `test_mace_*` suite + `trading_corp/web/mace_view.py` all absent → 43+ `no such table` errors). Resolution: based the branch on `mace-leg-sanity-guard-2026-10-02` (tip `694e5090`, a coherent full tree whose mace source is byte-identical to `e37fb3cd` **except** manager.py) + overlaid `e37fb3cd`'s manager.py. Result: all 5 source files on box-truth (`git diff e37fb3cd BASE -- trading_corp/mace/` was EMPTY), on a testable tree. The graft ships the 5 files regardless of git ancestry, so box `config_hash` is untouched. Review diff: `git diff e37fb3cd HEAD -- trading_corp/mace/`.

---

## 7. Decisions flagged for Jack (defaults chosen; reversible)

1. **TIME exit at DTE≤14 on a dead wing now RIDES** instead of force-closing marketable (explicit reversal of the "some close beats carrying to expiry" comment, per the ride-to-expiry ruling). The gate blocks it; the expiry sweep books it.
2. **Stop basis change** (`stop_mark`: wings floored to bid-or-0): fires stops ~a half-spread earlier on liquid names (cents) and **un-blinds dead-wing stops** (a latent pre-redesign bug — a garbage wing mid deflated the mark and hid the stop). Conservative (risk-reducing direction).
3. **Riding rungs keep counting against capacity / weekly budget** (unchanged; they still carry `max_risk`).
4. **Expiry sweep books `expired` at debit 0 / realized = full credit** only when NO same-expiry legs remain on the account; if legs remain (possible assignment), it warns ONCE and leaves the rung for manual resolution (a no-HITL engine must not guess at settlement).
5. Later optional config-only deploy could document the `management.closeability` knobs in `mace.yaml` (config_hash changes then — listed explicitly at that time).
