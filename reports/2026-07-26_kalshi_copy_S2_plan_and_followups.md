# kalshi_copy_trading — S2 Build Plan + Dormancy + TypeError follow-ups

**Date:** 2026-07-26 · Read-only diagnosis + build-plan (NO build this session). Companion to `2026-07-26_kalshi_copy_trading_roster_review.md`.

---

## Follow-up 1 — SESSION 2 BUILD PLAN (scope + readiness; do NOT build yet)

Three fixes make the Kalshi copy dashboard reflect live reality and make autopause functional. All loci verified by code map.

### Fix (a) — copyability counts live copies, not just paper `would_have_placed`
- **File:** `trading_corp/web/data.py` · `_query_kalshi_whale_intel` (copies-numerator query, ~lines 5311–5321)
- **Current:** `... WHERE actor='kalshi_copy_trader' AND kind='would_have_placed' AND ...` — froze at go-live (live copies emit `kalshi_copy_placed_live`).
- **Change (1 line):** `AND kind IN ('would_have_placed','kalshi_copy_placed_live')`. (Including `kalshi_copy_no_fill` as an "attempted copy" is an optional design call — minimal fix is the two-kind IN. Both kinds carry `whale_handle` in payload, confirmed.)
- **Layer:** WEB-only. **Test:** `tests/test_kalshi_whale_intel.py` (`test_copies_counted_from_would_have_placed` — retitle + seed a `placed_live` row).

### Fix (b) — write structured `whale_handle` into live `kalshi_round_trips.extra_json`  ← the autopause-enabler
- **File:** `trading_corp/agents/kalshi_resolver.py` · `_compute_round_trip_row` (extra_json build, ~lines 256–274)
- **Root cause (sharper than the 07-20 note):** the real split is **NOT** paper-vs-live. Whale-**closed** exits go through `_pair_pending_exits` (~L404–413) which **does** set `whale_handle`. Market-**settlement** rows (held to expiry) go through `_compute_round_trip_row`, which **omits** it — for **both paper AND live**. So autopause (which keys on `extra_json.$.whale_handle` in `_whale_autopause._query_whale_stats` ~L109) is blind to every settlement-path row.
- **Change (1 line):** add `"whale_handle": row.get("whale_handle")` to the extra_json dict. `row` is the parsed audit payload, which carries `whale_handle` for both `would_have_placed` and `kalshi_copy_placed_live` sources.
- **Layer:** **ENGINE CORE** → requires a restart to deploy. **Test:** `tests/test_kalshi_resolver.py` (`test_resolve_books_live_copy_placed_live_row` — add `whale_handle` assertion).
- **★ BACKFILL CAVEAT:** this fixes GOING-FORWARD rows only. The **15 existing live round-trips will still lack structured `whale_handle`** (handle is only in their free-text rationale). Autopause will therefore see **no live history** until either new settlement rows accrue OR a one-off backfill (parse rationale → extra_json.whale_handle) is run. This is a separate, optional step — reinforces "accumulate."

### Fix (c) — epoch-scope the per-whale panel to match the tile
- **File:** `trading_corp/web/data.py` · `_query_pm_whales` (~L4676–4757); call site ~L5690.
- **Current:** the tile (`_query_pm_resolved_stats`) applies `_kalshi_copy_mode_clause(mode, epoch, 'entry_ts')`; the per-whale panel (`_query_pm_whales`) does **not** — it aggregates full history. Epoch source: `KALSHI_COPY_LIVE_EPOCH="2026-07-01T14:08:58+00:00"` (overridable via `agent_state(kalshi_copy_trader, metrics_epoch)`), already computed at ~L5683.
- **Change:** add `kalshi_copy_mode`/`kalshi_copy_epoch` params to `_query_pm_whales`, apply the clause to its WHERE, pass them at the call site.
- **Layer:** WEB-only. **Test:** `tests/test_kalshi_whale_intel.py` (add `test_pm_whales_epoch_scopes_kalshi_round_trips`).

### Readiness summary
| Fix | Layer | Restart? | Test file |
|---|---|---|---|
| (a) copyability kind filter | web/data.py | web-layer bounce | test_kalshi_whale_intel.py |
| (b) whale_handle → extra_json | **agents/kalshi_resolver.py (core)** | **yes** | test_kalshi_resolver.py |
| (c) panel epoch scope | web/data.py | web-layer bounce | test_kalshi_whale_intel.py |

- All three are small, localized changes; test files all exist. **Bundle them** (same recorder/dashboard area) into one deploy with a restart (fix b is engine-core).
- **Sequencing after deploy:** verify (b) populates `whale_handle` on new live settlement rows → THEN autopause shadow→active can be considered (operator-gated, separate). Do **not** flip autopause until (b) is verified live — the handle fix is what makes Kalshi autopause functional at all.
- **Scope is confirmed and ready to build.** No blockers found; the only open design call is whether copyability counts `no_fill` (Fix a), and whether to backfill the 15 historical rows (Fix b caveat).

---

## Follow-up 2 — Copy-supply dormancy diagnosis (read-only)

Division has placed nothing since 07-19. Cause is **mixed**, not "whales inactive":

**Per-whale detected activity (all kinds: would_have_placed + placed_live + skips + no_fill):**
| whale | May | Jun | Jul | active days | span | category mix |
|---|---|---|---|---|---|---|
| AI.EDGE | — | 41 | 61 | 23 | 06-15→07-26 | Other 32 · parlay 26 · WorldCup 21 · sports 9 · pol/econ 2 |
| MaggieTheEagle | 6 | 55 | 4 | 17 | 05-17→07-18 | WorldCup 43 · sports 13 · pol/econ 9 |
| pritz786 (off) | — | 424 | 16 | 14 | 06-14→07-01 | (June-heavy, then gone) |

- **AI.EDGE = active + diversified** (year-round-ish, spread across parlays/other/sports, not just WorldCup). BUT of its **61 July detections, only 12 became copies** (12 no-fill + 36 skips) — and **all detections since its last copy (07-19) were skipped/no-filled** (`no_side` / `side_detection_low_confidence`). So AI.EDGE dormancy = a **copyability/side-detection gap**, not the whale going quiet.
- **MaggieTheEagle = event-concentrated** (WorldCup 43/65; June 55 → July 4 detections) — genuinely quieted post-World-Cup.
- **Net:** copy supply is fragile because the 2-whale roster is (i) one event-concentrated whale that quieted, and (ii) one active whale whose recent activity is largely uncopyable (side-detection failing on parlays/other). Reaching n≥30/whale will be slow. This reinforces HOLD-AND-ACCUMULATE and suggests two post-S2 threads: widen the roster, and investigate why AI.EDGE's recent markets fail side-detection (separate, out of scope here).

---

## Follow-up 3 — 1,721× TypeError: pre-existing, NOT autopause-introduced → P3

- **First-ever occurrence: 2026-06-26T13:45:58** — **25 days before** the 07-21 autopause shadow deploy.
- Per-day: 0 on 07-15…07-19, then **196 (07-20)** / 462 (07-21) / … — the recent resurgence began **07-20, a day BEFORE the 07-21 deploy**. Not deploy-correlated.
- Signature: `TypeError: not all arguments converted during string formatting`; traceback is entirely inside Python's `logging/__init__.py` (`emit → format → getMessage`) → a malformed `%`-format **logging call** somewhere in the engine. Non-fatal (logging swallows it; service NR=0). **0 occurrences are copy-division-related.**
- **Verdict: pre-existing logging defect, condition-triggered (intermittent). Filed P3.** Pinning the exact call site (the frame above the logging internals) is a small follow-up if desired. Not an autopause regression — no action needed on the autopause work.

---

**Guardrails honored:** read-only (except this report + the BACKLOG P3 entry); autopause left in shadow; per-order fee model; no edge/prospect memory written.
