# Trading Corp — Open Backlog

Durable list of open work. Each section ends with a recommended phase /
priority. Items get pulled into the active session when their phase comes up.

**Active session work lives in chat — not duplicated here.**

**Completed work moves to `runbooks/deploy_log.md` + memory entries — NOT
preserved here.** This file tracks open items only. The full historical
backlog (with EOS snapshots + completed entries) is archived separately.

**Last grooming pass: 2026-06-02 evening — pre-grooming this file was 8,881
lines; post-grooming organized around three operator priorities + open items.**

## kalshi_arbitrage bucket audit `would_emit` overstates missed opportunities (ignores detector guards) — OPEN (filed 2026-07-26, P3 observability)

The `kalshi_bucket_evaluated` audit field recomputes `would_emit = (1 − Σyes_ask) ≥
min_edge_cents` **without** the detector's guards (`kalshi_temporal_bucket_arb.py:541-554`),
while the real detector `_detect_bucket_violations` (`:266-273` — 60-day horizon + ≥2-leg +
per-leg `expected_expiration` guards) correctly rejects the same event. Result: the audit
logs `would_emit=true` for opportunities that are never tradeable. Observed 2026-07-26: the
`KXNBERRECESSQ` recession event (6 legs, sum_yes_asks 0.784, edge 21.6¢) flagged
`would_emit=true` on **77 consecutive scan cycles** (every 5 min) since 07-21, yet **0
emissions** — the recession-quarter legs resolve far beyond the 60-day horizon, so the
detector drops it (correctly). **Cosmetic only — do NOT fix without operator go.** Matters
because it makes the audit overstate missed opportunity: a reviewer reading
`bucket_evaluated would_emit=true` would wrongly infer a missed fat arb. Fix (when
authorized) = compute the audit `would_emit` from the same guarded predicate the detector
uses, or add a `rejected_by` reason field to the audit payload. **Priority: P3 /
observability.** Not gating. Surfaced in
`reports/2026-07-26_kalshi_arbitrage_entry_stoppage_diagnosis.md` (STEP 3).

## PMCC division — Bucket B fixes (Option 2) — ✅ CLOSED 2026-07-22 (off the open list)

**CLOSED 2026-07-22 — nothing open.** 10 fixes BUILT (B1/B2/B3/B4/B7/B8/B9/B10/B11 + the roll_leap B9/B2 extension — all data/structural-integrity), 6 findings WITHDRAWN as endorsed design (B5, B6, B3 cost/benefit-gate, B8 force-0.55, STRC, position-discovery; A3 falls with B5), readiness gate green (`pmcc_paper_run_readiness.py` → exit 0), auto_execute still FALSE, nothing pushed. **Full history + tally:** `planning/pmcc_option2_bucketB_plan.md` (top STATUS table) + `planning/pmcc_phase2_handoff_2026-07-22.md` (BUCKET B CLOSED section); audit context memory [[pmcc-logic-audit-2026-07-21]]. The original queued-build detail is retained below for history.

Phased build plan for the 11 deterministic-guard (Bucket B) fixes from the 2026-07-21 PMCC logic audit. **Plan of record: `planning/pmcc_option2_bucketB_plan.md`** (6 phases, ~8-11 build sessions). Scope: B1-B11 + A3 (B5's IV-wiring dependency); **auto_execute stays FALSE at exit** (no automation flip — separate future decision). Out of scope: C1 intraday loop, LLM/prompt/strategy redesign, prod deploy. Phase order: P0 regression-baseline harness (`tests/pmcc_regression/`) → P1 **B4 naked-LEAP (highest severity) + B1 HOLD-contradiction + B11 holiday-guard** → P2 roll-quality cluster (B2/B6/B7/B9 + the `PMCCAnalysis.override={kind,reason}` justification contract that defines "LLM-justified" for B1/B2) → **P2.5** roll_leap B9+B2-short-leg (ahead of P3, decided 2026-07-22) → **P3 LEAP cluster RE-SCOPED 2026-07-22** (B3 = old-LEAP-price fix ONLY; B8 = docstring + dead-config decision; the B3 cost/benefit auto-gate and B8 force-0.55 are WITHDRAWN as endorsed design) → **~~P4 A3+B5~~ WITHDRAWN 2026-07-22** (endorsed design — B5 delta targeting is the LLM's; a *selected* delta ≥0.40 is impossible on the target_delta path so the 24 ≥0.40 recs are endorsed strike-targeting; A3 falls as B5's only consumer) → **Final phase** (B3 old-LEAP-price fix + B8a docstring + B8b retire-dead-config + B10 15:00-ET seam) + a separate `pmcc_paper_run_readiness.py` follow-up. Testing = synthetic construction (NO labeling); the 157-row history (`planning/pmcc_rec_history.csv` + `planning/pmcc_recommendation_history_20260721.md`) is a **structural regression baseline**, not an oracle. Exit targets: 0% naked-LEAP, 0 same-expiry rolls, 0 unjustified net-debit/HOLD overrides, LEAP-roll cost recorded 100%, 0 holiday scans, delta in-band. **P0 DONE 2026-07-21:** `tests/pmcc_regression/` green (13 tests); reconciled baselines — close-without-recover 51 (32.5% recs / 40.5% rolls; 31 uncovered + 20 fully_naked-flat + 0 naked_short), 6 holiday-recs/3 dates, 38 LEAP-rolls cost-not-computable (33 @0.0 + 5 no sell-leg), 50 HOLD-overrides, 18 same-expiry, 37 net-debit, 24 delta≥0.40; 80 pre-existing PMCC tests pass. Audit context: memory [[pmcc-logic-audit-2026-07-21]]. ★ **RESTATED 2026-07-22:** a STATUS/exit table now sits at the TOP of the plan — SIX findings collapsed under the endorsed-design test (B6, B3 cost/benefit-gate, B8 force-0.55, B5, STRC, position-discovery); A3 falls with B5. Phases 3/4/5 collapsed into one Final phase (B3 price-fix + B8a/b + B10). THE PATTERN: every survivor is a data/structural-integrity fix; every collapse was an automatic-behavior change that would pre-empt a human or LLM decision. **Priority: operator-queued; execution in separate authorized sessions.**

## PMCC roll_leap B9/B2 gap — ✅ CLOSED 2026-07-22 (BUILT in Phase 2.5; see plan)

Phase-2 built the B9 earnings gate and B2 credit gate into `_propose_roll_short` ONLY. The **roll_leap** path assembles a 4-leg compound (close short + close old LEAP + open new LEAP + **open new short on the new LEAP**) in `propose_orders_for_pair` roll_leap (`pmcc_robinhood.py` ~L1234) and scan roll_leap (~L2187), and neither runs B9 or B2. Because its 4th leg **opens a new short**, a LEAP roll can currently ship a **net-debit short** or a **short into an earnings window** with **neither gate firing**. This is a coverage hole of the **same class as B4's original close-without-recover** — a correctness rule applied to one path (`_propose_roll_short`) but not its sibling (roll_leap), where the sibling does the same risky thing. **What already covers roll_leap (verified read-only 2026-07-22):** B4 atomic legs (Phase 1) + B7 roll-out filter and the `_WEEKLY_FALLBACK_MAX_DTE=60` ceiling (`after_dte` is wired at both roll_leap `_find_best_weekly` calls). **What does NOT:** B9, B2. **B2/B3 boundary (fix nuance):** B9 ports cleanly; B2's short-leg credit (`open_bid - close_mark`) ports to the close-old-short/open-new-short pair, but roll_leap also swaps the LEAP (legs 2+3) so the full 4-leg compound cost is **B3's** domain — a B2-on-roll_leap fix should gate the short-leg credit + earnings only and must NOT re-derive the compound cost (avoid double-counting with B3). **DECIDED (operator, 2026-07-22): its own PHASE 2.5, ahead of Phase 3** (B2-on-roll_leap must NOT re-derive compound cost — that is B3's domain — so they do not share a phase). Scope: port B9 + B2-short-leg-only to both roll_leap sites. Build spec: `planning/pmcc_phase2_5_plan.md`. Full detail in `planning/pmcc_phase2_handoff_2026-07-22.md` (OPEN ITEM section) + `planning/pmcc_option2_bucketB_plan.md`. Audit context: memory [[pmcc-logic-audit-2026-07-21]]. **Priority: correctness / operator-queued (same track as the Bucket-B build).**

## risk.py per-trade notional cap is unreachable on market-order paths (code-path note) — OPEN (filed 2026-07-22, low-pri observation)

Read-only observation about `RiskAgent.evaluate`, **NOT a division defect**. For any `order_type="market"` / fractional order (no `limit_price`), the step-7 per-trade notional cap is **structurally unreachable**: `ref_price = order.limit_price` → None → the `if ref_price > 0` guard at `risk.py:218` **skips the whole cap block**, and `risk.py:215-216` defers to an "executor enforce at fill" cap that **does not exist**. So setting `per_trade_risk_pct` to a real value would **silently fail to bind** a market-order path — a latent trap for a future editor. **This is NOT a PEAD finding:** `robinhood_pead`'s risk posture (stops-only — ATR 2.5× / swing-low + 60-td max hold, long-only, no leverage/margin/shorts; per-trade + daily-loss caps deliberately near-off, `max_drawdown_disabled`) and its `ceo_graph` bypass are **deliberate design owned by another session**, verified and confirmed by that owner — do NOT re-file PEAD's posture as a defect. Memory [[pead-notional-cap-noop-2026-07-22]]. **Priority: P3 / code-path observation.**

## PMCC risk gate evaluates against a FABRICATED $100k balance (real-money-adjacent) — OPEN (filed 2026-07-22, Phase-B candidate)

The PMCC risk gate — single-leg AND the new Phase-A roll_short combo path — evaluates each order against a hardcoded `AccountState(account="paper", equity=100_000, peak_equity=100_000)`, NOT the real Robinhood balance. Root cause: `main._run_order` (`main.py:4593-4595`) builds the graph state with only `{proposed_order, division, regime}` — no `account` key — so `ceo_graph.risk_node` (`ceo_graph.py:334-336`) falls back to its default $100k `AccountState`. Consequence: equity-relative caps (per-trade %, drawdown) are NOT binding on the real balance. **Pre-existing** (a property of the single-leg path today); **inherited faithfully by Phase A** — `_pmcc_combo.propose_pmcc_combo` replicates the exact same basis for byte-equivalence — **NOT a regression.** Fix shape: feed a real `AccountState` from `broker.snapshot()`, exactly as the IC path does via `_ic_account_factory` (`main.py:1528-1529`); PMCC has no equivalent on EITHER path. **Phase-B candidate** (post-submission live safety, NOT roll_short atomicity): a risk gate that cannot see real equity belongs with the live-safety work. **Do NOT fix now.** (Distinct mechanism from the risk.py market-order cap-skip note above: PMCC roll legs ARE limit orders, so the per-trade cap DOES run — just against fabricated $100k equity, not the real balance.)

## PMCC `data_exec._on_rh_auth_change` hard-codes the RH account list — OPEN (filed 2026-07-22, LOW-pri hygiene)

`trading_corp/agents/data_exec.py` `_on_rh_auth_change` (the RH-auth 401 alert hook, ITEM3) hard-codes the four Robinhood account numbers as a literal list in the audit payload (`"accounts": ["680725082","461391328","934310442","116637293063"]`). Three of the four were already on the remote in `config/divisions.yaml` + `config/strategies.yaml`; the reconcile (`557a39f`) put the list into `data_exec.py` too, now pushed to `origin/main` (operator-authorized push 2026-07-22, as-is). These are account **identifiers, not credentials** (auth lives in KeyVault, never git) — **NOT a blocker, NOT to be fixed now.** Hygiene follow-up: at some future reconcile, replace the literal list with a **runtime lookup** (derive the bound accounts from the broker / `Secrets` at hook time) so no account numbers sit in source. Surfaced during the PMCC live-execution push review 2026-07-22. **Priority: P3 / low (hygiene; operator-queued).**

## PMCC — retire the legacy yfinance spot + VIX feeds (brokerage-first policy) — OPEN (filed 2026-07-23)

PMCC carries the **largest remaining yfinance surface** in the system — legacy from being one of the first divisions built, not a considered choice. This is tracked debt under the standing data-source policy (memory `data-source-brokerage-first`): a division's data source should be the brokerage it trades on; yfinance is banned as a PRIMARY source and anywhere the brokerage has an equivalent. **The `broker.quote()` finding:** `RobinhoodBroker.quote(symbol)` (`brokers/robinhood.py:632` → `rs.stocks.get_latest_price`, batchable, fails safe to `0.0`) is the engine's standard RH equity spot, already used by the sister RH options division (`robinhood_joint_iron_condor.py:517,848`), PEAD, Tasty, `data_exec`, `portfolio` — PMCC is the outlier. (The deep-OTM early-roll **gate** was already moved onto `broker.quote` 2026-07-23; this item is the REST of the surface.)

**Two real debts to retire:**
1. **Spot — MEDIUM.** `_fetch_prices` (`pmcc_robinhood.py:1159`, `yf.Tickers.history`) + its wrapper `_fetch_underlying_spot` (`:1584`), pure yfinance. Consumers: scan `prices` dict (`:2143` → LLM analyzer input, breakeven/context math), context builders (`:1795`,`:1826`), scout (`:4075`), wait-alternative (`:1552`). RH drop-in = `broker.quote()` (batchable). Work: the two `staticmethod`s must become broker-aware (take a broker / move onto the instance); ~5 call sites, all already broker-aware or in a broker-scoped async context.
2. **VIX — SMALL.** `get_vix` (`utils/market_data.py:50`, `yf.Ticker("^VIX")`), consumed as an informational analyzer input (`vix=` at `:2130`), not a gate. **No clean broker equivalent** for the VIX index → needs a source decision (drop it, or re-source via RH `get_index_quotes` / another paid feed).

**NOT debt — leave in place:** earnings via `get_next_earnings` (`utils/market_data.py:137`) is **EODHD-primary (paid) + yfinance only a degraded fallback** when EODHD returns None. Per the data-source policy, a documented fallback behind a paid feed for data with no better source is an **accepted design choice**, not a violation. Do NOT file or "fix" the earnings fallback.

**Priority: P2 — spot is the real work (medium); VIX is small-but-needs-a-source. Operator-queued as a separate decision (the wider yfinance removal, distinct from the gate fix already landed).**

## PMCC — LLM prompt has no deep-OTM near-worthless theta-capture guidance; deterministic backstop lags by one session; override reasoning not surfaced on the card — OPEN (filed 2026-07-23)

Broader prompt/UX gap surrounding the narrow "deep-OTM near-worthless short" exception (Lever A/B are the in-scope fix for the exception itself; THIS item is the surrounding gap, filed not fixed). Three coupled parts:

1. **Prompt gap.** The `_STANDARD_RULES` / `_BLACK_SHEEP_RULES` blocks tell the LLM to DEFAULT-TO-HOLD in the terminal-DTE ATM zone but say nothing about the *deep-OTM near-worthless* case (a short marked ~$0.04, far from its strike, still days from expiry). So the model emits `HOLD` when rolling would capture more theta — the observed TSLA 1-DTE screenshot. The prompt narrates a forward plan ("hold now, roll next cycle") without any instruction to weigh next-cycle premium capture now.

2. **Deterministic backstop lags one session.** The only deterministic logic that captures the near-worthless-roll intent is `_terminal_dte_time_release` P1 cycle-continuity (`pmcc_robinhood.py:2574-2592`), and it is gated `short_leg_dte == 0` (`:2560`). On a 1-DTE (not-yet-0) deep-OTM short it does NOT fire — so neither the LLM nor the backstop acts, and the roll happens (if at all) a day later than the operator wants. Lever A widens this; this item records that today there is a one-session dead band.

3. **Card renders the uncorrected HOLD with no explanation.** Override warnings (cycle-continuity release, LEAP Hard-Rule promotion) are only appended to `analysis.warnings` *when a guard fires*, and even then reach the approval card ONLY via the `format_rich()` fold into the per-leg `rationale` string (small italic text) — never via `extra["warnings"]`, so the structured ⚠️ WARNINGS panel (`build_approval_view` `comms/position_context.py:85`; `approval_detail.html:168`) is empty for PMCC roll legs. A HOLD that "should have rolled" therefore renders as a bare HOLD with no correction, and a *promoted* roll_leap renders as several advisory cards whose trigger chain is buried in fine print (see the related read-only card-clarity finding, 2026-07-23). Fix shape (later): (a) add deep-OTM near-worthless guidance to the rule blocks (Lever B territory), (b) surface `analysis.warnings` into `extra["warnings"]` on PMCC legs so the override chain lands in the structured WARNINGS panel, (c) consider consolidating a promoted roll_leap's 4 legs into one advisory card. **Priority: P2 correctness/UX — operator-queued; do NOT fix here (Lever A/B session owns the exception; this is the surrounding context).**

## RH-auth dashboard (web layer) reconciled to git — RESOLVED pending review (2026-07-23; was OPEN 2026-07-22)

The 2026-07-18 RH-auth 401 self-heal was deployed prod-direct across 5+ files; the ENGINE layer was reconciled earlier (`main.py`/`secrets.py`/`robinhood.py`/`data_exec.py`; `f1b8a86`/`e2e2fad`/`557a39f`). **The WEB layer is now reconciled** (2026-07-23, as a prerequisite of the Phase A deploy, which touches `routes.py`): enumerated the whole web tree prod-vs-git first (set is exactly `routes.py` + `home.html` + `rh_session_panel.html`; no static/JS asset); `routes.py` produced by a 3-way merge (base `bb677e7` / ours git-HEAD+PhaseA / theirs prod) proven by bidirectional diff — **vs prod = only the Phase A routes (+98/−0); vs git-HEAD = only RH-auth `ITEM2-RH-ROUTES` (+52/−0)**; `home.html` byte-exact from prod (prod-superset guard passed: 0 git-only lines; LF-norm md5 `6d04accf`==prod); `rh_session_panel.html` new byte-exact (`c774b8f8`==prod). **Working-tree only — NOT committed** (awaiting operator review); NO deploy (prod already runs it). Memory [[rh-auth-git-reconcile-2026-07-22]]. **Priority: was P3; now review-gated.**

## prod-direct drift on a PEAD dashboard partial (`pead_live_sections.html`) — OPEN (filed 2026-07-23, needs own reconcile pass)

Surfaced incidentally while enumerating the web tree for the RH-auth reconcile. `trading_corp/web/templates/partials/pead_live_sections.html` is drifted git↔prod: git `4d771c0e` vs prod `5ab68dc5`, with a prod backup `.bak_peadpanel_20260720` confirming a prod-direct edit (git is behind prod). **Unrelated to RH-auth and to Phase A** — Phase A's deploy set does not include it, so it is NOT a Phase-A deploy blocker; it is a standalone un-reconciled prod-direct web change. Needs its own capture → byte-exact transcribe → LF-norm-md5-verify reconcile pass. **★ PEAD is another session's division — whoever picks this up should coordinate with the PEAD owner** (the partial renders a PEAD dashboard section). **Priority: P3 / git-hygiene.**

## No systematic tracking of prod-direct web-tree drift — schedule a prod→git web reconcile sweep — OPEN (filed 2026-07-23, process)

The RH-auth reconcile enumeration (2026-07-23) showed prod's `web/templates` tree carries un-reconciled prod-direct edits across MULTIPLE divisions, discovered only incidentally: the RH-auth web set + the PEAD partial above are two instances found by chance, plus ~90 prod-only `*.bak*`/`*.pre-*` backup snapshots and a static tree 14 files ahead of git. Nobody tracks web-layer prod↔git divergence systematically, so a `routes.py`/template deploy can silently clobber an un-reconciled prod-direct edit (the exact fork this Phase A reconcile hit). **Schedule a deliberate prod→git web reconcile sweep**: enumerate the whole `web/` tree, diff prod-vs-git per file, reconcile real drift, prune the backup cruft — coordinated across division owners. Do NOT do it now; file as a scheduled item to run **before the next web-touching deploy that isn't pre-enumerated**. **Priority: P3 / process.**

## GUARDRAIL: partial reconciliation of a multi-file prod-direct patch — OPEN (filed 2026-07-22, process lesson)

**Reconciling a SUBSET of a multi-file prod-direct patch can leave git booting WORSE than either endpoint.** Concrete cost this arc: reconciling `main.py`'s RH-auth ITEM3-WIRE line (`_rh._auth_alert_hook = data_exec._on_rh_auth_change`) without `data_exec.py` left git's `main.py` calling a method git did not define → fresh-deploy `AttributeError` at boot (the un-reconciled prod original booted fine; the fully-reconciled git boots fine; **the partial in between is worst**). **RULE: before reconciling ANY one file of a multi-file prod-direct patch, enumerate the WHOLE patch set (read the deploy artifacts, e.g. `deploy_rh_auth/*.patch`, AND grep for cross-file references like a wired hook's target) and reconcile the boot-critical companions TOGETHER in one commit.** Completeness-sweep at the PATCH level, not just the file level. Memory [[main-prod-reconciled-2026-07-08]] (reconciliation-method note, hazard recorded there) + [[rh-auth-git-reconcile-2026-07-22]]. **Priority: process guardrail (applies to every future prod→git reconcile).**

## Dashboard Today's-P&L / equity day-change % is x100 too large — OPEN (filed 2026-07-21, LOW-pri display bug)

Pre-existing display artifact (NOT caused by the 2026-07-21 kalshi_llm epoch/OPEN deploys). The Prediction-Markets division cards render `todays_pnl_pct` as roughly **x100 too large** — e.g. kalshi_llm_arbitrage showed **-17.24%** when the true day-change is **~-0.17%** (Today's P&L -$0.92 on ~$533.76 prior equity = -0.001724 = -0.17%). The same wrong % shows on both the Equity card and the Today's P&L card (they share `todays_pnl_pct`). Source: `_approx_todays_pnl` (web/data.py:6025) returns `pct = pnl/yesterday` as a FRACTION; a downstream x100 (view or template) is applied such that the fraction is multiplied by 100 twice. Fix: render `pct` as a percentage exactly once (audit `_approx_todays_pnl` consumers + the card template). Affects all PM division cards, not just kalshi_llm. **Priority: P3 / low** (cosmetic; dollar figures are correct).

## kalshi_arbitrage paper-fill model overstates on extreme-cheap legs — OPEN (filed 2026-07-21, P3 observability)

kalshi_arbitrage (temporal-bucket arb) paper P&L is **dominated by fills at $0.01-0.02** (e.g. a $1 stake = 100 contracts of a NO @ $0.01 -> +$99 when it resolves in-the-money; the +$3,040 backlog-drain total is driven by a handful of such long-shots). **Paper mode fills at midpoint with infinite depth**; live Kalshi at the extreme tick has a **thin book -> likely partial or no fill** at that price/size. Therefore the paper number is a **best-case CEILING, not a live projection**. Not a code bug — an interpretation caveat. **File for context when any live-mode conversation about this strategy arises.** Load-bearing sanity check at the forward-edge trigger = a paper-vs-live realistic-fill projection using thin-book assumptions for sub-$0.05 legs (see memory [[kalshi-arbitrage-followup-and-commingling-2026-07-21]] + report reports/2026-07-21_kalshi_arbitrage_data_review.md). **Priority: P3 / low.**

## RECEIPT (refuted, do-not-reopen) — kalshi_arbitrage "sub-$0.05 legs are fee-negative" — CLOSED (P4, 2026-07-21)

- **Hypothesis (raised 2026-07-21):** the strategy enters legs at $0.01–0.02 where per-contract-per-side `ceil(0.07·C·P·(1−P))` rounds to $0.01, making round-trip fees ~100% of leg cost at $0.02 / ~40% at $0.05 → sub-$0.05 trades structurally losing regardless of hit rate.
- **Investigation:** confirmed against all **260 resolved trades** in kalshi_arbitrage (read-only).
- **REFUTED:** Kalshi's fee is a **per-ORDER ceil** — `ceil(0.07·C·P·(1−P))` applied once to the whole order, **not** per-contract. At this strategy's **~$1/leg sizing (many contracts)** the fee is **~$0.07 flat per $1 stake (~7% of stake), independent of leg price** (C=1/P and P cancel). The "100%" figure only appears under a per-contract-ceil model that is not the real schedule. The sub-$0.05 subset is **net-positive** (85 RT, 75 winners; +$2,867 net at the real per-order fee, and still +$2,839 even under the inflated per-contract worst case) — it holds essentially the entire recorded P&L. "Losing regardless of hit rate" is backwards: hit rate is the engine (a $1 stake on a $0.02 leg that hits returns ~+$49 vs a ~$0.07 fee).
- **The correct fee gate already exists:** `kalshi_temporal_bucket_arb` gates on `min_edge_cents` (temporal **4¢**, bucket **5¢**) = edge **net of fees**, which is the right economic gate for an arb. A `min_leg_price` floor would be **economically backwards** — it would reject the cheap winners (~−$2,867).
- **DO NOT re-raise as a finding. DO NOT propose a min_leg_price floor.** Recorded so future sessions don't re-open this loop.
- **Still open (separate, not this):** the sub-$0.05 legs hit ~88% vs their 2–5% implied price — validated only by the forward-edge trigger (post-fix n≥30 OR 2026-09-15) once post-fix positions resolve; do not investigate before then. See [[kalshi-arbitrage-followup-and-commingling-2026-07-21]] + the distinct P3 paper-fill/liquidity note above (thin book on cheap legs — that one is NOT refuted).

## kalshi_llm resolver backlog: ~602 past-expiration legacy positions un-booked — OPEN (filed 2026-07-21, observability, P4/low)

As of 2026-07-21, kalshi_llm_arbitrage has ~1,317 open `would_have_placed` positions, of which **~602 are already PAST expiration** (May+June expiry) but not yet resolved in `kalshi_round_trips` — the resolver is still draining the pre-07-07 `leg_date` backlog. They will book as further realized P&L (the dropped-category book) as the resolver reaches them, so the current realized figure carries a **known un-booked overhang**. Not a bug (mechanical settlement lag) — observability note so the drain isn't mistaken for new-logic performance. See [[kalshi-llm-dashboard-epoch-2026-07-21]] + report `reports/2026-07-21_kalshi_llm_arbitrage_data_review.md`. **Priority: P4/low.**

## Copy-trading feed-health observability — OPEN (filed 2026-07-20, LOW-pri instrumentation)

Surfaced in the 2026-07-20 copy-trading review / autopause window-fix session (Finding #3). Two related gaps in copy-trading safety observability; neither is gating, both are instrumentation-only:

- **No operator-facing feed-health panel.** `kalshi_copy_feed_anomaly` (mass-disappearance + consecutive-fetch-failure) and Apify HTTP errors land only in `audit_event` + the Telegram alert queue — no dashboard widget aggregates them. The operator has no at-a-glance feed-health view for the copy divisions.
- **Feed-breaker counter is in-process and resets on restart.** `kalshi_copy_trader._consecutive_fetch_failures` is a plain instance int reset to 0 on any successful fetch AND on engine restart. A multi-day Apify outage spanning a restart would never cross the ≥3 threshold (cf. the 06-08→06-10 silent copy-emission gap). Consider persisting the streak (agent_state) and/or a time-window (last-N-cycles) basis.

Other copy-trading safety guards were audited this session and are NOT window-mismatched: mass-exit suppressor (cycle-local snapshot delta), residual/`leg_priced` guard (per-row boolean), drift filter (instantaneous), cold-start (one-shot). The auto-pause window mismatch (the one real bug) is fixed on branch `kalshi-poly-autopause-epoch-2026-07-20` (SHADOW, pending deploy). **Priority: P3 / low.** Not urgent.

## Coinbase BTC HODL (coinbase_spot / Donchian) — audit + Binance-4Y re-backtest — OPEN (filed 2026-07-19)

Full analysis: `reports/2026-07-19_donchian_binance_revalidation.md` (Phases 0-7) +
`reports/2026-07-18_coinbase_btc_hodl_audit.md` + `reports/2026-07-18_coinbase_spot_ownership_investigation.md`.
Harnesses `scripts/donchian_binance_*.py`. Memory `coinbase-donchian-revalidation-2026-07-19`.
**ALL READ-ONLY — nothing deployed / applied / committed to prod this session.**

**Division state (verified prod 2026-07-18):** `coinbase_spot` runs `coinbase_btc_donchian` = a binary
CASH<->BTC 6h Donchian trend-follower (NOT accumulate despite the "HODL" name). enabled:true + 6h
scheduler running + evaluating every bar, BUT structurally CANNOT fill: `coinbase` not in engine
`--brokers`, `coinbase_spot` not in `--live-divisions`, auto_execute:false. 0 real fills in ~10 weeks;
only signal 2026-07-15 board_rejected (approval timeout). ~$85K real cash idle, 0 BTC.

**Open DECISIONS (operator):**
- **P2 — Division purpose:** activate / repurpose the ~$85K / decommission (0 fills in 10 weeks).
- **P2 — BLOCKER for any activation = account isolation.** Strategy sizes off the WHOLE commingled
  account (BUY=100% acct cash, SELL=100% acct BTC); risk gate evaluates a SYNTHETIC $100K equity
  (ceo_graph risk_node fallback, blind to real balance); account commingled with Board treasury.
  auto_execute CANNOT be safely enabled without a dedicated Coinbase sub-account/portfolio + real
  equity into the risk gate. See ownership report verdict.
- **P3 — Naming:** "Coinbase BTC HODL" is a misnomer for a full-exit swing trend-follower.

**Backtest verdict (Binance 4Y, survivorship-corrected):** modest, real, NON-TUNABLE, timeframe-invariant
DRAWDOWN-CONTROL overlay. Survivorship-free ~1.0 Calmar OOS (vs HODL 0.46); LAGS HODL on return in bulls;
decline-concentrated. Deployed 20/168/6's +25.89%-alpha / ~2.0-Calmar headline is IN-SAMPLE SURVIVORSHIP
(top-3% draw) — do NOT rely. DO NOT re-optimize (89% overfit tax, unstable params). 6h is the best
interval (Phase 7 null). If kept, hold 20/168/6 FIXED.

**Highest-certainty action (independent of the above):** yield-ify the ~74% idle cash
(money-market/T-bill/USDC-yield swept when flat) = ~+3.3%/yr (~$2,800/yr on $85K) at ~0 risk; doable
whether or not the strategy ever activates.

**STAGED, not applied — `config/strategies.yaml` comment diff (2 hunks):** (a) replace the L822-827
"+25.89% alpha" in-sample headline with the corrected drawdown-overlay expectation; (b) fix the L829-832
implementation-status block which wrongly says "Phase 2 NOT YET BUILT / enabled:false" (it's
built+enabled+running-but-cannot-fill). Diff in session chat + report §6.3. Same stale +25.89% headline
also lives in `runbooks/deploy_log.md` (2026-05-09 entry) — update those when the diff is applied.

## RH-auth resilience follow-ups — OPEN (filed 2026-07-18, all LOW/optional)

RH-auth self-heal (ITEMS 1/2/3) DEPLOYED+LIVE 2026-07-18 — see `runbooks/deploy_log.md`
2026-07-18 + memory `rh-auth-resilience-deployed-2026-07-18`. Remaining, all optional:
- **P3 — Timer "1B" refresh-token renewal** (avoid the daily/expiry device push entirely by
  renewing the access token from the stored refresh_token before expiry). Needs verifying
  robin_stocks supports a refresh_token grant; current timer is gentle-on-expiry (pushes only
  on real expiry). Better if real; not blocking (ITEM 3 in-proc reload + button carry recovery).
- **P3 — Strip ROBINHOOD_* from root `/etc/trading-corp/tastytrade.env`** — OPTIONAL: we
  deliberately KEPT unit-env as a break-glass FALLBACK (KV is authoritative). Strip only if you
  want strict KV single-source; a KV/MI boot blip then has no fallback. Do NOT strip before the
  secrets.py patch is confirmed live (it is, as of 2026-07-18 restart).
- **P3 — git↔prod source reconciliation** — the 2026-07-18 RH-auth (+ SFP ROI/epoch) engine
  edits were applied prod-direct; local `main` trading_corp source is behind prod. The exact
  RH-auth changes are replayable from `deploy_rh_auth/apply_rh_auth_batch.py`. Reconcile when
  convenient (drift-gate first).

## PMCC go-live: approval-vs-autonomous decision + submission-idempotency gate — OPEN (filed 2026-07-08)

Two-part pre-go-live decision, IN ORDER. **NOT a current risk** (`robinhood_pmcc` is paper-only
today). Context: memory `pmcc-lifecycle-fix-2026-07-08` + `runbooks/deploy_log.md` 2026-07-08 PMCC
entry (the lifecycle fix that motivates this).

**PART 1 (decide FIRST) — approval flow, or autonomous?** PMCC today = paper + Board approval
(agent proposes → operator approves via Telegram/web → graph resumes → executes). Live-go must
first choose:
- **Autonomous** ELIMINATES the entire orphaning failure class this session fixed (no approval
  wait, no interrupt/resume, no lifecycle coupling). Fix A / Fix B / canary become **moot for
  PMCC** (retained as belt-and-suspenders + reference architecture for any other division on the
  same LangGraph approval pattern).
- **Approval flow** adds a human safety layer but keeps the failure surface the reconciler now
  guards.
- Trust / risk-appetite call, not technical — both defensible. Operator decides at go-live based
  on comfort with the agent's decision quality.

**PART 2 (ONLY if PART 1 = keep the approval flow → then a HARD pre-go-live gate) — verify the
approval-decision → broker-submission path is idempotent under recovery.**
- Recovery restores the audit TRUTH ("Board approved this"); it does NOT automatically recover the
  EXECUTION. The graph submission (`execute_node` → `data_exec.place`) is a single-shot side effect
  that was lost with the original failed resume `ainvoke`.
- Paper-mode visible bug was cosmetic. LIVE-mode INVISIBLE bug would be: **approved trades that
  never execute** (capital reservation released, no broker order, no position).
- ★ Current-impl clarification: **Fix A is REJECT-ONLY** — it recovers reject→`board_rejected` and
  deliberately SKIPS recorded-approve rows (warns + leaves them `risk_approved` for the canary →
  human handles). So the silent-unexecuted-approve bug does NOT occur via Fix A today; but the
  underlying non-idempotent submission path IS this gate.
- Resolution options if the flow is kept: (a) reconciler submission-retry when it recovers an
  approved row; (b) require manual review of recovered-approved rows before submission; (c) make
  the graph submission step itself idempotent + retryable so the reconciler's status fix alone
  triggers correct downstream execution.

## Merge debt (07-02+ CRLF hybrid + 4 deploys) - RESOLVED 2026-07-08

Prod had diverged from main since 2026-07-02 (SFP bidirectional deployed as targeted
hunks onto prod's Kalshi-CRLF tree, never wholesale-copied); 4+ subsequent deploys
widened the gap. **RESOLVED 2026-07-08:** reconcile branch `main-prod-reconcile-2026-07-08`
(C1 `b9de6c5` + C2 `7b642f8` + C3 `829ebdf`) merged `--no-ff` to main as **`be1d809`**,
pushed origin. 17/17 files byte-exact (triple md5-verified; origin/main == local main ==
prod). Report `reports/2026-07-08_main_prod_reconciliation.md`; deploy_log 2026-07-08 entry.

**Honest scope of the restored invariant:** main == prod restored 2026-07-08 for deployed
executable content; **6 CRLF-cosmetic files + 1 docstring-lag file (`kalshi_resolver.py`,
main leads) + 7 dev-only files are documented exclusions** - see the 2026-07-08 reconciliation
report for per-file rationale.

## MEMORY.md grooming pass - ~60KB -> ~25KB target - OPEN (filed 2026-07-08)

MEMORY.md is ~59KB vs the ~24KB soft limit (only part loads into context otherwise). Groom:
- **Compress entries** - overlong index lines down to one-line hooks (<~200 chars); the detail
  already lives in the topic files.
- **Consolidate the related SFP-arc entries** (`bitunix-sfp-division-build-spec`,
  `bitunix-sfp-mode-b`, `bitunix-sfp-bidirectional-live`, `dashboard-reorg-2026-07-07`) into a
  tighter set.
- **Mark superseded entries as historical** (or delete truly-dead ones) so recall surfaces
  current state.

Low priority; non-destructive to topic-file content; do between deploy-adjacent work.

## P1 — Bitunix SFP **Mode-B (15m SFP / 3m BOS)** forward-track + scale gate — OPEN (deployed live 2026-06-28)

Mode B is LIVE (see `deploy_log.md` 2026-06-28 (later)): **BTC + ETH `arm:trading`**, **SOL + XRP `arm:watch`
(paper)**. Open work:
- **Forward-track BTC/ETH to n≥30, THEN scale money.** Sizing is trivial by design (risk_pct 0.0025, lev 2.0);
  the operator scales capital only after BTC/ETH 3m-BOS reach a verdict-grade sample. n=1 so far (ETH stop-out
  −1.16R, 2026-06-28 20:15Z). Track win%/avgR per coin via the cockpit TIER-A / `paper_trade_record`
  (`division='bitunix_sfp'`, `source_signal` like `sfp_*_3m_bos`).
- **SOL/XRP stay watch-only (paper).** Backtest negative/thin (2026-06-26 reports). Revisit a live arm only if
  forward-track paper data shows an edge. Arm = flip `strategies.yaml bitunix_sfp.symbol_modes.<coin>.arm`
  `watch→trading` + restart (Board ack; not code-gated).
- **BTC is now on 3m-BOS, OFF its validated 15m-BOS edge** (operator's 2026-06-28 choice). If 3m underperforms
  on the forward-track, revert = `symbol_modes."BTC/USDT.P".bos_tf` `3m→15m` + restart (the Mode-A path is
  byte-intact and still parity-tested).
- **Cockpit TIER-B → real reads.** `sfp_watch_state` IS now populated live (Mode-B arms/confirms write it); the
  cockpit's armed-watch overlay + near-miss/bos-confirm panels are still `_mock_*`. Wire them to real
  `sfp_watch_state` reads (display-only).

*(Carried, older, SEPARATE — Bitunix **confluence/futures** division, NOT SFP: P1-A silence-window + P1-B
TP-structure backtests; fee/slippage levers; deeper high-vol 3m corpus ingest. See the 2026-06-14 entries below.)*

## P2 — `backtest_bitunix_confluence.py` five_factor/coinbase machinery MISSING from git (prod-vs-git drift) — ✅ RESOLVED 2026-06-20 (commit `2659c81`)

**RESOLVED 2026-06-20 via surgical recovery — commit `2659c81` (merged to main this session).** Root cause was
NOT prod-vs-git drift (prod also lacked the files; backtest scripts aren't deployed to prod): it was a
**commit-omission** — `861bf90` ("persist BitUnix backtest tooling") landed the tests + analysis scripts but
omitted the impl files, which survived only on the 2026-05-19 backup snapshot `c4e47a7` ("do not merge" WIP).
Recovered per-file (NOT a branch merge): `bitunix_confluence_gate.py` (net-new), `_ta_helpers.py` (net-new —
a transitive dep the original 3-file scope missed, surfaced by running the test), `bitunix_price_context.py`
(additive hand-port — live-path file, existing functions byte-untouched), and `_resample_to_3m/5m/15m` in
`backtest_btc_accumulator.py` (pure-additive). The 3 RED-at-collection tests now pass (78 green); observer
suite 53/53 (no live-path regression). Investigation: `Desktop/bitunix_reports/2026-06-20_backtest_five_factor_gitdrift.md`.

---

_Original report (for history):_

Surfaced 2026-06-14 during the PA-redeem-cap engine build. `scripts/backtest_bitunix_confluence.py`
was **unimportable on main `32e7fb4`** (and current main) — it imports + calls THREE things that are
defined **nowhere in the repo** (only the PA/bybit_hybrid path's deps exist):
1. `_resample_to_3m` / `_resample_to_5m` / `_resample_to_15m` (from `backtest_btc_accumulator`; only
   `_resample_to_4h`/`_1h` exist) — used by the **coinbase** bar-source path (~lines 480-482).
2. `bitunix_confluence_gate` (whole module: `ConfluenceGateConfig`, `GateDecision`,
   `evaluate_confluence_gate`) — the **five_factor** gate arm.
3. `bitunix_price_context.build_gate_inputs` — also five_factor.

Consequence: `tests/test_backtest_bitunix_confluence_five_factor.py` was **already RED at collection on
main** (ImportError) — the "zero-regressions baseline" for that test is broken independent of any new
work. Almost certainly **prod-vs-git drift** (the five_factor/coinbase machinery shipped to prod but
the defining modules/functions were never committed; cf. the `sync: catch git up to prod` history).

**Worked around (NOT fixed) on branch `bitunix-redeem-cap-backtest-tooling-2026-06-14`:** the 3 imports
are `try/except`-guarded so the **PA + bybit_hybrid** path (the redeem-cap engine) imports + runs; the
five_factor/coinbase arms stay broken-but-LOUD (raise NotImplementedError if used). **Proper repair is
a separate task** (touches the shared `backtest_btc_accumulator`): recover the missing modules/functions
from prod, OR delete the dead five_factor/coinbase paths if abandoned. Investigate which.


---

# Priority 1 — Bitunix Futures path to live trading

Phase 3 (live exit path infrastructure) DEPLOYED to prod 2026-06-02 ~01:40 UTC.
Paper-mode observation window 2026-06-02 → 2026-06-09. After window, operator
decides whether to flip `config/strategies.yaml:1022 execution_mode: paper → live`.
No formal checklist — operator judgment based on observed paper performance,
audit log review, and any unexpected behavior in the new code paths.

When the flip happens, the dashboard begins filtering by flip-date — paper-mode
trades persist in the DB but are no longer rendered in the live-mode view.
Queries against historical paper data remain available via Claude.

## P1 — Bitunix reconciler false-divergence on live positions → latches engine out of new entries (filed 2026-06-14 via first-fill close-out)

**BLOCKS LIVE TRADING.** Surfaced on the first live fill (2026-06-14). `reconcile_position_state`
matches bot-tracked rows to broker positions by exact `(symbol, side)`
(`bitunix_position_reconciler.py` ~505-508), but BOTH keys mismatch for every live Bitunix position:
- **Symbol:** the bot stores `BTC/USDT.P`; `get_pending_positions` returns broker-native `BTCUSDT` → never equal.
- **Side:** `get_pending_positions` negates qty only when `side == "SHORT"` (`bitunix.py` ~1029-1031), but
  BitUnix returns a different side label for the short → qty stays positive → `_broker_side` = `"buy"` ≠ bot `"sell"`.

Either failure alone guarantees a false `missing_on_broker` (the bot's real position) + `orphan_on_broker`
(the same position re-read as a phantom opposite side). The sanity-poll loop
(`run_position_state_sanity_poll_loop` → `reconcile_position_state`, default `halt_on_divergence=True`) then
sets `broker._halt_new_orders=True` (reason `position_state_reconciler_divergence`) every ~60s →
`place_order` refuses new entries (`bitunix.py:849`); exits are unaffected. **Clears only on broker
re-init.** Observed: false divergence every ~60s from 18:25:04 (56s post-fill); engine still latched at 19:30:47 UTC.

**Fix (NOT done — §4-gated, design first):** normalize symbols both ways (`BTC/USDT.P` ↔ `BTCUSDT`) in the
matcher AND derive qty sign in `get_pending_positions` from the actual BitUnix side enum (handle the
non-`"SHORT"` label), with a regression test on a real-shaped position payload. **Resuming live needs THIS
fix THEN a restart — a restart ALONE re-triggers the same divergence on the next fill and re-latches.**
Report: `reports/2026-06-14_bitunix_first_fill_closeout.md`.

## P2 — Bitunix server-side-stop close is not auto-booked (paper_trade_record.result stranded NULL) (filed 2026-06-14 via first-fill close-out)

When the B1 server-side SL (or any broker-side close) closes a live position, the exchange does the
closing — the bot's replay/exit path (which expects to PLACE the reduce-only close itself, then
`_record_exit_outcome`) never runs, so `paper_trade_record.result` stays NULL: no exit price, PnL, or exit
fee booked. The reconciler classifies it as `missing_on_broker`, which `resume_live_positions` **defers to
operator resolution by design** (Phase 1b §4) — there is no auto-book path. Observed on the first live fill:
`result` still NULL ~2.5h after the stop (broker flat). Side effect: the unresolved row keeps the P1
divergence (and its halt-latch) alive indefinitely.

**STATUS 2026-06-14 — QUICK FIX BUILT (known-level estimate + latch-release); accurate version PENDING.**
Branch `bitunix-p2-autobook-latch-release-2026-06-14` (§4 build+test, **NOT deployed**, off main `299b40c`)
adds, in `reconcile_position_state`: **(a) auto-book** a confirmed `missing_on_broker` bot row (closed
server-side, `result` NULL) at the KNOWN stop level — `result='loss'`, `result_price=stop_price`, PnL
`(entry−level)×qty` sign-correct, flagged `result_source='auto_booked_from_stop_level'` /
`pnl_basis='known_level_estimate'` / `slippage_unreconciled=true`; defers (NULL + `autobook_deferred` flag)
if a TP leg was reached (ambiguous) or no stop level. **(b) latch-release** — clears `_halt_new_orders` on
TWO consecutive clean ticks so the engine self-recovers WITHOUT a restart (stays halted on a genuine
orphan). Both gated on a 2-consecutive-tick confirm (one empty `get_pending_positions` can be a transient
API error). 11 tests + zero-regression gate.

**PERMANENT FIX (PENDING — supersedes the estimate): auto-book from the REAL server-side fill.** Replace the
known-level estimate with a **signed broker trade-history query** (e.g. `/api/v1/futures/trade/get_history_trades`,
keyed on the position's `broker_order_id` / symbol+close-window) → book the EXACT exit `result_price`,
`actual_pnl_dollars`, and `exit_fee_usd` from broker truth, with `result_source='auto_booked_from_broker_fill'`
(authoritative; drop the slippage flag). **Motivating example:** trade 2's recorded `stop_price` was 65004.48
but it actually filled **65142.3 (~138pt / 0.52% slippage)** — the known-level estimate books ≈−0.107 vs the
real −0.134; the signed-fetch version removes that gap (and captures the exit fee, which the estimate leaves
unset). This is a signed/public-API call (outside the §4 known-level scope), so it lands separately. Reports:
`reports/2026-06-14_bitunix_p2_autobook_latch.md`, `reports/2026-06-14_bitunix_first_fill_closeout.md`.

## P1 — D1/D2 account-drawdown auto-flatten fix: **MERGED + DEPLOYED + LOADED** (deployed 2026-06-13; filed 2026-06-11)

The 15% account-drawdown auto-flatten was a placeholder that never fired
(`peak_equity=current` ⇒ `drawdown_pct()=0`; and the score path never dispatched
the flatten — D1/D2). **FIX BUILT + TESTED on branch
`bitunix-d1-drawdown-flatten-fix-2026-06-11` (4d3a97c, pushed; MERGED to main as `76f3bb8`):**
persisted account high-water-mark in `agent_state` (restart-safe, fail-safe to
current on read error), both call sites fed the tracked peak, score-path flatten
dispatch added. Tests prove a forced 15% drawdown flattens on BOTH paths;
regression-proof + branch-vs-pristine-base no-regression gate (zero new). Report
`reports/2026-06-11_bitunix_d1_drawdown_flatten_fix.md`.

**STATUS: DEPLOYED + LOADED 2026-06-13** — merge `76f3bb8` (`--no-ff` of `5c3a294`, pushed);
single-file `bitunix_futures_observer.py` to prod (md5 `21830bf3…710b`, backup
`.bak-pre-d1-2026-06-11`) + restart 03:37 UTC (new PID 2608222, fresh venv-3.12 `.pyc`,
`execution_mode=paper`; peak self-initialized on first eval → no false flatten; the restart
also (re)loaded the B1 stop). **15% breaker now LIVE in paper** — see `runbooks/deploy_log.md`
2026-06-13 entry. **First prerequisite of the path-to-supervised-live sequence**
(deploy D1 ✅ → HITL removal ✅ **DEPLOYED-TO-DISK 2026-06-13** [[bitunix-hitl-removed-deployed-to-disk]]; B1 real-fill validation **PASSED on first real fill 2026-06-14** (was dropped/accepted-risk; now validated — see `runbooks/deploy_log.md` 2026-06-14 entry) → item-4 non-interactive `--live` go-live **DONE 2026-06-13** — the go-live restart loaded HITL+D1).
Resolves the dead-breaker BLOCKER tracked in
[[2026-06-11-bitunix-hitl-removal-blocked-dead-drawdown-breaker]] — **NOW RESOLVED**.

## Item 4 — non-interactive durable `--live` authorization: **DONE — MERGED + DEPLOYED + AUTONOMOUS-LIVE 2026-06-13** (merge bbae4d6)

The systemd service couldn't run live: `--live` required an interactive typed-LIVE
confirmation (`main.py` `confirm_live`) which EOFErrors under systemd → exit 2 →
crash-loop. **BUILT + TESTED on branch `bitunix-noninteractive-live-auth-2026-06-13`
(`710e181`, pushed, UNMERGED):** durable env `TC_LIVE_AUTHORIZED=LIVE` (persists across
restarts incl. crash/`Restart=on-failure` → resurrects live without re-arming; revoke by
unsetting → paper). Unauthorized non-interactive `--live` **downgrades to PAPER** (never
`return 2` → no crash-loop). Interactive typed-LIVE unchanged; `assert_live_ready` still
runs. 15/15 tests + full branch-vs-base zero code regressions.

**STATUS: DONE — MERGED + DEPLOYED + AUTONOMOUS-LIVE 2026-06-13.** All four go-live steps
completed (operator-run): **(a)** merged `710e181`→main (`bbae4d6`); **(b)** deployed `main.py`
(md5 `659bbb80`); **(c)** CLAUDE.md invariant #3 rewritten to match the guarded non-interactive
live path (`199716b`); **(d)** go-live restart 15:36 UTC (`--live --brokers bitunix` +
`TC_LIVE_AUTHORIZED=LIVE` + `execution_mode: live`; loaded HITL=0 + D1 + Item-4 → autonomous
live). See `runbooks/deploy_log.md` 2026-06-13 15:36 go-live entry. Full map: memory
[[bitunix-go-live-sequence-and-item4]]. Accepted residual risks: durable auth resurrects live
on crash; B1 **VALIDATED on first real fill 2026-06-14** (was the dropped unvalidated-on-real-fill residual risk); taker-fee net-negative.

## Observation window — active

> **2026-06-10 — FRESH window active** (post vol-classifier fix `7834375`, started
> 2026-06-09 03:49:41 UTC). Day-2 expanded review CLOSED clean — F-5 confirmed; report on
> branch `bitunix-day2-expanded-review-2026-06-10`. **⏰ Day-5 close-out due 2026-06-14
> ~04:00 UTC** — full window aggregate + flip-readiness inputs; **run from a prod-connected
> (local) session** (read-only SSH). Unblocks the P1 post-window backtest/TP-structure
> session and the P2 10006 backoff fix. Tracked here rather than a remote `/schedule`
> routine because remote cloud agents can't reach prod over SSH.

> **2026-06-08 update — window INVALIDATED** by the P1 finding below (`bitunix_htf_regime`
> volatility classifier bug — **0 fires for 6 of 7 days**). Day-7 close-out 2026-06-09
> **cannot** produce a flip-readiness verdict. A fresh observation window is required
> post-fix. Root cause + evidence in the P1 entry immediately below.

- **Day 2 audit completed 2026-06-02:** all gates intact, zero firings of Phase 3
  audit kinds, 6 bitunix paper trades (5W/1L), error rate normal,
  `_DB_LOCK_RETRY_DELAYS_SEC` retry exhausting 8× per 23.5h on `hitl/*` writes
  (pre-existing `agents/logger.py` path, not Decision 6.2's new path). Decision 6.2's
  `insert_paper_trade_record` retry: zero firings (silent). Audit at
  `reports/2026-06-02_phase3_day2_audit.md`. **(2026-07-13: this `hitl/*` db-lock retry-exhaustion baseline is now ROOT-CAUSE FIXED + SHIPPED — checkpointer isolated off the shared WAL writer, 0 locks over 72h+; see the P3 event-loop DB-lock cross-ref + `deploy_log.md` 2026-07-10.)**
- **Day 4 mid-window probe:** scheduled 2026-06-04. Same query set; compare
  8-count db-lock baseline + bitunix trade distribution + bitunix win rate.
- **Day 7 close-out:** scheduled 2026-06-09. Full window aggregate; verdict on
  whether `execution_mode: paper → live` flip is ready.

## P1 — `bitunix_htf_regime` volatility classifier ignores config; treats BTC ATR ≥3% as extreme (filed 2026-06-08 via Thread A investigation)

> **RESOLVED 2026-06-09 — DEPLOYED.** Fixed via merge `7834375` (`--no-ff` of `ab0d251`
> source + `ea92d4c` tests; Option 1a — `_atr_pct_to_tier` final tier boundary reads
> `extreme`/5.0% not `high`/3.0%). Deployed to prod 2026-06-09 03:49:41 UTC (MainPID
> 2397472; healthz `{"status":"ok","mode":"PAPER"}`; no config change — `extreme: 5.0`
> was already on prod). Orphaned `high` key filed as P3 (`4214c23`). **Fresh paper
> observation window started 2026-06-09 03:49:41 UTC; the 06-02→09 window stays
> INVALIDATED — a full fresh window is required before any `execution_mode` live-flip
> decision.** See deploy_log 2026-06-09 entry + memory
> [[2026-06-08-bitunix-volatility-classifier-wired-deployed]].

Root cause of zero Bitunix fires since 2026-06-02 22:15 UTC, identified 2026-06-08 via
paper-mode observation-window investigation.

**Bug:** `trading_corp/agents/strategies/bitunix_htf_regime.py:725-737` (`_atr_pct_to_tier`)
sets the high→Extreme boundary at the `high` threshold (3.0%) and does NOT read the
`extreme: 5.0%` value from `config/strategies.yaml:1268-1272` — the `extreme` key is dead
config. The strategy size-zeroes any trade (`size_multiplier=0.0`,
`hard_zero_reason="vol_tier_extreme"` at `bitunix_htf_regime.py:990-1001`; abandoned under
`htf_gate.mode=enforce` at `bitunix_futures_observer.py:1410-1416`) when BTC 1D ATR ≥3.0% —
which is normal BTC volatility, not extreme.

**Empirical evidence:** BTC 1D ATR has been ~4% since 2026-06-03; strategy traded **9×** on
2026-06-02 (ATR 2.92%, "high" band; Day-2 audit snapshot at 06-03T01:08Z showed 6 resolved),
zero since (ATR ~4%, hits the effective "extreme" band → size 0). Confirmed via signal-pipeline
trace: scoring + PA + HTF-regime all alive at high volume through 06-08; the directional gate
grants "short full size"; the final volatility hard-zero nulls the size. A6 regime trace shows
06-03/04/05 were STRONG_BEAR (tradeable) yet suppressed — ruling out a "correct chop stand-aside."
Distinct from the well-calibrated PA validator (`feedback_pa_gate_well_calibrated`); this is the
HTF vol-tier classifier.

**Impact:**
- Phase 3 paper-mode observation window (2026-06-02 → 2026-06-09) contaminated — strategy
  dormant 6 of 7 days. Cannot judge live-readiness from this window's data.
- Same cutoff would suppress real trades in live mode the moment `execution_mode` flips,
  defeating the strategy's intent.

**Fix scope:**
1. Read the `extreme` threshold from `config/strategies.yaml` per the existing config pattern.
2. Verify the other bands (`high`/`normal`/`low`) also read from config or are documented as
   hardcoded-by-design.
3. Backtest validates ≥5.0% as the intended cutoff against historical BTC data — confirm it
   produces a reasonable trade-eligible regime distribution before shipping.
4. Per CLAUDE.md §4: strategy-parameter change → Backtester approval required before any code
   change. Run the backtest first.

**Prerequisites for `execution_mode` flip:** this fix must land + a fresh paper-mode
observation window must be observed before any flip decision is meaningful. The current
2026-06-02 → 2026-06-09 window is invalidated by this finding.

**Reference:**
- Investigation report: `reports/2026-06-08_bitunix_silence_investigation/FINDINGS.md`
  (branch `bitunix-silence-investigation-2026-06-08`, verdict commit `9e9053b`).
- Phase 3 paper-mode observation window: `runbooks/deploy_log.md` entry 2026-06-02.
- CLAUDE.md §4 ("Things to ask before doing") — strategy-parameter change gate.

**Priority: P1 — RESOLVED (deployed 2026-06-09, merge `7834375`).** The wiring bug is
fixed and live; the only remaining gate to a live-flip is a fresh paper observation window
(started 2026-06-09 03:49:41 UTC), not this bug.

## P3 — Bitunix HTF volatility config has orphaned `high` threshold after Option 1a wiring fix (filed 2026-06-08)

After the P1 fix (commits `ab0d251` source + `ea92d4c` tests), the
volatility classifier final tier boundary reads `tier_thresholds["extreme"]`
(5.0%). The `high: 3.0%` config value becomes dead — present in config but
unused by the classifier. Same drift class as the original P1 bug, just
relocated by virtue of the 4-tier-vs-4-threshold structural redundancy.

**Resolution options (each defensible):**
1. Remove `high` from config schema + defaults (Option 1b from fix-session
   Phase A analysis). Cleanest end-state, hot-reload-sensitive.
2. Add a 5th tier (e.g., Elevated for 3-5%) so both thresholds stay live
   (Option 2 from fix-session). Requires backtest, real strategy-design
   decision.
3. Leave as-is with explicit documentation that `high` is vestigial (the
   `_atr_pct_to_tier` docstring already names it as such as of `ab0d251`).

**Priority: P3.** Not gating. The classifier behaves correctly per operator
intent (`extreme: 5.0%` knob is live); cleanup is honesty + schema hygiene.

**Reference:** P1 fix commits `ab0d251` (source) + `ea92d4c` (tests);
original Phase A analysis at fix-session worktree
`bitunix-htf-vol-classifier-fix-2026-06-08`.

## P3 — B1 hardening: malformed `stop_price` silently attaches NO server-side stop (filed 2026-06-11)

In `BitunixBroker._build_order_body` (B1, merged `5edf8ea` / deployed 2026-06-11), a non-float /
non-positive / absent `extra["stop_price"]` on a live OPEN entry causes the order to place with NO
attached server-side stop — silently (fail-safe-to-no-SL, not fail-closed-to-reject). Defensible
default (better than placing a bad/wrong-side stop), but a silent naked live entry warrants a
**log-warn or an order-reject** for defense-in-depth. Natural to address alongside the ratchet /
`modify_position_tp_sl_order` build. Ref branch `bitunix-b1-entry-attached-stop-2026-06-10`.

## P2 — Bitunix `code=10006 'request too frequently'` on account polls (filed 2026-06-10 via Day-2 review)

Account-poll WARNINGs + 1 replay-fetch ERROR (order `171d7a46`, retried OK)
observed during Day-2 window review. Self-recovering today. P2 not P3
because snapshot failure falls back to the placeholder-equity sharp edge
(H-11 class) — live-flip-relevant. Fix: poll backoff/jitter on 10006.
DO NOT fix mid-observation-window.
Reference: `reports/2026-06-10_bitunix_day2_expanded_review.md`.

**ESCALATED — go-live + read-only investigation (2026-06-13, post-go-live PID 2637434).**
Bitunix is LIVE since 15:36 UTC, so this now touches the REAL-MONEY path. New
gate supersedes the Day-5 date: **do NOT fix until after the first real live fill
is observed** (operator plan); the fix is a separate post-first-fill session.
Read-only investigation (agent SSH + code read, no changes):
- **Scope: all 10006 are on `/api/v1/futures/account` (balance) only** — 10× since
  the 15:36 boot, in 2 short bursts (15:52:06 ×6, 16:07:39–40 ×4). ZERO on position
  reads, order placement, or fill confirmation.
- **Does NOT trip the snapshot-staleness halt.** BitUnix returns HTTP 200 with
  `code=10006` in-body, so `r.raise_for_status()` (bitunix.py:419) does not raise;
  the `code!=0` branch logs a warning + `continue`s (bitunix.py:421-426). `snapshot()`
  still reaches `_last_successful_snapshot_ts = time.monotonic()` (bitunix.py:488)
  → freshness preserved → `_assert_snapshot_fresh()` (bitunix.py:556) not tripped.
- **Does NOT block/delay/mis-fire placement or fills.** `place_order` (bitunix.py:829)
  uses the place/position-mode/leverage/fill-observe endpoints via the `_request`
  retry helper — NOT `/account`.
- **Root cause:** the per-coin account loop (bitunix.py:411-419) issues 2 back-to-back
  `/account` GETs (USDT, USDC) with NO throttle/cache/retry on the account call, and
  multiple periodic poll loops (per-division pollers 30–300s in main.py, the 5-min
  equity_snapshot loop, dashboard + webhook-sizing snapshots) call `snapshot()`
  concurrently → bursts exceed BitUnix's /account rate limit.
- **Residual (low-prob, conservative):** a 10006 racing an order-time sizing snapshot
  under-counts equity (skipped coin) → that one entry under-sized or skipped
  (conservative; never over-trades). Could dent first-fill observation fidelity; not
  a safety risk.
- **Priority stays P2 (not raised to P1):** (b) confirmed it does NOT threaten the
  staleness halt or the order/fill critical path; impact is bounded + conservative.
- **Fix direction (design only — DO NOT implement; §4 real-money pipeline + pytest +
  prod-vs-main md5 sweep gate; post-first-fill):** single-flight / short-TTL cache on
  `snapshot()` so concurrent callers share one account read; and/or client-side
  throttle/spacing on `/account`; and/or dedupe overlapping pollers to one shared
  snapshot. Optionally add backoff-retry to the account call (currently skips →
  under-counts), but caching/single-flight is the primary lever (retry alone doesn't
  reduce rate pressure).
- **Verdict: live path SAFE to keep running to the first fill.**
Reference: go-live deploy_log entry 2026-06-13 15:36 UTC; this session's read-only investigation.

## P3 — Event-loop contention under live load → TradingView webhook delivery timeouts (filed 2026-06-13 post-go-live)

Background webhook-processing latency (`webhook_received` → handler completion) grew
from ~1.5–3s pre-go-live (paper, PID 2608235) to **8–10s post-go-live** (live, PID
2637434), measured in journalctl. The TV-driven handlers (`web/webhooks.py`) respond
HTTP 200 immediately and dispatch heavy work via `background_tasks.add_task`
(webhooks.py:281), so a TV "delivery timed out" is NOT slow handler logic — it
indicates the event loop is too contended to service/flush the request within TV's
~10s window. **Currently harmless:** the only TV strategies (`market_cypher`,
`lord_otter`) are `enabled: false`, so timed-out signals are received + ignored
(zero missed entries — confirmed in the 2026-06-13 webhook diagnostic). **Becomes
real if any TV-driven strategy is enabled live** (timeouts would hit tradeable
signals; the server still processes them in background, but TV reports failure).
Likely contributors: live BitUnix broker load + the 10006 bursts (see the P2 10006
item) + always-busy copy-trader batches. Action: profile event-loop blocking under
live load (sync/CPU-bound calls in the loop; the BitUnix snapshot/account path is a
candidate). P3 unless a TV-driven strategy is slated to go live.

> **2026-07-13 cross-ref — DB-lock-storm fix (SHIPPED):** the checkpointer-isolation fix
> (LangGraph saver moved off the shared `trading_corp.db` onto its own `data/checkpoints.db` +
> `synchronous=NORMAL`; `deploy_log.md` 2026-07-10 entry, verdict SHIPPED 2026-07-13, 0 locks / 72h+
> incl. a live Mon PMCC burst) removed one event-loop stall source relevant HERE — the blocking
> `time.sleep` db-lock retries in `log_event`/`log_proposed_order` that froze the loop ~16s/victim
> during PMCC approval bursts. Two deferred hardenings (built NEITHER — not needed post-#3, kept as
> options): **#5 = offload those blocking retries off the loop (thread executor) then raise the
> shared `busy_timeout`** — a DIRECT candidate for this P3 item if a TV-driven strategy goes live;
> **#4 = lock-retry wrapper on `set_agent_state`/`delete_agent_state`** (bucket-D no-retry sites).
> Detail + the ready two-phase deploy runner: `reports/2026-07-10_db_lock_storm_diagnosis.md`,
> `scripts/deploy/deploy_db_lock_fix.ps1`.

## P3 — `assert_live_ready` has no `bitunix` branch → creds gate is a no-op for the live bitunix path (filed 2026-06-13 at go-live)

`assert_live_ready` (`utils/secrets.py:403-448`) validates creds for
robinhood/coinbase/fidelity/tastytrade/polymarket but has NO `bitunix` branch. So
`--live --brokers bitunix` passes the LIVE preflight on `ANTHROPIC_API_KEY` presence
alone, without verifying `BITUNIX_FUTURES_API_KEY/SECRET`. The CLAUDE.md STOP-AND-READ
#3 "populated creds via assert_live_ready" is thus generically true but unenforced for
bitunix. **Backstopped today** by the broker stub-mode guard: with missing creds,
`BitunixBroker.place_order` raises NotImplementedError (bitunix.py:843-848) →
fail-closed (no silent live order on a stub broker). So this is a defense-in-depth gap,
not an active hole (creds are present — confirmed by the live $343.07 equity read).
Fix: add a `bitunix` branch to `assert_live_ready` asserting key+secret presence.
§4-adjacent (creds/secrets path) — explicit approval + test before deploy.

(Note: the Fidelity Playwright Firefox `ENOENT` → paper-fallback surfaced at the
go-live boot is ALREADY filed below as a P3 — not duplicated here.)

## P1 — Bitunix post-window analysis: silence-window what-if backtest + TP-structure review (filed 2026-06-10, execute after Day-5 close-out 2026-06-14)

**2026-06-14 UPDATE — fee-gate slice RESOLVED; A (silence-window) + B (TP-structure) still open.**
Report `reports/2026-06-14_bitunix_fee_gate_analysis.md` (branch
`bitunix-fee-gate-analysis-2026-06-14`, commit `676f33d`, **unmerged**) answered the
operator's live fee-gate observation via empirical 3m forward-replay of the **88
`fees_too_high_for_risk` declines** (77 unique) over 2026-06-09→14:
- **Verdict: HOLD the gate.** Declined set gross +0.039R → **net-taker −0.87R / net-maker −0.61R**;
  only 6/77 net-positive. Gate correctly rejects sub-fee-threshold scalps (median 1R = 0.10%·entry).
  Confirms the **2026-05-25 Board rejection of lowering `tp1_min_profit_multiplier`** on empirical
  (not theoretical) data. Validation V1 43/43, skip-reproduce 88/88, 0 ambiguous 3m bars.
- **Correction to file against the (b) maker deliverable:** the 2026-05-25 memo §9(b) called
  `tp_is_maker:true` "strict improvement, same selection." **It is not** — the flag couples
  cost-reduction with floor-relaxation (0.18%→0.128%), admitting an 18-trade band that is
  **net-maker −0.62R**. (b) is a cost lever for the *taken* set only; to get the cost benefit
  without admitting losers the floor's fee-basis must be **decoupled** from the booking fee-basis
  (code change, not a flag flip). Fold this into the (b) fill-rate-model deliverable.
- **Still OPEN:** P1-A proper (the 2026-06-02→09 vol-classifier-*suppressed* set — a different
  cohort than the fee-declines) and **P1-B TP-structure analysis** (why TP3 is rarely reached;
  alt-leg-target re-walk). The fee-gate replay touched the anatomy (TP3 reached 6/77; 21 tp1-only
  net-losers) but did not execute the alt-structure re-walk. Both remain §4-gated.

**2026-06-14 UPDATE — entry-timing analysis DONE → NEW §4-gated lever: cap PA-redeem.**
Report `reports/2026-06-14_bitunix_entry_timing_analysis.md` (branch
`bitunix-entry-timing-analysis-2026-06-14`, commit `bddfd50`, **unmerged**) tested the operator's
latency theory on the 42 fires over the window:
- **CONFIRMED — (i) latency, not (ii) threshold-looseness.** Synchronous gate chain is sub-second
  (0 bars); **PA-redeem is the sole multi-bar latency** (re-evals ~60s, rejects up to 25 bars/75min
  before passing). **64% of fires redeem-rescued; 40% (17/42) fire ≥1 bar late.** Early(signal)-vs-
  late(confirmation) entry counterfactual on the redeem cohort: early gross +0.31R / net-taker
  +0.01R vs **late −0.17R / net-taker −0.47R = +0.48R/trade lost to latency**; 6/17 flip win→loss;
  median 40% of the signal→TP1 move spent before the fire.
- **NEW RECOMMENDED LEVER (§4-gated): cap/remove the PA-redeem deferred entry (fire-fast-or-abandon).**
  Late redeem entries are value-destructive (net-taker −0.47R realized vs non-redeem −0.27R); the
  early edge is NOT capturable without firing on unconfirmed signals (= a separate, riskier (ii)
  hypothesis — the full PA-rejected-set early-entry walk, NOT done here). Backtest: `current redeem`
  vs `cap@1bar` vs `no redeem`, net-of-cost expectancy, late-fill priced at the fire bar (paper
  books the stale signal price → optimistic).
- **Caveat / reconciliation with the fee report:** even at ideal early timing the set is net-taker
  −0.15R overall — latency is necessary-not-sufficient. Two independent drags (fees on tight stops +
  redeem latency); **neither says "loosen the gates."** Complements
  `reports/2026-06-14_bitunix_fee_gate_analysis.md` (which tested entry *edge*; HOLD the fee gate).

**2026-06-14 — backtest-infra inventory DONE (pre-scoping for the PA-redeem-cap §4 test).**
`reports/2026-06-14_bitunix_backtest_infra_inventory.md` (branch
`bitunix-backtest-infra-inventory-2026-06-14`, commit `1cbddad`, **unmerged**):
- **Recommendation: EXTEND `scripts/backtest_bitunix_confluence.py`** — it has the corpus + score/PA
  gate loader but models NONE of the test's needs (no redeem loop, single-TP fixed-ATR not the v2
  3-leg trade_plan, signal-time entry only). **Build** the PA-redeem loop (`--redeem-cap` 0/1/∞);
  **graft** the v2 `build_trade_plan` + late-entry walk from this session's `etharness.py`/`fgharness.py`.
  Don't build-new; don't reuse the et/fg harnesses as-is (recorded-fire-driven, can't re-derive redeem).
- **DB / runnability caveat:** two backtest DBs — `data/btc_scalping.db` (indicator-enriched corpus)
  + `data/trading_corp.db` (local STALE; live bars/fires on prod). **High-vol regime present only at
  15m/30m (Feb-2026); at 3m the corpus is Mar–May 2026, a modest ~1.9× gradient ending 3 weeks before
  the live low-vol window.** Test is runnable on that spread but NOT a textbook rotation — consider
  ingesting a high-vol 3m period for a defensible regime-robustness arm (small data task, not tooling).

Two related questions, one session (~3-4h, read-only vs snapshot/prod):

A. WHAT-IF BACKTEST of the 2026-06-02→09 silence window: replay the
suppressed signals (signals fired + approved but size-zeroed by the
vol-classifier bug) through the v2 3-leg TP plan against 1m bars.
Output: trade count, W/L, R distribution, cum P&L of what was missed.
Known caveat: same intrabar TP-vs-advanced-SL ambiguity as the
reconciler P3 (`70d50f7`) — results are approximate, ±chronic variance.

B. TP-STRUCTURE ANALYSIS (the Day-2 Q3 finding): across all available
samples (live window + backtest set from A), characterize why TP3 is
never reached and wins average sub-1R (Day-2: TP3 reached 0/16, TP2
6 @ avg 0.78R, TP1-only 7 @ avg 0.17R, stopped 3 @ -1R, expectancy
+0.176R). Questions: is TP1 sizing/target taking too much too early?
Are TP2/TP3 targets calibrated for a volatility regime that doesn't
run? What would expectancy look like under alternative TP structures
(re-walk the same trades with modified leg targets — per-CLAUDE.md §4
Backtester-gate territory if it leads to a parameter change)?

Output feeds the live-flip decision directly: +0.176R expectancy is
thin against live fees/slippage; flip decision needs to know whether
TP-structure tuning is required first.
Reference: `reports/2026-06-10_bitunix_day2_expanded_review.md` (Q3),
P1 vol-classifier fix (RESOLVED `7834375`).

## Open items influencing the live-flip decision

These are operationally relevant but NOT formal flip-gates. Operator decides
whether to address before flip OR after flip.

### P2 — Low-equity Telegram alert for `bitunix_futures` division

Filed 2026-06-01 via Finding #10 triage. When equity drops below configurable
threshold (suggest 80% of starting), emit Telegram ping per CLAUDE.md HITL
surface direction (short ping + deeplink, no detail in body, debounced).
Reuses existing `safety_notifier` infrastructure.

Daily-loss-cap in risk gate is the structural safety; this alert is
belt-and-suspenders observability. Not gating execution_mode flip.

### P2 — Per-division configurable equity placeholder for webhook snapshot-failure fallback

Filed 2026-06-01 via Finding #10 triage (architectural review H-11 sharp edge).
Current behavior: webhook risk gate falls back to `equity = 100_000.0` on
snapshot failure. Defensible for paper-mode analytics; operationally dangerous
for live mode (sizes trade against placeholder).

Operator decision: change fallback to per-division configurable placeholder,
defaulting to small conservative value, with explicit per-division overrides:
- `bitunix_futures: 10000` (matches operator-stated discipline of keeping
  Bitunix topped up to $10K).
- `coinbase_spot: <operator-decided based on actual account>`.
- Default: $1K.

Mode-aware stand-down behavior (alternative architectural choice) deferred —
may revisit before `execution_mode` flip if operational patterns suggest it.

### P2 — Bitunix paper-trade `actual_pnl_dollars` persistence

Persistence path for actual P&L dollars per closed trade. Currently computed
on-the-fly; persistence simplifies dashboard rendering + reduces stale-data risk.

### P2 — Bitunix paper-mode cost-accrual (fees + funding)

Layer 2 follow-up to Session B's Layer 1 fee plumbing. Track cumulative
realized P&L net of fees + funding rate accruals across the observation
window.

### P2 — Bitunix dashboard full 5-panel rebuild

Separate session work. Dashboard tile rebuild to surface more decision-quality
signal (vs. current trade-flow-centric view).

### P2 — Bitunix PA validator raw-input audit

Instrumentation layer — capture inputs to the PA validator for later analysis
of decision quality.

### P1 — Bitunix PA validation observation window (closes 2026-06-03 ~23:18 UTC)

Separate PA-specific validation window. Verify validator behavior on observed
trades; close on schedule.

## Investigative items (paper-mode period, no rush)

### P3 — Investigate TP1 `target_r` calculation in v2 3-leg `tp_plan`

Filed 2026-06-01 via dashboard inspection of trade `2b418971-7955-4dd4-ae20-8e56d4c9401c`.
TP1 at `target_r=0.972`, TP2 at `target_r=1.000` (default_1r) — produced TPs
$3.75 apart, 75% of position effectively exiting at same level. Math is correct
for stated `target_r`; question is what produces TP1's non-clean R value.

`extra_json` surfaces `tp2_method="default_1r"` but NOT `tp1_method` — separate
finding: TP1's method should be auditable.

Read-only investigation, ~30-60 min. Locate v2 tp_plan construction (likely
`agents/strategies/bitunix_confluence.py`); identify TP1 method; assess whether
near-1R values in certain conditions are by design or a bug.

### P3 — Audit `proximity_to_support` / `proximity_to_resistance` hard-zero behavior

Filed 2026-05-31. HTF proximity rule may zero out trade probability when
support/resistance is "too close" but the operator's read of structural
significance differs from the mechanical detector. Related to AlexO market
structure framework Option B (body-close validation) — see CLAUDE.md skill
references.

### P3 — Post-Session-B audit of analogous paper-vs-live timing assumptions beyond Finding #5

Filed 2026-06-01 via Finding #10 triage Decision #6. After Session B lands AND
paper-mode exercise begins, brief audit of paper-vs-live timing assumptions
beyond cases 5a (classifier-bar vs broker-event) + 5b (`_observe_fill`
one-shot vs re-poll). Surface any additional cases that emerge from running
the new wiring.

Prerequisite: Session B merged (✓) + 1-2 weeks paper-mode exercise. Not gating.

### P1 — Revisit BitUnix scoring weights after ≥30 live PREMIUM fires post-H2

Tune scoring weights based on observed fire-rate + win-rate of PREMIUM-tier
trades after enough live PREMIUM fires accumulate. Needs ≥30 sample size.

---

# Priority 2 — Polymarket Copy Trading path to live trading

## ★ 2026-07-07 SESSION — edge measured, roster reassigned, option-c + item-1 DEPLOYED, dashboard reset, main reconciled

**Edge (report `reports/2026-07-07_polymarket_copy_edge_analysis.md`):** OLD manually-pinned roster had NO
tradeable copy edge (+0.54% blended dollar ROI = OPTIMISTIC zero-slippage; trustworthy settle-subset −0.66%;
May +13.7% / June −26.6% / July −8.2%; WR 53.6% < avg entry px 55.9% = no gross skill). Whale edge real +
dollar-verified but does NOT survive copying on that roster. Roster-dependent → reassigned (below). Do NOT stand
up EU/go-live until a properly-selected roster shows positive FORWARD copy edge.

**Roster reassignment APPLIED (board-auth, paper, 19:13 UTC):** `agent_state(polymarket_copy_trader, selected_whales
+ pinned_whales)` = 15 whales (removed 11 losers/unrankable incl damed21/jtwyslljy/slimjoe/AdrianCronauer/
BigodinSagaz; kept 7 copy-positive; added 8 realized-edge copyable directional whales — digitalnomad85, Hakei.,
Civic-Static, ChadStarmer, Moond, potatobrahh, LJa7io23MCv954j, monkeybar; makers cnyek/CandleHammerDrum/VBQZSXZ7
excluded). Hand-curated — do NOT blindly `refresh --algo-select` (the realized algo STILL ranks makers + a
net-loser Vanquish high: Wilson-WR-dominated w/ ROI floor). Old roster → `/tmp/pm_roster_backup.json`.

**DEPLOYED to prod + RECONCILED to main (deploy_log 2026-07-07; main commit e70af23):**
- option-c Phase 1 realized scorer (4 files @ c14e786, md5 df1ebed9/235b7612/dce46918/fcb79bc9), no restart
  (script-only isolated import chain). Verified: `refresh --dry-run` emits realized fields, naive
  `avg_pnl_per_contract` gone.
- item-1 `copy_quote_price` — logs real post-lag price on taken entries so slippage is measurable (copy_trader
  @ d1a874f md5 2f92049a; engine RESTARTED PID 97179, LIVE divisions preserved paper=False).
- dashboard `metrics_epoch` reset → `2026-07-07T20:00:54+00:00` (all pm panels scoped from now; non-destructive,
  6,281 pre-epoch RTs preserved).
- **main reconciled** e70af23 (bless-prod-as-truth, mirrors 3f60f9d): main now carries the deployed files. Fuller
  option-c Phase 2-4 stays undeployed in history — prod + main run Phase 1 ONLY.

**Execution gate CLOSED/paper (verified):** `auto_execute:false`, `standby:true`, 0 `filled` events ever — roster
change did NOT arm real money. Prod copy sizing still OLD tier ladder ($1/$2/$5); E2.3 flat-$1 clamp not deployed.

**NEXT:** let the new roster run a forward paper window → re-measure forward copy P&L (now slippage-instrumented
via item-1) — the clean test of whether a properly-selected roster has real copy edge. Only if clearly positive
does EU/go-live reopen. Latency lever: CLOB websocket / faster poll (feed lag = the slippage source; not
blockchain-limited). Full handoff: `reports/2026-07-07_next_session_prompt.md`.

## E2 — route the copy loop to the live broker — ✅ E2·1–E2·6 MERGED (loop wiring complete); E2·7 live-enablement + OP·E shakedown = remaining operator gate

**Reconcile 2026-06-29 (read-only git audit).** Header previously read "SCOPED 2026-06-14; branch
`e2-scoping-2026-06-14`, unmerged" — **STALE.** All agent-buildable E2 increments (E2·1–E2·6) plus the
exit-side E5 work are on **main AND origin/main (`f57ef35`)**; only the OPERATOR-gated E2·7 (live
enablement + $1 shakedown) remains.

E1 done + merged (`72e8dc6`); PCT wallet `0x2160…9F82` fully provisioned (OP·A/B/C ✓, 6/6 approvals,
119.98 USDC.e). E2 routes the **PCT** copy loop `would_have_placed` → `data_exec.place()` →
`PolymarketLiveBroker` → `FillEvent` (arb stays paper). Full scope + verified path-map:
[`reports/2026-06-14_polymarket_e2_scoping.md`](reports/2026-06-14_polymarket_e2_scoping.md). Operator
decisions baked in: token_id via `activity.asset`; **synthesized-FAK** order type (0.17.5 has no native
FAK/IOC — GTC+poll+cancel-remainder, configurable); **no HITL** (whale-promotion is the approval);
**flat ≈$1** sizing default (full schema, conviction off); per-division live isolation; DB
`execution_mode` column.

Increments (all ✅ merged to main+origin `f57ef35` unless noted):
- **E2·1** ✅ `3016513` — `token_id` → `extra` (`_emit_entry`/`_emit_exit`) + main.py base_payload.
- **E2·2** ✅ `3b47c16` — `order_type` config (`fak_synth` default) + `fak_poll_seconds`; broker synthesized-FAK (GTC→poll→cancel remainder→filled-portion FillEvent).
- **E2·3** ✅ `fa42f2c` — replace `_size_tier_usdc` with clamp formula + schema; flat ≈$1 default, conviction off.
- **E2·4** ✅ `062186d` — per-division live select (`--live-divisions`); `is_live_division` by slug — division-level anti-half-flip (PCT live, arb paper).
- **E2·5** ✅ `f692fa2` — add `execution_mode TEXT DEFAULT 'paper'` to `proposed_order` + `paper_trade_record` (idempotent migration); written at placement.
- **E2·6** ✅ `7b2b70e` — PCT loop wiring: gated live placement (`isinstance(broker, Broker)`), `NoFillInWindow` no-fill handling, partial-fill write-back (records ACTUAL filled qty). *(mocked/fundless)*
- **E2·7** ⏳ **OPERATOR-only — live enablement + OP·E $1 shakedown.** Deps prep DONE: `setuptools<81`
  lock fix on main + `--require-hashes` smoke GREEN (`7530ccc`); step-3 install/cutover runbook
  (`21d3f59`, `reports/2026-06-15_polymarket_e2-7_step3_install_cutover_runbook.md`). Remaining = the
  prod deps install + `systemctl restart` (bounces ALL live divisions → flat window) + flip PCT
  `broker:paper→polymarket` / `--live-divisions polymarket_copy_trading`, then the $1 shakedown.

Exit-side **E5** ✅ merged: E5a `64a93df` (execution config relocated to the Division), E5b
`5ff8f1a`/`17c3e19` (exit escalating-chase + reconciliation; **mechanism inert / off by default**).
Carry-forward: dashboard paper/live filter UI = backlog (DB column ships in E2·5).

## P1 — Polymarket copy-trader SELL-pairing → option (c) — ✅ FIX MERGED (Phases 1–4 all on main+origin `f57ef35`); inert until a deliberate prod refresh run

**Reconcile 2026-06-29 (read-only git audit).** This entry previously read "Phase 1 merged, phases 2-4
UNSTARTED" — that was **STALE.** Git truth: option (c) is **fully implemented; all four phases are on
main AND origin/main (`f57ef35`, in sync):**
- **Phase 1** (copy roster `refresh_polymarket_whales.py`) — merge `b137c03`, 2026-06-10. Screens on
  REDEEM-grounded realized P&L (`build_audit_report` + `score_whale_from_audit`): decision-unit Wilson
  WR × realized ROI × category bonus, `pnl_inflation_ratio` exclusion gate (default 0.5),
  window-truncation gate (pin-overridable), `/activity` walk-to-exhaustion.
- **Phase 2** (observation roster `seed_polymarket_watchlist_deep.py`, the Sunday `watch_only_whales`
  job) — merge `1c0b52e`, 2026-06-10. Same REDEEM-grounded compute.
- **Phase 3** (unify) — merge `3d8cc1a`, 2026-06-13. Extracted shared `trading_corp/data/whale_screening.py`
  + removed the refresh→seed coupling (byte-identical, 54 tests green).
- **Phase 4** (cleanup) — merge `1327764`, 2026-06-13. Dropped the seed re-export shim.

**What this closes:** the screening layer now identifies "winning traders" from accurate REDEEM-grounded
realized P&L over the activity feed — sidestepping the copy SELL/BUY-pairing path that produced the
~99.86% `skipped_no_entry` rate. **Screening-accuracy blocker = resolved in code.**

**⚠ One operational step remains (operator-gated, NOT code): the compute is inert on prod until a
deliberate `refresh_polymarket_whales` run.** A default run is **PINS-ONLY** (writes only pinned whales,
never auto-expands the roster); `--algo-select` is the explicit opt-in that surfaces the new algo picks
for manual promotion. Until that run, the live roster still reflects the pre-option-(c) screen. Phase-1
validation: [`reports/2026-06-10_polymarket_option_c_phase1_validation.md`](reports/2026-06-10_polymarket_option_c_phase1_validation.md)
(realized reconciles to Polymarket `/closed-positions` to the dollar on complete windows; 2256 tests pass
/ 28 pre-existing). The SELL-pairing resolver / `round_trips` display path (structural-causes detail
below) is a SEPARATE accounting concern, not the screening blocker — retained as historical context.

**Known autopause-pin flap (benign):** a pins-only refresh rewrites the full pinned set, which
currently includes the 2 autopaused whales (Johnnyboy42069, damed21 — still pinned) →
`selected_whales` 13→15 transiently until `_whale_autopause` re-removes them (~60s). Harmless
(paper-mode, operator-pinned, whale-own-profitable per Phase E reconciliation). Clean resolution
(unpin, or have autopause also clear `pinned_whales`) belongs with the **demotion-transparency P3
entry below** — do NOT touch autopause for this.

**Status (2026-06-09): investigation COMPLETE, option (c) selected as fix path.**
Full findings: [`reports/2026-06-09_polymarket_sell_pairing_investigation.md`](reports/2026-06-09_polymarket_sell_pairing_investigation.md)
(on branch `polymarket-sell-pairing-investigation-2026-06-09`, unmerged planning
artifact — mirrors the Robinhood Agentic evaluation pattern).

**Selected fix — (c) net-position whale P&L from the activity feed.** Compute each
whale's P&L from net position + VWAP entry/exit over the `ActivityRow` stream we
already ingest, instead of pairing our copy SELL/BUY audit rows. Sidesteps BOTH
structural causes below. (a) deferred
(subsumed by (c) for the stated goal); (b) rejected (removes 97% of trade volume);
(d) rejected (type-mismatch refuted — `outcome_index` is `integer` on every row).

**Implementation SCOPED (2026-06-09):** [`reports/2026-06-09_polymarket_option_c_implementation_scoping.md`](reports/2026-06-09_polymarket_option_c_implementation_scoping.md)
(branch `polymarket-option-c-scoping-2026-06-09`, unmerged planning artifact).
Framing (ii): the REDEEM-grounded compute already exists and is deployed
(`build_audit_report`, `data/polymarket_whale_audit.py`, `df3e48b`) — option (c) is
*operationalization*, not greenfield. **Revised scope: ~1–2d for Phase 1** (route the
copy-roster screen `refresh_polymarket_whales.py` through `build_audit_report`) vs. the
original ~3–5d. Phasing: P1 copy roster → P2 observation roster (`seed_*_deep`) → P3
unify both onto shared compute → P4 cleanup. Coexistence gap surfaced: add an
`auto_paused_whales` agent_state key so a refresh doesn't silently re-add whales
`_whale_autopause` dropped (shared `selected_whales` key today). 5 operator-resolved
decisions in the doc (selection metric, branch disposition, validation gate, autopause
model, refresh cadence). The superseded `pm-watchlist-pnl-aggregation-fix` branch is
NOT a prerequisite (its fix is already on main, `899821d`). Implementation UNSTARTED.

**Dual structural causes (verified against prod, read-only):**
- **Partial-fill duplication (4.6×):** 5,084 copy BUY `would_have_placed` rows
  collapse to 1,115 real `(whale,condition,outcome)` positions; 130 positions
  carry 10+ BUY rows (one had 216; one burst was 66 buys in 582 s).
- **Settle-path contention:** `polymarket_resolver._fetch_unresolved_orders`
  (`agents/polymarket_resolver.py:65-83`) resolves copy-trader BUY rows into
  round-trips keyed on the BUY's `order_id`, consuming them before sell-pairing
  can use them. 466/484 (96%) of the "BUY-exists-but-unpaired" SELLs lost their
  BUY this way; 90% of all copy round_trips (4,565/5,058) are settle-derived.
- Of 874 unpaired SELLs: 55% have a consumed BUY (above), 44% have no BUY logged
  at all (entry never copied), <1% had a risk-rejected BUY.

**Background:** `polymarket_resolver._pair_pending_exits` re-scans ~720
unpaired copy-trader SELL `would_have_placed` rows every tick and skips ~99.86%
as `skipped_no_entry` — can't find prior BUY (matched on `whale_wallet` +
`condition_id` + `outcome_index`, entry `ts` < sell `ts`, unpaired).

**Operator hypothesis (2026-06-02):** the SELL-pairing problem may not be a
bug in trade-matching logic. Likely cause: whale orders that fill incrementally
over time (one $50K limit order filling in 50 chunks) appearing as 50 separate
trades. If true, the fix is upstream — aggregate partial fills of the same
order before treating them as separate trades — not "match SELL to BUY"
algorithmic work.

**Investigation needed (read-only, ~30-60 min, prerequisite to engineering fix):**
1. Sample skipped SELLs: does a matching BUY actually exist in `audit_event`?
2. Type-check `outcome_index`/`whale_wallet` in BUY vs SELL payloads.
3. Determine fill pattern: are the skipped SELLs from whales who split orders
   over time? Or whales we never copied a BUY for?
4. Quantify: how many profitable whales bet in single chunks vs split? Could
   the watchlist filter out splitters without losing significant signal?

**Decision branches after investigation:**
- (a) Engineering fix: aggregate partial fills upstream (probably moderate scope).
- (b) Operational workaround: filter watchlist to whales whose bet patterns
  don't trigger split-fill behavior (probably small scope).
- (c) Algorithm change: compute whale P&L via net position + entry/exit average
  prices without trade-pair matching (probably large scope).

**Why this WAS the highest-impact open item (RESOLVED IN CODE 2026-06-29):** the copy-trader couldn't go
live until whale P&L attribution was accurate; the 99.86% skip rate made "winning trader" identification
unreliable. Option (c) (Phases 1–4, above) fixes this at the screening layer. Realizing it on the live
roster = the operator `refresh_polymarket_whales` run noted above. **No longer the structural blocker for
Priority 2** — the remaining live-execution gates are operational:
1. **Deps lockfile prod-deploy** — `setuptools<81` fix on main, `--require-hashes` smoke went green;
   needs the install + a `systemctl restart` that bounces ALL live divisions (Bitunix + PEAD) → run at a
   flat window. (E2·7 prereq.)
2. **E2·7 live enablement** — flip PCT `broker:paper→polymarket` + `--live-divisions polymarket_copy_trading`;
   the loop wiring E2·1–6 is merged (`PolymarketLiveBroker` built; PCT wallet provisioned, 6/6 approvals,
   ~120 USDC.e).
3. **OP·E $1 shakedown** — first real-money order; also the ONLY test that closes the order-submission
   geo/jurisdiction residual (`runbooks/eu_proxy_smoke_test.md` task #31 + `reports/2026-05-29_polymarket_live_prep_groupB_spike.md`
   Track 1a: reads + authed surface reach the US Azure IP fine; EU-proxy NOT needed; only signed-order POST
   jurisdiction is unproven, provable only here).

## P3 — Polymarket whale demotion transparency in dashboard (filed 2026-06-10 via workflow verification)

**Context:** Verification session 2026-06-09 confirmed autopause runs every 60s on prod (per `polymarket_copy_trader.py:185 → :571`). Three autopause events recorded (2026-05-15, 2026-06-03, 2026-06-09). System operates correctly per code; operator workflow now explicitly includes both manual demotions AND system-driven autopause demotions. Operator accepts the autopause functionality but needs transparency on demotion events.

**Operator requirement:**
1. Dashboard surface: visible status for whales recently demoted (system or operator).
2. Promotion-guard: when promoting from watchlist, surface previous demotion history for that whale to prevent inadvertent re-promotion of known-bad performers.

**Scope:**
- New dashboard tile/section showing recent demotions (autopause + manual) with: wallet, user_name, demote_ts, source (autopause/operator), reason (autopause: trade count + WR% + P&L; operator: optional note).
- Promote-button modal: if wallet has prior demotion event, surface warning with demotion history.
- Source data: autopause writes audit events today (`polymarket_whale_auto_paused`). Manual demotion path needs to write equivalent (or already does — verify).

**Implementation notes:**
- Audit event already exists for autopause (`polymarket_whale_auto_paused`).
- May need parallel audit event for manual demotion if not already present.
- Dashboard query against audit_event filtered to demotion-class events.
- Promote-button check: query demotion history for wallet before write.

**Not gating:** any active development. Quality-of-life feature for operator transparency.

**Reference:** workflow verification report `reports/2026-06-09_polymarket_workflow_ground_truth_verification.md`.

## P3 — Polymarket `/activity` pagination ceiling caps full-history reconciliation (filed 2026-06-10 via option (c) Phase E re-validation)

**Finding:** the public `/activity?user=` feed stops serving past ~3,500 rows (page 8 at
`limit=500`) — a Cloudflare-403 / hard pagination cap. For the highest-volume, longest-history
whales their full BUY/SELL/REDEEM history is NOT retrievable, so REDEEM-grounded realized P&L is
computed on a truncated window and **over-states** (incomplete cost basis). Reproduced 2026-06-10
on AdrianCronauer (`0xf9c1…`, walk `fetch_error` at 3,500 rows; my realized $1.56M vs Polymarket
`/closed-positions` $108k) and BigodinSagaz (`0xca1e…`, 25/168 matched positions over-stated).
Whales with complete windows reconcile to the dollar (Magamyman 85/85, kitten147 156/162) — so
this is an API-retrieval limit, **not a compute bug**.

**Containment already shipped (option (c) Phase 1):** such whales are flagged `window_truncated=true`
and **excluded from algorithmic selection** (gate `f448c93`; proven by
`test_truncated_whale_cannot_enter_algo_selected_roster`). The over-stated number cannot drive a
copy-roster pick — this P3 is about *completeness*, not safety.

**Investigate (not blocking):** is offset >3,500 ever fetchable for these wallets (longer backoff /
retry / alternate endpoint), or is it a hard cap? If hard, the highest-volume whales are permanently
unrankable by the algorithm (manual pin remains available). Matters because longest-history whales
are plausibly among the most interesting to copy.

**Reference:** convergence table in `reports/2026-06-10_polymarket_option_c_phase1_validation.md`
(Re-validation section). Relates to the Cloudflare-retry-burn P2 and the 0.0s-backoff P3 below.

## P3 — Polymarket audit-cache unification deferred: cache key `(wallet, activity_max_ts)` is scope-blind (collision risk) — filed 2026-06-12 via option (c) Phase 3 Phase B

**Context:** option (c) Phase 3 "Option 1" (small extraction, branch
`polymarket-option-c-phase3-unify-2026-06-11`) shared the per-candidate
`/activity` walk + loop wrapper between `refresh_polymarket_whales` and
`seed_polymarket_watchlist_deep` via the new `trading_corp/data/whale_screening.py`.
The audit cache (`agents/research/polymarket_whale_audit_cache.py`,
`read_audit`/`write_audit`) was **deliberately left OUT of that extraction.**

**Collision risk (why it can't be folded in under a byte-identical mandate):**
the cache key is `(wallet, activity_max_ts)` — **scope-blind.** `refresh` builds
a FULL-window `WhaleAuditReport`; `seed` builds a WINDOWED (last-100-decision)
report. For the SAME wallet at the SAME `activity_max_ts` those are DIFFERENT
reports. `refresh` reads/writes the cache today; `seed` deliberately skips it
(see the comment at `seed`'s windowed `build_audit_report` call). If `seed` were
wired to read-through the existing cache, a `refresh` entry would be served for
`seed`'s windowed request (or vice versa), silently corrupting one roster's
realized PnL.

**Why deferred, not done:** unifying requires adding a scope discriminator to
the key (e.g. `(wallet, activity_max_ts, window_scope)`) → a NEW key shape →
NEW cache entries → a behavior change, not the byte-identical refactor Phase 3
Option 1 was scoped to. It is therefore separate, behavior-affecting work.

**Scope if picked up:** add the scope discriminator to the cache key + writer +
reader; decide whether `seed` should cache at all (today it recomputes each run
— correct but slower); add a test that a `refresh` full-window entry and a
`seed` windowed entry for the same `(wallet, ts)` do NOT alias; validate
realized PnL unchanged for both rosters.

**Priority: P3.** Not gating — `seed` skipping the shared cache is correct today
(no collision is possible while it doesn't read it). This is a
performance/architecture item, and a TRAP flag to stop anyone later "optimizing"
`seed` by pointing it at the audit cache without keying by scope.

**Reference:** Phase A duplication map
`reports/2026-06-11_polymarket_option_c_phase3_phaseA_duplication_map.md` §4
("The audit cache cannot be unified inside a byte-identical refactor"); Phase
B/C/D extraction on branch `polymarket-option-c-phase3-unify-2026-06-11`
(`reports/2026-06-12_polymarket_option_c_phase3_phaseBCD_extraction.md`).

## P3 — Polymarket retry backoff: currently 0.0s on 429 responses

Filed 2026-05-31. Cloudflare/Polymarket 429 responses hit retry path with
backoff=0.0s — effectively no backoff. Likely a `max(0, computed_delay)` bug
or missing minimum-backoff floor.

## P3 — Polymarket: add `division` column to `polymarket_round_trips`

Filed 2026-05-09. Copy-trading reuse of the round_trips table needs division
disambiguation for per-division P&L attribution.

## P3 — Polymarket Gap C: open-positions cache (paper-mode equivalent)

Filed 2026-05-09. Open positions cache for paper-mode parity with live mode.

## P3 — Polymarket portfolio dashboard (betmoar.fun-inspired)

Filed 2026-05-09. Division-reusable portfolio view. Lower priority than
SELL-pairing.

## P2 — Polymarket dedupe follow-up: underlying/series-level concentration cap

Filed 2026-05-21. Blocked on per-`condition_id` cap ship (operator-approved
2026-05-21) + post-cap data review. Verify ship status before working on
this follow-up.

---

# Priority 3 — InfoSec

## P1 (recurring) — Run InfoSec Architect audit, file findings as priority items

Operator runs the InfoSec Architect skill periodically; findings get filed
into this file as priority items (P0/P1/P2 based on severity).

**Last full audit:** 2026-05-21 — `reports/2026-05-21_security_review.md`
(committed `e88d663`). Identified 7 CRITICAL (S-1 through S-7), 17 HIGH,
22 MEDIUM, 13 LOW findings.

**Status of 2026-05-21 audit findings:** mostly remediated per operator
(2026-06-02 grooming). The roadmap below preserves the critical findings list
for reference; specific status of each should be verified by next audit run.

**Next scheduled audit:** open (operator-triggered).

### Open InfoSec items from 2026-05-21 audit (verify status on next run)

**P0 CRITICAL roadmap (from 2026-05-21 review, may be substantially complete):**

| # | Finding | Original Effort |
|---|---|---|
| S-1 | Local `.env` may hold full live secret set in plaintext. Rotate every secret + depopulate workstation `.env` to just `KEY_VAULT_URI=`. | 1–3h |
| S-2 | `TradeConfirmation.verdict == "push_back"` skips `RiskAgent.evaluate()`. Route through risk gate as forced-reject. | 1–2h |
| S-3 | `_check_auto_execute` re-reads `strategies.yaml` per-order with no mtime cache, no schema validation. | 2h |
| S-4 | Timer service units run as `User=root` with no sandbox directives. Rewrite as `User=azureuser` + sandbox. | 2h |
| S-5 | No production DB backup. Nightly `sqlite3 .backup` → encrypted Azure Blob. | 4h |
| S-6 | No dependency lockfile / hash pinning. `pip-compile --generate-hashes` → `requirements.lock`. | 30m–1h |
| S-7 | Rejected-webhook audit writes `raw[:500]` containing secret in plaintext. Scrub + backfill. | 1h |

**HIGH/MEDIUM highlights from 2026-05-21 review:**
- HIGH H-1/H-2/H-3: Replace static-bearer webhook auth with HMAC-SHA256 + replay window.
- HIGH H-10: Telegram bot has no sender-ID allowlist.
- HIGH H-12: 4 DR runbooks needed (VM compromise, KV compromise, broker-key rotation, panic halt).
- HIGH H-13: Azure VM has no Trusted Launch (Secure Boot, vTPM).
- HIGH H-15: No CI pipeline. GitHub Actions + branch protection + signed commits + `pip-audit` + `bandit` + `trufflehog`.

Full report: `reports/2026-05-21_security_review.md` §5.

### Specific open items surfaced or filed since 2026-05-21

#### P1 — Tastytrade env vars bypass KV path

`TASTYTRADE_PROVIDER_SECRET` and `TASTYTRADE_REFRESH_TOKEN` loaded via systemd
`EnvironmentFile=/etc/trading-corp/tastytrade.env` instead of KV. Bypasses
`_populate_from_keyvault`, the `_SECRET_KEY_NAMES` redaction list, and
`register_redact_literal()` calls. Creates parallel secret-handling path
outside documented KV-first architecture.

Fix path: upload secrets to KV, patch `utils/secrets.py` to include the two
keys in `_SECRET_KEY_NAMES` + `expected_env_vars`, remove the `EnvironmentFile=`
drop-in, shred the file. Bundle with AM SDK-bug fix branch (both touch same
provider).

Risk if deferred: low marginal (creds already on prod, 600 root-owned). Cost:
no rotation via KV, no audit, redaction filter blind to the values.

#### P1 — Real SMTP for Authelia notifications

Filed 2026-04-30. Maps to H-14 in 2026-05-21 review.

#### P2 — Tighten prod-access permission rules in `.claude/settings.local.json`

Filed 2026-05-22. Maps to AI-attacker-angle section of 2026-05-21 review.

#### P2 — Polymarket + Kalshi deep-watchlist timers run as root

Filed 2026-05-23. Maps to S-4 from 2026-05-21 review (broader fix).

#### P1/P2/P3 — VM security state anomalies (from §7 verification)

Filed 2026-05-23. 13 commands from 2026-05-21 review §7 to verify on `tc-prod-vm`
(Caddyfile, Authelia, sshd, sudoers, unattended-upgrades, AppArmor, Defender,
VM Trusted Launch state, DB pragmas, Kalshi PEM tempfile cleanup). Verify
status on next audit run.

---

# Other Open Items (not in priority list above)

## P2 — Kill paper-trade path: remove SOL/XRP from bitunix_sfp config + expire stuck SOL paper row (filed 2026-06-30, operator-decided)

**Operator decision 2026-06-30:** do NOT build a paper resolver; KILL the paper-trade path instead. The
paper-sim/replay was RETIRED in the two-state collapse (`main.py:1743 _REPLAY_ENABLED=False`) and the reconciler
resolves LIVE rows only (execution_mode=live filter) — so `arm:watch` paper rows (SOL/XRP) get written on entry
but have NO resolver → sit open forever. All-time: 1 SFP paper fire, 0 resolved (SOL/XRP forward-track is inert).

**Do:** (1) drop SOL + XRP from `bitunix_sfp.symbol_modes` in strategies.yaml (BTC + ETH STAY `arm:trading`/live
— untouched); no more paper rows get written → the resolver gap becomes moot. (2) Expire/clean the 1 stuck SOL
paper row (`e450302a-a7b0-4181-9d06-eb722c201fbb`, SOL/USDT.P buy, entry 71.01 / SL 69.609 / TP 73.812,
`sfp_real_3m_bos`, opened 2026-06-28). (3) Prune dead `agents/paper_trade_replay.py` whenever.

**How:** strategies.yaml edit (azureuser-editable) + flat-guarded restart via runner; row cleanup via sqlite
(one-off). Then commit config to main for parity (like the Phase 2 cutover). Ref [[bitunix-two-live-phase1]].

## P2 — Tune SOL SFP (carefully, no overfit) → add live per-coin (filed 2026-06-30)

SOL SFPs are visually clean and the Mode-B detector fires them correctly (06-28 `sfp_real_3m_bos` 2R bracket
that would've hit TP) — detection is NOT the gap. But that is n=1/eye-selected; the backtest showed **no clear
SOL edge**. Revisit via a systematic SOL SFP backtest with **beats-null / no-overfit** discipline (see the
2026-06-29 OU/momentum diagnostic arc `reports/2026-06-29_ou_meanreversion_diagnostic/` for the method —
honest denominator, null gate, don't fit to the beautiful example). Add SOL (then XRP) to LIVE config ONLY
when a robust, non-overfit edge is confirmed. **Go-forward gate: add coins one at a time, each individually
tuned.** Own focused session. Ref [[bitunix-sfp-mode-b]], [[ou-meanreversion-dead-momentum-skew]].

## P3 — Remove `yfinance` from all use — free/undependable service (filed 2026-06-14 via Bitunix first-fill investigation)

Goal: eliminate dependence on `yfinance` (free, unofficial Yahoo endpoint — rate-limited, schema-drifts, returns empty/"delisted" with no warning).

**Known context (NOT a scope — see prerequisite):**
- **Bitunix first live fill (2026-06-14 18:24 UTC) surfaced it.** An open BTC/USDT.P
  position triggers a ~10s yfinance poll fed the perp symbol mistranslated to
  `BTCUSDT` (yfinance expects `BTC-USD`) → `404 Quote not found` + `possibly
  delisted; no price data (period=1d)` ERROR log-spam every ~10s (began ~8s after
  the fill; zero before). **Cosmetic for Bitunix** — the protection paths are
  confirmed independent of yfinance: replay-loop TP/SL detection uses **BitUnix
  native klines** (`_bitunix_kline_fetcher`, no-auth; replay `errors:0`), the
  catastrophic stop is server-side (`slPrice`), the snapshot-staleness gate uses
  the BitUnix broker snapshot. Ref `reports/2026-06-14_bitunix_first_fill_tp_investigation.md`.
- **Equity divisions likely use yfinance for stock quotes** (VIX, benchmarks,
  market-ribbon, PMCC/options price fallbacks, `data/feeds.py`) — **load-bearing-or-not
  is UNVERIFIED.** Do not assume cosmetic outside Bitunix.

**Prerequisite before ANY removal (this item is FILE-ONLY — no scoping/code yet):**
build a yfinance usage inventory + decide a per-use replacement first. Cosmetic
uses = safe delete; load-bearing uses (any price/quote/IV a division actually
trades or gates on) = wire a replacement source FIRST, then delete. Removal is
per-use, not a blanket rip-out.

## P3 — `TypeError: not all arguments converted during string formatting` ×123 in tastytrade-streamer/starlette logging path (filed 2026-06-10)

Cosmetic `%`-format bug; log lines still emit; zero functional impact on
bitunix or any division. Cleanup candidate.
Reference: `reports/2026-06-10_bitunix_day2_expanded_review.md`.

## P2 — `scripts/redeploy3_chunked_transfer.py` worktree-stranded

Filed 2026-06-02 via Phase 3 deploy. Script is referenced as canonical in
CLAUDE.md but doesn't exist on origin/main — lives only on branch
`stage1-redeploy3-session-2026-05-30` (commit `3088966`). Fresh session
checking out origin/main gets "file not found."

**Fix:** cherry-pick `3088966` to a fresh branch off main, parameterize the
hardcoded 66-file manifest via `--manifest <path-to-json>`, bring forward the
redeploy3 deploy_log entry into main. Estimated ~2-3h.

## P3 — `scripts/prod_vs_main_file_level_md5_sweep.py:124` LF-normalizes binary files

Filed 2026-06-02 via Phase 3 deploy. `local_md5_lf()` unconditionally calls
`data.replace(b"\r\n", b"\n")` before hashing — corrupts PNG/ICO/binary file
hashes. Produces false-positive DIFFER on binaries.

**Fix:** add `is_text_file()` filter (~1 line). ~30 min.

## P3 — pytest 9.0.3 default-abort-on-collection-errors

Filed 2026-06-02 via Phase 3 deploy. Need `--continue-on-collection-errors`
flag for canonical 28/3 baseline gate, OR delete the 3 stale-import test files
that import the removed `bitunix_confluence_gate` module.

Affected files (all import `trading_corp.agents.strategies.bitunix_confluence_gate`):
- `tests/test_backtest_bitunix_confluence_five_factor.py`
- `tests/test_bitunix_confluence_gate.py`
- `tests/test_bitunix_gate_inputs.py`

**Fix:** ~30 min to delete the tests, ~2h to restore the module.

## P3 — `test_paper_run_tooling.py` readiness checks have undocumented `data/trading_corp.db` filesystem dependency

Filed 2026-06-01, refiled with corrected framing during Session B pre-flight.
Tests fail 28/3 in fresh worktrees, pass 26/3 only on machines with prior
DB-init activity. Either: (a) make tests self-contained (init temp DB in
fixture), (b) mark as integration-only, or (c) document the dependency
explicitly.

## P3 — PROD_ONLY anomalies surfaced by Item 5 sweep

Filed 2026-05-31. Three files exist on prod that are NOT git-tracked on
`origin/main` and do NOT match documented `.bak-<label>-<date>` or
`.pre-<label>-<date>` deploy-backup conventions. Item 5 sweep flagged for
review. None block redeploy attempts; cleanup is operator-curated, low-priority.

## P3 — Stage-1 paper-mode dashboard precursor charts

Filed 2026-05-31. Dashboard precursor chart work for Stage 1 paper-mode observability.

## P3 — Discipline: derive deploy windows from `systemctl ExecMainStartTimestamp`

Filed 2026-05-31. Use systemctl for prod-deploy windows rather than
prompt-stated timestamps.

## P3 — Stage-1 BitUnix readiness gaps — low-severity

Filed 2026-05-30. 2 untracked low-severity items from Stage-1 readiness audit.

## P3 — Wider db_url plumbing through risk.evaluate sites

Filed 2026-05-29. Stage-1 N+1 follow-up. Partial coverage post-merge; complete
the plumbing across remaining risk.evaluate call sites.

## P3 — Fidelity Playwright Firefox binary missing → both Fidelity divisions paper-fallback (sharpened from prior "login flakiness" P3 — finding 2026-06-09 via P2 restart)

**Symptom:** Both `fidelity_joint` and `fidelity_401k` divisions fail
broker connect at trading-corp startup; fall back to paper mode.
Their dashboards show $0 equity.

**Concrete cause (verified 2026-06-09 14:49:03 UTC via post-restart
journal):**
- `BrowserType.launch: ENOENT … ms-playwright/firefox-1511/firefox/lock`
- Playwright Firefox binary missing from cache at expected path.
- Triggers `broker_fallback_to_paper` for both Fidelity divisions.

**Sequence:**
1. `fidelity_joint` connect fails first (browser binary missing) →
   paper fallback.
2. `fidelity_401k` sees "Fidelity shared session bootstrap previously
   failed" → paper fallback.

**Fix:**
- `playwright install firefox` on prod VM (likely as azureuser).
- Verify binary lands at expected cache path.
- Restart trading-corp; expect Fidelity divisions to connect cleanly.

**Watch concern:** the binary may have been wiped by an unattended-
upgrades cleanup, OS pruning, or a previous deploy. Confirm root cause
of the missing binary BEFORE just re-installing — re-installing
without understanding the deletion mechanism risks repeat.

**Priority: P3.** Read-only equity monitoring, no real-money execution
surface today. Same priority class as the prior Robinhood P2 session
auth (which has now been RESOLVED 2026-06-09).

**Not gating:** Bitunix observation window, Polymarket development.

**Supersedes:** prior "Fidelity startup login flakiness on trading-corp
restart" P3 entry (which was vague; this finding sharpens to concrete
cause).

## P3 — Wider db_url plumbing for cross-process halt persistence

N+1 follow-up; PARTIAL coverage post-merge.

## P3 — `tasty_options` config block missing from prod's `strategies.yaml`

ANOMALY. Deploy gate path question — see also the committed-but-undeployed
P2 entry above.

## P2 — Reconcile committed-but-undeployed main vs prod divergence

Filed 2026-05-29. `origin/main` has tasty_options + iron_condor wiring
committed but not deployed. Every deploy that touches `secrets.py`/`main.py`
must navigate this drift surgically. 2nd documented occurrence as of
2026-05-29.

**Resolution:** either (a) fire Phase-0 sandbox smoke for tasty_options and
deploy it (closes gap, commits real-money order-placement wiring — needs own
gate); or (b) revert un-deployed commits off main and re-introduce when smoke
passes.

## P3 — Reconciler intrabar TP-vs-advanced-SL path ambiguity (chronic variance, documentation + remediation gap) — filed 2026-06-08 via Thread C investigation

**Finding:** Three flagged trades (c8f25d17, ac5f9c59, c2eb7cda) show
recorded-vs-sim R deltas of -0.418, +0.437, +1.125 respectively.
Root cause is chronic intrabar path ambiguity in the 1m re-walk
reconciler (audit_reality_reconciler.py → _classify_v2_multi_leg),
NOT a regression.

**Mechanism (code-confirmed):**
- paper_trade_replay.py:503-574: SL is checked at bar-start against
  prior current_sl. Advanced (ratcheted) SL after a TP fill is only
  applied via current_sl = new_sl at line 574 → evaluated on the
  next bar.
- When TP fill + advanced-SL-stopout collapse into one 1m bar, the
  sim fills the legs and misses the same-bar advanced-SL exit.
- Bidirectional: c8f25d17 shows the reverse (sim's SL-first walk
  truncates a fill the live path credited).
- Same class as the prior 3m→1m granularity fix (06b5a9e, took
  mismatches 12/17→17/17). These three are the residual sub-1-minute
  tail it cannot reach.

**Source of truth:** Recorded is authoritative ("audit wins" per
CLAUDE.md STOP-AND-READ #2). Sim is a cold re-walk diagnostic.
Paper-mode, no capital at risk.

**Two scope items:**

1. **sharp_edges.md documentation gap.** The original-SL intrabar tie
   case is documented; the advanced-SL case is NOT. Add the
   advanced-SL intrabar reconciler-variance entry for completeness.

2. **Remediation for the 3 flagged trades.** Mark each as
   audit_corrected using the existing audit_corrected /
   corrected_r_multiple mechanism (audit_reality_reconciler.py:189-202).
   This stops the dashboard's RECONCILER MISMATCH tile from showing
   these as persistent INVESTIGATE items. Prod write — operator
   action, not in-session.

**Why filed P3:** chronic, irreducible at 1m granularity, audit is
authoritative, paper-mode only. Diagnostic-tool fidelity rather than
trading-correctness concern. But sharp_edges.md gap erodes future
diagnosis quality; flagged-trade noise erodes future reconciler-tile
signal quality. Worth tracking, not urgent.

**Reference:** Thread C investigation commit 10a8bfd.

**Not gating:** any active development.

## P3 — kalshi_weather tier-1 schema committed but NOT deployed

Filed 2026-05-29. ANOMALY. Same shape as the tasty_options committed-but-undeployed
drift.

## P1 — Polymarket dedupe: per-`condition_id` position cap

Filed 2026-05-21, operator-approved 2026-05-21. Verify ship status — may be
complete.

## P1 — Polymarket clean-data tracker

Filed 2026-05-21. Trades with `entry_ts` before 2026-05-21 12:28:07 UTC are
pre-cap and excluded from the 50-trade floor.

## P2 — bitunix dashboard full 5-panel rebuild

(Cross-referenced from Priority 1 — also listed there.)

## P3 — Replay-loop bar-buffer optimization

Filed 2026-05-11. Nice-to-have.

## P3 — Pink Box S/R confluence integration

Filed 2026-05-10. Mechanical S/R zone integration; related to AlexO market
structure framework Option A (operator-curated S/R zones). See CLAUDE.md skill
references.

## P3 — CLAUDE.md inline § references could be anchored links

Filed 2026-05-16. Convert §-references in CLAUDE.md to anchored markdown
links for easier navigation.

## P3 — `tests/test_webhooks_return_fast.py` 5 failures from `_Deps.bitunix_observer` fixture gap

Filed 2026-05-26. Test cleanup.

## P3 — Copy-trader `equity_history` writer never wired

Filed 2026-05-24. Cleanup.

## P3 — Analyze button has no collapse — toggle the whale-audit panel open/closed

Filed 2026-05-26. UX.

## P2 — Cloudflare-retry burn vs `TimeoutStartSec=3600` on watchlist deep timers

Filed 2026-05-23. Ops.

## P1 (ops/security) — Deferred 43-package upgrade from C-6 lockfile drift

Filed 2026-05-24. 43 deferred package bumps from C-6 lockfile reversal.

## P0 — Crash diagnosis (2026-05-19)

Local Python workstation crashes; partial diagnosis at
`docs/diagnostics/2026-05-19_crash_diagnosis.md`. Mitigation in place
(`scripts\run_capped.ps1` wrapper with 25GB Job Object cap per CLAUDE.md
STOP AND READ #6). Root cause still open.

## BitUnix — post-funding diagnostics (2026-05-21)

Investigative checklist after Bitunix funding. May be partially complete;
verify status.

## P2/P3/P4 — 2026-05-14 deferred items from specialized-agent work

Pre-Phase-3 era items from specialized agent sessions. Review for relevance
at next grooming.

## P2/P3 — 2026-05-16 PM Dashboard hygiene followups

Older dashboard hygiene items. Review for relevance.

## P2/P3 — 2026-05-15 K3 Watch-only follow-ups

Older Kalshi K3 items. Review for relevance.

## P2/P3 — 2026-05-15 Kalshi Weather Tier-2/3 follow-ups

Older Kalshi weather items. Review for relevance.

## P5 — Rename `EngagementSpec.requesting_division` → `requesting_strategy`

Filed 2026-05-02. Naming cleanup.

## P5 — Realignment-memo wording: `would_have_placed` is Otter/Cypher-only, NOT a PMCC signal

Filed 2026-05-02. (Was DONE 2026-05-09 — verify and remove if confirmed done.)

## ⏸ DEFERRED — Phase E: PWA + web push subscription flow

Broken out 2026-05-09 from the HITL approval flow. Web push subscription flow
deferred — not currently in scope.

## ⏸ DEFERRED — Market Cypher: add bear-bias backup if Blood Diamond too rare

Originally P2 — 2026-04-30. Deferred 2026-05-09 with the Cypher disable on
`coinbase_spot`.

## ⏸ DEFERRED — Lord Otter Phase 1.5 (equity-aware sizing + real stops)

Originally P1 — 2026-04-30. Deferred 2026-05-09 with the Otter disable on
`coinbase_spot`. Preserved for potential BitUnix Futures revival.

## P1 — Fidelity broker: read-only + analysis on Azure VM

DEFERRED — 2026-05-03. Was SCOPE-NARROWED 2026-04-30.

## P2 — 5 PMCC scan tests failing on liquidity gate

Filed 2026-04-30. Test cleanup.

## P3 — Polymarket: add `division` column to `polymarket_round_trips`

(Cross-referenced from Priority 2 — also listed there.)

## P3 — Fidelity options ticket flow (deferred autonomous execution)

Filed 2026-04-30. Long-term ticket-flow design.

## P3 — Differentiate "expected" vs "real" `broker_fallback_to_paper` audit rows

Filed 2026-05-01. Audit-row cleanup.

## ✅ P2 RESOLVED (2026-06-09) — Robinhood session auth dead since ~2026-05-29; PMCC/IRA/joint reading $0 (filed 2026-06-08 via Thread B investigation)

**Symptom:** Robinhood session pickle returning 401 Unauthorized for
PMCC, IRA, and joint account reads. Affected division dashboards
show $0 equity — masking actual positions and P&L. Discovered
2026-06-08 during Thread B investigation of the original "Robinhood
pickle reset" concern. Was NOT a system restart — service has been
stable since 2026-06-02 deploy (MainPID 2043009, NRestarts=0).

**Impact:**
- PMCC / IRA / joint dashboards have been blind for ~10 days.
- No capital at risk (paper-mode exec; reads-only side affected).
- Dashboard observability for those divisions cannot be trusted
  until session re-auth.

**Fix:** operator interactive re-login to regenerate Robinhood
session pickle. Requires MFA — cannot be agent-resolved. ~5 min
operator action.

**✅ RESOLVED 2026-06-09 ~14:49 UTC.** Operator-supervised path-(b) re-login
(backup + clear + fresh `rs.login(store_session=True)`); one device push
approved → fresh pickle (mtime 2026-06-09 14:45Z, was 2026-05-29 01:59Z).
`sudo systemctl restart trading-corp` at 14:48:58Z (MainPID 2397472 →
2427161) loaded it: RobinhoodBroker logged in (jrsumner@yahoo.com), 3
accounts bound (individual/ira_traditional/joint), no RH 401, no
device-approval hang; PMCC reading 11 real legs; PMCC/IRA/joint dashboards
confirmed real equity (operator C3). Method + sequence in
`runbooks/deploy_log.md` 2026-06-09 follow-up. Session: 2026-06-09
operator-supervised P2 re-login.

**Reference:** Thread B investigation commit a78eff7. See also
"## P3 — Fidelity startup login flakiness on `trading-corp` restart"
elsewhere in this file (related class of broker session-cache
fragility).

**Not gating:** any active development. PMCC / IRA / joint are
read-only divisions in paper-mode.

## P2 — Robinhood pickle stale-state on restart silently drops `robinhood_pead` to paper (filed 2026-07-01 via Kalshi K5 INERT deploy)

**Symptom / risk.** `robinhood_pead` is a LIVE division. Every `trading-corp` restart re-auths
Robinhood from the on-disk session pickle; when that pickle is stale/expired the boot either **hangs
on a device-approval challenge** (~3-min timeout, sometimes indefinitely) or falls through to
`broker_fallback_to_paper` — so a restart done for an unrelated reason (e.g. the K5 INERT deploy,
2026-07-01) can leave the live PEAD division silently **paper** until an operator notices. Flagged as
a heads-up during the 2026-07-01 Kalshi INERT deploy (operator cleared/refreshed the pickle by hand
that time — same manual step as the 2026-06-09 re-login; see `deploy_log.md`).

**Ask (either is acceptable).**
1. **Document** the pickle clear/refresh as an explicit pre-restart step in the restart runbook (and
   in the K5 live-flip RUNBOOK, whose restart bounces PEAD), so it's never left to memory; OR
2. **Add a startup guard** that, when the RH pickle is missing/expired at boot, emits a loud
   Telegram/audit alert (and does NOT silently paper-fall-through the live PEAD division without
   surfacing it) — turning a silent degrade into an observable one.

Related class: the resolved 2026-06-09 RH session-auth item above + `## P3 — Fidelity startup login
flakiness on trading-corp restart` (broker session-cache fragility on restart) + `## P3 —
Differentiate "expected" vs "real" broker_fallback_to_paper audit rows` (which this would feed).
Operator currently owns the RH pickle manually. **P2** because it can silently un-arm a live
money division on any restart.

## P3 (LOW) — PEAD signal-funnel observability: populate `scan_evaluation` (filed 2026-07-10 via PEP first-live signal-side verification)

**Problem.** `scan_evaluation` is 0 rows (DARK) on prod. Now that `robinhood_pead` trades real
money, a live entry's signal-side reasoning is not auditable after the fact — we can confirm HOW a
trade executed (RH-authoritative round-trip) but not WHY that name was selected over others. On the
2026-07-10 PEP entry we could confirm universe=3,207, PEP SUE=2.202, entered=1, but NOT the wave size
(the top-quintile denominator) nor how many names cleared each gate.

**Fix (forward only).** Persist the per-scan funnel each run into `scan_evaluation`: universe count →
screened (screen_ok) → SUE>1.5 → top-quintile → entered, with per-name SUEs, the wave size, and the
quintile cutoff. Gives every live entry a provable signal trail. Note the structural finding from the
PEP verification: the wave's max-SUE name is never gated by top-quintile (cutoff ≤ max for any n; n=1
→ cutoff = own SUE), so **persisting the wave size is what makes "was the quintile gate binding?"
answerable per entry.**

**Constraints.**
- `.py` change (`pead_strategy.py` scan() write-path) → **restart-gated**. BATCH into ONE coordinated
  restart with the other deferred PEAD/dashboard display fixes (paper-vs-live dashboard rendering,
  homepage crypto-count card, `/telemetry/pead` UI tile-repoint verification) — do NOT bounce the
  live engine piecemeal per fix. Pre-restart, refresh the RH pickle (see the P2 pickle item above +
  [[prod-restart-rh-pickle-hazard]]) so the restart doesn't silently drop PEAD to paper or hang.
- **DO NOT reconstruct past waves** by re-running `get_quarterly_eps`+screen across the 3,207-name
  universe — that is the synchronous blocking sweep that caused the 6/26 ~51-min event-loop freeze.
  Fix forward only (new scans persist; historical waves stay unrecoverable).

**Priority: LOW.** Not blocking — PEAD is live and working as designed (2026-07-10 PEP entry verified
end-to-end, RH==DB exact). This is auditability, not a defect. Evidence:
`Desktop\bitunix_reports\2026-07-10_pead_PEP_signal_side_verification.md`; memory
`pead-first-live-entry-pep-2026-07-10`.

## P3 — Robinhood IRA drilldown: not a LEAP / PMCC strategy

Filed 2026-05-03. UX clarification.

## P3 — Robinhood Agentic Trading: revisit integration (DEFERRED 2026-06-08)

Robinhood launched Agentic Trading (beta) 2026-05-27 via MCP server
at `agent.robinhood.com/mcp/trading`. Per planning report
`reports/2026-06-08_robinhood_agentic_evaluation.md`: deferred
formal integration; Pattern 1 (broker adapter under existing risk
gate) is the only shape that preserves single-chokepoint invariant,
blocked today on:
- No documented non-interactive / service-account auth path
- trading_corp is not an MCP client today (no MCP client library)

Revisit triggers (any one is sufficient to re-evaluate):
- (a) Documented programmatic / service-account auth path lands
- (b) GA out of beta (currently 2 weeks old)
- (c) Options/crypto trading-execution support, including
  instrument-chain discovery primitives (get_option_instruments
  or equivalent) — watchlist-only options surface already exists
  and must NOT false-trigger a revisit
- (d) Published rate limits / SLA
- (e) Observed beta stability over 3+ months
- (f) Operator capacity for new-division build
- (g) Trailing-stop order type or equivalent ratcheting
  primitive (soft — eliminates adapter friction for
  trading_corp stop-management, e.g. Bitunix advanced-SL;
  ratcheting otherwise achievable via cancel+replace, so not
  strictly required for initial Pattern 1)

The most load-bearing trigger is (a) — without service-account auth,
Pattern 1 is infeasible regardless of other improvements.

Pattern 3 (operator-driven manual exploration via Claude Desktop)
remains available as a low-cost surface-familiarity option; not
filed as a BACKLOG task since it's an operator-driven action, not a
session work item.

**UPDATE 2026-06-09 — Pattern 3 exploration COMPLETED; verdict
UNCHANGED (defer).** Operator-driven manual exploration via Claude
Desktop empirically validated the Defer verdict and sharpened
triggers (c)/(g) above. The 22-tool surface confirmed equities-only
trading (no place_option_order, no instrument-chain discovery), an
interactive desktop-OAuth-only auth path (trigger (a) unmoved), and
a two-step review_equity_order -> place_equity_order write flow.
Material new finding (not in the 2026-06-08 eval): **read-vs-write
scope asymmetry** — the MCP token grants full cross-account READ
visibility (7 accounts incl. IRA / advisory-managed) despite
write-isolation to the agentic sub-account; any future Pattern-1
read-adapter requires explicit Board acknowledgment of the
data-exposure scope (CLAUDE.md §4). Captured in auto-memory
`2026-06-08-robinhood-agentic-evaluated-deferred.md`.

References:
- reports/2026-06-08_robinhood_agentic_evaluation.md (original eval)
- reports/2026-06-09_robinhood_agentic_pattern_3_exploration.md
  (Pattern 3 exploration; branch
  robinhood-agentic-pattern-3-exploration-2026-06-09, unmerged,
  pushed to origin)

## P3 — Migrate `FidelityBroker` onto a `ReadOnlyBroker` ABC

Filed 2026-05-01. Architecture cleanup. See CLAUDE.md §1 code-path-isolation
section for the ReadOnlyBroker ABC pattern.

## P3 — Cost-optimize tc-prod-vm away from Standard_D2s_v3

REVISED — 2026-05-02. Cost-optimization investigation.

## P2 — Cloudflare Tunnel with named domain

Network infrastructure.

## P3 — Authentication (Sign in with Apple)

Long-term auth roadmap item. May be partially obsoleted by Authelia work.

## P4 — Hetzner deployment

Alternative deploy target investigation.

## P4 — Research firm: minimum-coverage quorum gate for TradeConfirmation

Filed 2026-05-01. Research-firm scope item.

## P4 — Investigate: PMCC scout fired at 04:03 UTC outside the 8:30-9:25 ET scheduler window

Filed 2026-05-02. Anomaly investigation.

## P4 — Logging: RedactingFilter mangles dict args in %-style log calls

Filed 2026-05-02. Logging cleanup.

## P5 — Mobile-responsive layout audit

Long-term UX.

## P6 — Real macro calendar fetcher

Long-term data integration.

## P7 — Crypto-friendly stock holdings display

Long-term UX.

## P8 — JSON API endpoints (`/api/v1/*`)

Long-term API surface.

---

# Items consciously excluded

- Multi-region active-active deploy — overkill for personal trading.
- Kubernetes — overkill, single VPS is right.
- Pure-native iOS app — PWA is sufficient.
- Reverse-engineering Lord Otter's signals — defeats paying for it.

---

_Last grooming pass: 2026-06-02 evening. Previous file: 8,881 lines (62 EOS
snapshots + 44+ DONE/SUPERSEDED entries archived to deploy_log + memory).
Current file: ~470 lines organized around three operator priorities
(Bitunix live-readiness, Polymarket Copy live-readiness, InfoSec) + other
open items._

_Convention: completed work moves to `runbooks/deploy_log.md` + memory entries.
This file tracks open items only. EOS snapshots and DONE entries do NOT
accumulate here._

## PZ (low) — `KalshiBroker.quote()` / orderbook field-name mismatch — OPEN (filed 2026-06-30)

`brokers/kalshi.py` `quote()` (and `_best_price`) read `ob.yes_bids` / `ob.yes_asks` off the
pykalshi orderbook, but pykalshi 1.0.6's `OrderbookResponse` exposes **`orderbook.yes_dollars` /
`orderbook.no_dollars`** (arrays of `(price_str, size_str)`) plus the computed properties
**`best_yes_ask` / `best_yes_bid` / `mid`**. So `quote()` returns **0.0** for every real market
(confirmed on demo `KXALIENS-27-29`: real book present, `quote()` -> 0.0). Fix: read
`ob.best_yes_ask`/`best_yes_bid`/`mid` (or parse `yes_dollars`/`no_dollars`; note NO-side asks come
from `no_dollars` via the `1 - price` complement).

**Non-blocking for K5 go-live** — the live order path uses `ProposedOrder.limit_price`, not
`quote()`; and `kalshi_copy_trader._emit_exit` falls back gracefully when quote is 0 (records a
0-PnL round-trip). **Must fix before any future Kalshi-quote-dependent analysis** (exit
mark-to-market, mid-based sizing, dashboards). Low priority. Surfaced during the K5.1b demo
validation (2026-06-30).

## P3 — kalshi_copy_trader phantom position-cache: paper-era `our_side` holdings emit one-time exit-residual noise on live (filed 2026-07-01 via K5 go-live)

At the 2026-07-01 live flip, the `agent_state kalshi_copy_trader positions:*` cache carried months of
**PAPER-era phantom holdings** (`our_side`/`copy_size_usd`/`entry_price` set on positions never
actually held live). Now that the division is LIVE, when a whale **closes** such a cached position the
bot places a `reduce_only` sell to mirror the exit → nothing to reduce → `exit_no_fill` →
`kalshi_copy_exit_residual` audit + Telegram, then the cache self-clears (verified: `positions:pritz786
→ {}` after the first one; won't repeat per position). **$0-risk** (`reduce_only` cannot create a
position) and **self-healing** (drains as the phantom positions close; real copies accrue from new
entries). The only cost is **residual-flag Telegram noise** during the drain.

**Do NOT "fix" by clearing/zeroing the whole cache** — that would make every whale's *current* holding
look brand-new → **live mass-entry burst**. If the noise is worth suppressing: (1) FIRST read the
entry-trigger logic in `agents/strategies/kalshi_copy_trader.py` and confirm entries fire on
**event/`first_seen`** (new position appearing), NOT on state (`whale holds & we don't`); (2) only then
surgically zero `our_side`/`copy_size_usd`/`entry_price` on the phantom cached positions **while
keeping the position keys** (so holdings stay "seen" and don't re-enter). Until verified, **leave it** —
it converges on its own within days. Low priority (cosmetic/operational, not a money or safety issue).

## P3 — kalshi_copy_trader cheap-contract sizing precision: worst-case spend ~2× the tier $ on sub-2¢ contracts (filed 2026-07-01 via first-live-copy review)

Copy contract count is `usd_to_contracts = floor(copy_usd / no_leg_price)` sized at the whale's
outcome-leg price, but the live order carries the fixed **2¢ slip ceiling** (`max_slippage_cents=2`).
On a sub-2¢ contract, 2¢ of slip is a huge % move, so the actual fill can cost up to
`count × (base_price + $0.02)` ≈ **~2× the intended tier dollars** (e.g. a $3 copy of a $0.018 NO =
166 contracts; worst-case fill at $0.038 = ~$6.3). Observed live 2026-07-01: the BTC-15m NO copy
actually filled *cheaper* ($0.013 < $0.018 base) so no overspend occurred — but the precision is
coarse and the ceiling is one-sided on cheap legs. Bounded (~$6 worst case at current $1-3 tiers), so
**NOT blocking** — count/sizing logic itself is correct (the 166 count was $3 of a 1.8¢ contract, not
a bug; only the fill-price *recording* was wrong, fixed in the 2026-07-01 copy fixes).

**Options (operator to choose when prioritized):** (1) a **percentage-based slip cap** on sub-2¢
contracts (e.g. `min(2¢, N% of price)`) so the % move is bounded; or (2) a **hard sub-2¢ skip** (don't
copy contracts priced below ~2¢, where slip precision is unavoidable). Low priority. Surfaced during
the first-live-copy accounting review (the same review that produced the NO-leg / recording / filter
fixes on branch `kalshi-copy-recording-shortfilter-2026-07-01`).

## P3 — kalshi_copy_trader mass-disappearance guard false-fires on high-churn whales (filed 2026-07-01 via pritz786 feed-anomaly)

The K5·4 feed-health / mass-exit guard (`_is_mass_disappearance` in
`agents/strategies/kalshi_copy_trader.py`) fires `kalshi_copy_feed_anomaly reason=mass_disappearance`
when a high % of a whale's previously-tracked positions vanish between polls — designed to catch a
BROKEN feed (scraper returned empty). But a whale trading **ultra-short markets** (pritz786: 15-min BTC,
live cricket) legitimately turns over **100% of positions between 10-min polls** (they all resolve +
reopen), which is indistinguishable from a feed break to the current logic → it fired every poll →
**Telegram spam** (2026-07-01, feed was HEALTHY at 17–22 rows). Workaround applied: pritz786 dropped
from `selected_whales` (agent_state; prod-only). The ultra-short FILTER does NOT fix this — the guard
tracks the WHALE's positions regardless of whether we copy.

**Fix:** only trip the guard on a genuinely broken feed — e.g. gate on the TOTAL feed row count being
~0 (all whales empty), or verify the "removed" positions didn't actually resolve (cross-check
`get_market_resolution`), rather than firing on one whale's legit 100% churn. Consider a per-whale
churn baseline. Low priority (workaround holds; benign — guard abstains, no money at risk).

## P3 — kalshi copy dashboard Paper/Live/All toggle: v1 scoping gaps (filed 2026-07-01 via toggle build)

The `wr_mode` toggle (deployed 2026-07-01, `main` `9218997`) scopes the summary stats + round-trip
history + open LIST, but two surfaces were left unscoped for a surgical v1:
1. **Open *tile count*** (`_query_pm_pending_count`) + **equity curve** (`_query_pm_equity_curve`) are
   NOT mode-scoped — so in Paper/Live mode those two show all-time. No visible mismatch at launch
   (go-live just happened → pending rows are post-epoch anyway); thread `kalshi_copy_mode`/
   `kalshi_copy_epoch` into both to close it.
2. **Sort/filter controls don't carry `wr_mode`** — clicking a Kalshi sort/filter after choosing a mode
   resets scope to the default (LIVE). Add `wr_mode` to those link/query-string builders in
   `pm_dashboard_body.html` for full cross-control persistence.
Low priority (cosmetic; the toggle itself works + preserves other params).

---

## P3 — Kalshi arb divisions: fee-model + forward-track (filed 2026-07-07)

Session 2026-07-07 assessed **Kalshi LLM Arbitrage** (`kalshi_llm_arbitrage`) + **Kalshi
Arbitrage** (`kalshi_arbitrage`) for a live edge — **neither has one; both stay paper/standby.**
Deployed paper-refinements (LLM discovery → Economics+Elections; resolver `leg_date` starvation
fix; temporal/bucket 60-day horizon caps) + backlog cleanup. Full detail:
`runbooks/deploy_log.md` 2026-07-07 + memory `kalshi-arb-divisions-2026-07-07`. Open:

1. **★ Add Kalshi fees to `kalshi_resolver.py` P&L.** Paper P&L is GROSS — the Kalshi taker fee
   (`ceil(0.07·p·(1-p)·qty)` per fill) is not modeled, so every `kalshi_round_trips`/dashboard
   number overstates net. This gates ANY live-edge judgment on ANY Kalshi division (llm, arb,
   weather, crypto, copy). Highest-value follow-up.
2. **Monitor un-blinded `kalshi_arbitrage` NET.** The resolver fix un-blinded temporal arb
   (0 → 50+ round-trips, draining ~50/hr). The first-tick +$674 gross is a ONE-SIDED partial
   (early legs only; offsetting late legs + fees pending) — compute true net (both legs,
   post-fee) after a few days' drain before believing any temporal edge.
3. **LLM Economics+Elections forward test.** The narrowed slice is the only remaining LLM thread;
   if it is net-positive AFTER fees out-of-sample over the coming weeks, only THEN discuss
   auto_execute — which needs the built-but-unmerged `KalshiLiveBroker`
   (`kalshi-k5-golive-2026-06-30`) wired in + an auto_execute branch.

Priority P3: all paper, no live money, no urgency. #1 (fees) is the prerequisite for everything
downstream.

---

# Research / Future

## LLM Shadow-Logger — BUILT + DEPLOYED LIVE 2026-07-13/15 (log-now phase DONE; the OPEN item is now evaluate-later)

**★DELIVERED** as the LLM half of the Market-Context Recorder — the exact isolated-side-process
architecture this item proposed. See `runbooks/deploy_log.md` (2026-07-12 recorder LIVE + D1/D2/D3 entries)
+ memory `market-context-recorder-phase0-2026-07-12`. What shipped:
- **D1 `llm_shadow` stream** — on each SFP construct FIRE, a BLIND (outcome-free) LLM review: regime
  AGREE/DISAGREE vs the detector's live gate (rd/ps_trail30/ema200) + setup-grade 0-100 + caution flag,
  temp 0, FIXED versioned prompt (`shadow_v1`), model + prompt_version logged per row. Nightly
  outcome-backfill pairs it to booked R. Written as `context_kv` rows (stream='llm_shadow') in the
  recorder's OWN `market_context.db` — NOT a new engine table (stronger isolation; engine untouched).
  Anthropic key via the recorder's `_kv_secret` Key Vault path (engine-import-free). VALIDATE-not-gate:
  logs a challenger, gates nothing. First live fire (XRP 2026-07-13) verified end-to-end (blind grade 28
  + non-mechanical reasoning). ⚠ scoped to construct FIRES (not every regime classification, per the
  original "each fire" option — regime-classification logging would be a later extension if wanted).
- **D2 `llm_news`** (daily sentiment) + **D3 `/sfp/llm` dashboard** also live.

**OPEN — the evaluate-later phase (the original "test it in ~3-6 months" step):** the paired
(LLM-judgment → booked R) dataset is now ACCUMULATING (~3 SFP fires/wk). Do NOT evaluate on small n. When
**n≥~30 paired rows** (~3-6 mo): does the LLM beat the detector on the DISAGREEMENT set (when they differ,
who's right on forward R)? Does the setup-grade predict booked R? Is it CONSISTENT at temp 0
(answer-variance on the same setup = a disqualifying data point)? Score with the standard drift-null
discipline. The `/sfp/llm` dashboard already gates its verdict at n≥30. Skeptic priors still hold: the LLM
sees the same bars — it may just re-derive the detector (the 2-trade feel-test hinted it CAN add a
non-mechanical read, but n=2 proved nothing). **Graduation path unchanged:** shadow-log → (if signal) a
logged funnel stage → (only then, maybe) a live gate. Never straight to a gate.