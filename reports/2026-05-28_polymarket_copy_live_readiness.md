# Polymarket Copy-Trader — Live-Money Readiness Audit

**Date:** 2026-05-28
**Scope:** What it would take to run **one** division, `polymarket_copy_trader` (PCT), LIVE with real
money while every other division stays in paper mode.
**Status:** Scoping only — COMPLETE and SHELVED. This audit does **not** decide to go live and changes nothing.

**Explicitly out of scope / untouched:**
- The 2026-05-31 whale-scoring verification gate (separate from the paper→live decision).
- The C-1 `POLYMARKET-PRIVATE-KEY` rotation track (parked; its intersection with wallet work is noted, not acted on).

**Method:** 3 parallel read-only Explore passes, then direct source verification of every load-bearing
claim (`main.py`, `risk.py`, `polymarket_copy_trader.py`, `brokers/polymarket.py`, `agents/data_exec.py`,
`utils/secrets.py`, config YAMLs), plus a dedicated **reuse audit** of the order-placement stack covering
py-clob-client, the py-sdk replacement, MCP config, prior prototypes, and in-repo signing code. Every
file:line below was confirmed in source.

---

## Headline

Going live with PCT is a **multi-week Phase-3 build, not a config flip.** The order-placement path does
not exist (broker is read-only; the loop ends at `would_have_placed` with no `auto_execute` branch). The
order-signing core (item 8) is **not greenfield**, though: a proven pinned client and MIT-licensed
reference implementations exist to adapt — see Note C.

---

## ⚠ NOTE A — Two of these are live PAPER-MODE BUGS today, not future-live problems

Real **right now**, worth fixing on their own merit independent of any go-live conversation. Items **#1
and #2** of the prioritized list; land independently of all live work.

1. **PCT's Polymarket risk caps are phantom.** The gate routes Polymarket caps ($250 single-market, $1k
   daily aggregate, 5%/position, 5–95% implied-prob) on the `is_prediction_market` flag in `order.extra`
   (`risk.py:134`). **PCT orders never set that flag** (`polymarket_copy_trader.py:429-446` entry,
   `:475-489` exit) — so `_evaluate_polymarket` is never invoked for PCT; orders fall through to the
   generic `per_trade_risk_pct` backstop. At fixed $1/$2/$5 sizing it passes, so the caps look present but
   enforce nothing. The gate's own comment (`risk.py:127-133`) says it was built for `copy_trading` to
   reuse — it was simply never wired.
2. **The open-aggregate cap is OFF system-wide.** `_sum_polymarket_open()` is a hard `return 0.0` stub
   (`risk.py:434`); the $1k open-notional cap enforces nothing **for every Polymarket division, including
   the real-broker `polymarket_arbitrage`.** Separately, `_sum_polymarket_today()` hardcodes
   `actor='polymarket_arbitrage'` (`risk.py:407`), so the daily-aggregate cap would not count PCT rows even
   after fix #1.

---

## ⚠ NOTE B — Wallet-isolation decision is RESOLVED: dedicated EOA per Polymarket division

**Decision (operator, 2026-05-28):** every Polymarket trading division gets its **own EOA wallet** —
separate signer key, funder address, USDC balance, nonce sequence; no shared blast radius. The
shared-wallet option is **rejected** and not scoped as an alternative.

Build it as a **generalized per-Polymarket-division wallet pattern, not a one-off for PCT:**
- Per-division KV secret paths (e.g. `POLYMARKET_COPY_PRIVATE_KEY` / `POLYMARKET_COPY_FUNDER_ADDRESS`,
  distinct from `polymarket_arbitrage`'s `POLYMARKET_PRIVATE_KEY` / `POLYMARKET_FUNDER_ADDRESS`).
- Per-division `Secrets` fields (today one set, `utils/secrets.py:116-117`).
- Per-division broker instances keyed off division in the factory (today one shared instance,
  `main.py:~1794`).

**`polymarket_arbitrage`'s single-wallet path migrates onto the same pattern as part of this work
(item 6 scope, explicitly — not a side effect),** so the architecture is uniform across all Polymarket
divisions from day one. Per-wallet provisioning consequences (each EOA needs its own USDC→CTF approval and
its own MATIC reserve/monitoring) are folded into items #4/#5/#6.

---

## ⚠ NOTE C — Order-signing client: a proven-but-archived path vs an official-but-beta path

The reuse audit (extended to the external ecosystem) found real, adaptable reference material — item 8 is
**not** greenfield. [Correction: an earlier draft of this note called `py-clob-client` "dead / no longer
functional." That quoted the GitHub repo banner literally and was wrong in practice — the pinned package is
what the ecosystem actually runs.]

- **`py_clob_client` is archived as a GitHub repo but the pinned package works and is the de-facto
  production client.** Polymarket's OWN official agents framework (`Polymarket/agents`, MIT, repo also
  archived May 2026) pins `py_clob_client==0.17.5` + `py_order_utils==0.3.2` + `web3==6.11.0` +
  `eth-account==0.13.1` and uses it to build/sign/place CLOB orders (`Polymarket.py`, `trade.py`). The
  `harish-garg/Awesome-Polymarket-Tools` list still presents py-clob-client as THE official Python CLOB
  client with **no maintained alternative listed**. Entry points: `ClobClient(host,key,chain_id,
  signature_type,funder)` → `create_order(OrderArgs)` / `create_market_order(MarketOrderArgs)` →
  `post_order(signed, OrderType)`, EIP-712 signing internal. "Archived" = repo read-only, **not** "pip fails."
- **The official forward path is beta.** `polymarket-client` (py-sdk) is Polymarket's stated migration
  target, but it's beta (install needs `--pre`), "working toward a stable public API," with the order /
  signing / allowance API **undocumented at README level**.
- **MIT reference signing/placement code exists to adapt:** `Polymarket/agents` (official, MIT) and
  `warproxxx/poly-maker` (production MM, MIT, places real orders both sides). Copy-trading-specific
  references exist too (`polymarket-copy-trading-bot`, `polymarket-trade-copier` from the Awesome list —
  confirm license/lang in the spike). **Exclude `Drakkar-Software/OctoBot-Prediction-Market`: GPL-3.0
  (copyleft, incompatible with lifting into this proprietary codebase) and its copy-trading is WIP.**
- **No shortcut on the rest:** no Polymarket **MCP** (empty `mcpServers` in every `~/.claude.json` profile,
  no `.mcp.json`, nothing in memory/runbooks/reports); **no in-repo signing code** — `polymarket.py`
  deliberately uses raw httpx JSON-RPC for the read-only `balanceOf` eth_call and hardcodes the selector to
  *avoid* eth-utils (`:67-68`); no `web3`/`eth-account` in requirements yet.

**Implication:** item 8 has genuine reuse. The spike (8a) is now a **path decision**, not a from-scratch eval:
- **Path A** — pin `py_clob_client==0.17.5` (+ py_order_utils/web3/eth-account) and adapt the MIT reference
  signing flow into our `Broker` interface. Faster, proven; cost = an EOL/archived dependency (no upstream
  fixes; server-side CLOB API drift is the risk).
- **Path B** — adopt beta `polymarket-client`. Official-future; cost = beta instability + undocumented order
  API + upgrade churn.

This **lowers item 8 toward M–L** (adapt MIT reference vs implement EIP-712 from scratch) and shrinks the spike.

**Operator lean for revisit:** default to **Path A** (pin `py_clob_client==0.17.5` + adapt MIT refs) —
"archived but proven/pinned, used by Polymarket's own framework" beats "beta with undocumented order API +
upgrade churn" for real-money production code. NOT locked: the 8a spike still runs (ecosystem status may
shift by revisit), but it starts from Path A as the working hypothesis, not an open A-vs-B question.

---

## Per-capability verdicts

### 1. Per-division mode flag — **MISSING**
Mode is one process-wide string: `main.py:171` sets `mode` from `--live`; `--brokers` (`:147-149`) selects
live broker *families*; live-ness is per **family**, not per strategy (`:1710`). PCT's division is
`broker: paper` + `standby: true` (`divisions.yaml:169-171, 166`); the comment states the copy_trading
paper broker "won't be reached by any strategy" (`:156-158`). The loop (`main.py:3079-3237`) never receives
`mode` and never branches on `auto_execute` — it ends at `would_have_placed` (`:3206`). **Gap:** flip
broker selection + `standby:false`; pass a live/dry decision into the loop; add an `auto_execute`-gated
placement branch. Per-strategy live-while-others-paper *is* achievable (factory keys off division).

### 2. Live broker adapter — **MISSING (but adaptable references exist — Note C)**
`PolymarketBroker` subclasses `ReadOnlyBroker` (`brokers/polymarket.py:50`); no `place_order`, by design
(`:3-7`); `private_key` stored unused (`:31-32`). No in-repo signing code. The signing/posting flow has
adaptable references: the proven pinned `py_clob_client==0.17.5` used by the MIT `Polymarket/agents`
framework (Note C), or the beta `polymarket-client`. The `Broker` interface is small and known
(`tastytrade.py:252`: `async def place_order(order) -> FillEvent`), and `data_exec.place()` handles the
downstream fill/audit plumbing. **Gap:** run the client-path spike (8a), then build
`PolymarketLiveBroker(Broker)` by adapting the chosen client's signing+post flow, and wire the loop's
`auto_execute` branch through `data_exec.place()`.

### 3. Capital isolation / wallet — **EXISTS (single shared EOA) → RESOLVED to per-division (Note B)**
One credential set system-wide (`utils/secrets.py:116-117` + `polygon_rpc_url`), one KV, one broker
instance (`main.py:~1794`) used today only by `polymarket_arbitrage`. EOA == signer == funder
(`polymarket.py:20-24`). **Gap → Note B + item 6.**

### 4. Risk gates — **PARTIAL (structure correct; inert for PCT)**
Caps exist and fire **before** any broker call (`_evaluate_polymarket` at `risk.py:140`; `RiskAgent.evaluate`
precedes placement). Configured in `config/risk.yaml` polymarket section. **But** routed on a flag PCT never
sets (Note A #1), `_sum_polymarket_open` is a stub (Note A #2), `_sum_polymarket_today` filters the wrong
actor (`risk.py:407`), no max-open-positions count. **Gap:** set the flag (+ `implied_prob_at_entry`); add
PCT to the daily-sum actor filter; implement `_sum_polymarket_open`; add a position-count cap.

### 5. Credential plumbing — **EXISTS (KV complete; key unused; no live-preflight)**
Full chain present: `KEY_VAULT_URI` in systemd → `load_secrets()` (`utils/secrets.py:258`) → KV pull
(`:220-222`) → `Secrets` fields → redaction (`:317-321`). Broker factory passes the key in (`main.py:~1794`),
unused in Phase 1. **`assert_live_ready()` (`secrets.py:335`) has robinhood/coinbase/fidelity/tastytrade
branches (`:343-352`) but NO polymarket branch** — a missing key wouldn't abort a LIVE start; the broker
silently inits in stub mode. **C-1 intersection:** live work lands on the same KV secret family C-1 rotates;
build/test on new per-division keys in staging, then rotate under C-1 protocol. **Gap:** add a
`"polymarket"` branch (per-division key+funder).

### 6. Audit differentiation — **MISSING**
`audit_event` has no `mode` column; mode is implicit in the `kind` name. Live fills emit `kind="filled"`
(`data_exec.py:157`) — also no `mode`. The resolver scans `would_have_placed` only. **Gap:** add explicit
`mode: paper|live` to `would_have_placed` + `filled` payloads (and the synthetic close at
`polymarket_copy_trader.py:724`).

### 7. Dashboard / metrics — **PARTIAL**
Division-level filtering + a redeploy-free `metrics_epoch` (operator-set `agent_state` timestamp) exist
across the 6 PM query helpers + equity curve (`web/data.py`). No `mode` field on read-side dataclasses.
Read queries scan `kind='would_have_placed'` only → live `filled` rows render as empty Open/History. Equity
curve reads broker snapshots, so live on-chain USDC appears automatically. **Gap:** extend open-trades +
resolver queries to include `filled`; optional mode column for split views.

### 8. Position reconciliation — **MISSING**
`_fetch_positions()` exists (`brokers/polymarket.py:~699`) and `snapshot()` calls it, but nothing
cross-checks it against PCT's `our_positions`. The bitunix `audit_reality_reconciler.py` is bar-replay —
structurally incompatible with on-chain settlement. **Field-mapping caveat:** positions response shape never
verified against a funded wallet (`polymarket.py:34-39`). **Gap:** startup + periodic reconcile; verify the
response shape on first funded snapshot.

### 9. Restart / crash recovery — **PARTIAL**
Normal restarts survive via durable `agent_state`. No broker-truth recovery on startup — only
`coinbase_btc_donchian` does that (`main.py:461-498`, the pattern to copy). On crash-before-persist/state
clear, PCT resets `our_positions={}` and orphans on-chain positions; the `last_seen_txhashes` dedup window
is lost → possible double-emit. **Gap:** broker-truth recovery on startup + duplicate-order guard.

### 10. Kill mechanism — **PARTIAL**
Per-whale demote is runtime-togglable, no restart (`web/routes.py:~2115`), reloaded each 60s. Whole-strategy
halt via `enabled: false` is hot-reloaded (`polymarket_copy_trader.py:141-144`) — no restart, but needs
file/SSH access. No one-click web pause; `StrategyState.halted` is always `False` for PCT (`main.py:3147`).
**Gap:** strategy-level pause control (endpoint / persisted `halted`).

### 11. Pre-cutover dry-run — **PARTIAL**
Process `--live --dry-run` short-circuits `data_exec.place()` (`data_exec.py:90`), emitting `dry_run_skip`
(`:131`) instead of `broker.place_order()` (`:150`). **But the PCT loop bypasses `data_exec.place()`, so
`--dry-run` has no effect on PCT today.** No Polymarket CLOB sandbox/validate-only endpoint (unlike
tastytrade `is_test`). Smallest tier is $1 USDC. **Gap:** route the loop through `data_exec.place()` +
optional `shakedown_size_usdc` override.

---

## Reuse audit — build-new vs already-exists (the trap check)

| Asset hoped-for | Reality | Effect on estimate |
|---|---|---|
| `py_clob_client` (official client) | Repo **archived/read-only**, but pinned `==0.17.5` **works** + is used by Polymarket's own MIT `agents` framework | **Path A** for item 8; proven, EOL-dependency risk |
| `polymarket-client` / py-sdk (replacement) | **Beta**, undocumented order API, unstable public API | **Path B** for item 8; official-future |
| MIT reference signing code | `Polymarket/agents` (MIT) + `warproxxx/poly-maker` (MIT) build/sign/place real orders | **Adaptable** — lowers item 8 to M–L |
| Copy-trading-specific bots | `polymarket-copy-trading-bot`, `polymarket-trade-copier` (Awesome list; license/lang TBD) | Candidate refs for the spike |
| `OctoBot-Prediction-Market` | **GPL-3.0** (copyleft) + copy-trading WIP | **Excluded** — license-incompatible |
| Polymarket **MCP** with order tools | **None configured** anywhere | No shortcut |
| In-repo **EIP-712 / signing** code | **None** (read path avoids eth-utils, `polymarket.py:67-68`) | Adapt external MIT ref, don't write from scratch |
| `data_exec.place()` execution plumbing | **Exists + reused** across 6+ HITL call sites (`ceo_graph.py:496`, `webhooks.py:727/990`, `telegram_commands.py:499`, `routes.py:990/1188/1449`); handles dry-run + `filled` audit | **Lowers items 9 & 11** to low-end M |
| `Broker.place_order` interface shape | **Known** (`tastytrade.py:252` → `FillEvent`) | Trivial interface reuse |

**Conclusion:** meaningful reuse exists on both ends — the *downstream* execution plumbing
(`data_exec.place()` + `Broker` shape) **and** the *order-signing core* (a proven pinned client +
MIT reference implementations to adapt). Item 8 is **M–L**, not a from-scratch L; the spike chooses the
client path rather than evaluating an unproven dependency cold.

---

## Prioritized build list (implementation order + effort, post reuse-audit)

Effort key: **S** ≈ hours · **M** ≈ ~1 day · **L** ≈ multi-day. Order respects dependencies.
**[paper bug]** = real today, lands independently of any go-live decision.
**[⚠ silent-failure]** = no loud error path; would otherwise be a "why isn't it trading at 2am" mystery.

**Group A — Paper-mode correctness (land now, independent of live)**
1. **[paper bug] Risk-gate flag wiring** — S–M. Set `is_prediction_market` + `implied_prob_at_entry` on PCT
   orders; add `polymarket_copy_trader` to `_sum_polymarket_today` (`risk.py:407`).
2. **[paper bug] `_sum_polymarket_open` + max-open-positions** — S–M. Implement the open-notional query
   (`risk.py:434`, currently `return 0.0`); add a position-count cap. *Open-aggregate cap is off
   system-wide — `polymarket_arbitrage` is uncapped on open notional too.*

**Group B — Silent-failure operational blockers (high visibility)**
3. **[⚠ silent-failure] Polygon write-path geo-check** — S. Verify authed/write CLOB endpoints serve the US
   VM IP (only reads smoke-tested 2026-05-09). EU-proxy runbook exists if blocked. Do early.
4. **[⚠ silent-failure] USDC→CTF allowance, per wallet** — S/wallet. One-time on-chain `approve()` to the
   exchange contract(s); **per-EOA** (folds into item 6). Without it, orders silently never fill.
5. **[⚠ silent-failure] MATIC gas reserves + per-wallet monitoring** — S–M. Every order burns MATIC;
   **per-EOA** balance tracking + low-balance alert + top-up runbook. Without it, live placement silently
   fails when MATIC drains.
   > **CORRECTION (2026-05-29):** "every order burns MATIC" is **overstated.** CLOB order placement is
   > *gasless* for the user — orders are signed off-chain and matched/settled by Polymarket's operator.
   > MATIC/POL is consumed only by (a) one-time per-wallet approvals and (b) per-resolution
   > `redeemPositions`. The live wallet holds 98.375 POL (ample). The monitoring/low-balance work is
   > still worth doing for the Group C per-division-wallet pattern (new EOAs may start under-funded), but
   > it is not a per-order silent-failure risk. See `reports/2026-05-29_polymarket_live_prep_groupB_spike.md`.

**Group C — Live build**
6. **Per-Polymarket-division wallet pattern + arbitrage migration** — **L**. Per-division KV secret paths,
   `Secrets` fields, broker instances keyed off division; **migrate `polymarket_arbitrage` onto the same
   pattern (in scope).** Per-EOA provisioning includes CTF `approve()` (#4) + MATIC funding/monitoring (#5).
7. **Credential preflight** — S. Add a `"polymarket"` branch to `assert_live_ready` (`secrets.py:335`)
   checking per-division key+funder before a LIVE start.
8a. **Client-path spike** — S. Decide **Path A** (pin `py_clob_client==0.17.5` + py_order_utils/web3/
    eth-account, adapt the MIT refs `Polymarket/agents` & `poly-maker`) vs **Path B** (beta
    `polymarket-client`). Validate with a sign-only script (construct+sign one order, do NOT post). Exclude
    GPL `OctoBot`; license-check the copy-trading-specific refs if used. **Operator lean: start from Path A.**
8. **`PolymarketLiveBroker(Broker)` adapter** — **M–L** (was L). Adapt the chosen client's signing+post flow
   into `place_order`/`cancel_order` (signing internal to the client); conform to `tastytrade.py:252` shape;
   `data_exec.place()` handles fill/audit downstream. M if Path A (reference-adaptable), L if Path B (beta,
   undocumented).
9. **Live-mode routing in the loop** — M (low end; reuses `data_exec.place()`). Pass live/dry decision in;
   add the `auto_execute` branch mirroring existing HITL call sites (also activates `--dry-run` for PCT,
   item 11); flip division broker selection + `standby:false`.

**Group D — Observability + correctness for live**
10. **Audit mode tagging** — S. `mode: paper|live` on `would_have_placed` / `filled` payloads.
11. **Resolver + dashboard live read-side** — M (low end; dry-run/`filled`/snapshot plumbing already exists).
    Resolver processes `filled`; open-trades query includes `filled`; verify equity curve; set a fresh
    `metrics_epoch` at cutover (operator action).

**Group E — Live-state integrity**
12. **Position reconciliation + field-mapping verification** — M. Startup + periodic `our_positions` vs
    on-chain `_fetch_positions()`; verify positions response shape against a funded wallet.
13. **Restart/crash recovery + duplicate-order guard** — M. Broker-truth recovery on startup (Donchian
    pattern, `main.py:461-498`).
14. **Execution robustness** — M. Order-timeout/cancel, partial-fill handling, stale-quote exit guard
    (`_emit_exit` sizes off persisted `entry_price`), fee/min-edge check at $1–$5 sizing.
15. **Kill-mechanism hardening** — S–M. One-click strategy pause (endpoint / persisted `halted`).

**Group F — Cutover**
16. **$1 min-size shakedown** — S (gated on all above). Per-wallet MATIC/positions/geo monitoring before
    meaningful capital.

**Rough total:** one firm **L** (wallet pattern) + one **M–L** (adapter, depending on Path A vs B) + a small
spike + ~eight **M** + several **S** ⇒ on the order of **~2–3 weeks** of focused build before a real-money
shakedown. The reuse audit shortened the critical path on both ends: downstream `data_exec.place()` plumbing
+ `Broker` shape trim items 9 & 11 to low-end M, and the order-signing core (item 8) is reference-adaptable
(proven pinned `py_clob_client` + MIT impls) rather than greenfield — M–L, not L. Items #1–#2 remain landable
immediately and independently of the live decision.

---

## Status log

- **2026-05-28** — Audit complete + shelved.
- **2026-05-29 — Group A #1, #2, #3 SHIPPED** (paper-mode correctness; all deployed to prod + verified):
  - **#1 risk-gate flag wiring + #2 open-notional cap functional** — `5b947ea`. PCT orders now route into `_evaluate_polymarket`; `_sum_polymarket_open` is real (was a `0.0` stub).
  - **#3 resolver settle-fairness** — `ab8232a`. Per-actor scan budget; arbitrage no longer starves the copy-trader. First post-deploy tick **resolved 640** (was `0` every tick for 12h+); stuck-on-resolved **578 → draining to ~0** over the next 1–2 hourly ticks.
  - **Open-aggregate notional cap journey:** `1_000` dead stub → `b4838ac` **$4k interim** (the now-live cap immediately bound on $2,998 of accumulated open) → `09481de` **$3,500 principled** (after #3 drained the contamination; ~$1.5k headroom over genuine ~$2k).
  - **`max_open_positions` count cap DISABLED for PCT** — `504fb6e`. A flat per-division count cap is architecturally wrong for a many-whale copy-trader (hundreds of concurrent open by design); the notional cap + per-position size limits are the real constraints (reasoning recorded in `config/risk.yaml`).
  - **Follow-up filed (P1, BACKLOG.md):** copy-trader SELL-pairing skips ~99.86% of exits — a separate metric-contamination vector; scope-before-build.
  - **Note:** open notional was $2,415 one tick post-#3 and settling toward genuine (~$2k) as the resolver drains the remaining backlog over the next 1–2 ticks.
- **2026-05-29 — Group B + item 8a prep complete** (`reports/2026-05-29_polymarket_live_prep_groupB_spike.md`): Path A signing CONFIRMED; geo GO (authed surface serves the US VM, task #31 closed); **gas premise corrected** (CLOB orders gasless — see item 5 correction note above). **Collateral premise corrected:** live CLOB settles in **USDC.e** (on-chain `getCollateral()`), so the wallet's 500 **native** USDC is the wrong token. Code fix `631ddc4` (read-path → USDC.e, **held**, not deployed) + doc corrections.
- **2026-05-29 — Funding target = Option A** (operator): fund the new PCT EOA directly in USDC.e **after item 6 lands**; the existing arb wallet's $500 native USDC **stays put** (arb is read-only; its funding is a separate decision, not bundled into PCT go-live). Rejected converting the existing wallet (walks back the locked per-division decision; signs a DEX swap with the production key).
- **2026-05-29 — Item 6 plan authored** (PLAN ONLY): `reports/2026-05-29_polymarket_item6_wallet_plan.md` — per-division wallet map, exact per-file diffs, arb migration = keep-wallet (i), test strategy, #7 positions-shape verification scoped (post-funding/pre-trade), #8 consolidation flag for item 8. Awaiting ratification + build-sequencing decision.

---

## Verification (for the eventual build — not this session)

- **Risk-gate fix:** unit test that a PCT `ProposedOrder` routes into `_evaluate_polymarket` (flag set) and
  an over-cap notional is rejected/resized; confirm `_sum_polymarket_today` counts PCT rows and
  `_sum_polymarket_open` returns non-zero against open positions.
- **Client-path spike:** a throwaway script that constructs + signs (does NOT post) one order via the chosen
  client (`py_clob_client==0.17.5` or beta `polymarket-client`) on Polygon, proving the signing path works
  before adapter work starts.
- **Live adapter:** `--live --dry-run` produces a `dry_run_skip` (not `would_have_placed`) for PCT; a real
  $1 shakedown order observed on-chain with a matching `filled` audit row tagged `mode:live`; dashboard
  Open/History render it.
- **Wallet isolation:** confirm `polymarket_copy_trader` and `polymarket_arbitrage` resolve to distinct EOA
  signer/funder addresses and distinct broker instances; arbitrage still functions post-migration.
- **Recovery:** kill mid-position, restart, confirm `our_positions` reconciles to on-chain truth with no
  duplicate re-entry.
- All read-side queries via `scripts\run_capped.ps1` per the capped-Python rule.
