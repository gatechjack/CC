# Polymarket E2 scoping — route the copy loop to the live broker

Date: 2026-06-14 · Branch `e2-scoping-2026-06-14` (off main `fe0666a`), unmerged.
**Scoping/design only — no E2 code.** All file:line claims verified by direct Read.

## Context
E1 (`PolymarketLiveBroker`) is complete + merged (`72e8dc6`). The PCT wallet
`0x216064D944e54756074E11CE5a22B1E4CB6B9F82` is fully provisioned (OP·A present, OP·B
119.978358 USDC.e, OP·C 6/6 approvals). **E2 routes the PCT copy loop from
`would_have_placed` → `data_exec.place()` → the live broker → `FillEvent`** — PCT division
ONLY (arb stays paper). The operator decided the design (6 decisions); this report turns
them into thin, fundless-first increments with the live flip gated last. The two open
sub-questions are resolved (below).

## The path today (verified)
| Step | Location | Note |
|---|---|---|
| Strategy emits `ProposedOrder`, stops | `trading_corp/agents/strategies/polymarket_copy_trader.py:416-452` (`_emit_entry`), `:469-503` (`_emit_exit`) | no broker call; `extra` dict at `:429-451` |
| Loop logs `would_have_placed`, never places | `trading_corp/main.py:3459-3467` (loop `~:3332-3490`) | terminus today |
| Live placement primitive | `trading_corp/agents/data_exec.py:99` (`place`); dry-run skip `:111-169`; `broker.place_order` `:179`; `filled` audit `:197-210` | division-keyed broker registry |
| Bitunix analog (to mirror) | gate `bitunix_futures_observer.py:2548` (`execution_mode=="live" AND auto_execute`); `_place_live → data_exec.place(...,"bitunix_futures")` `:2891`; live tag `paper_trade_record.extra_json["execution_mode"]="live"` `:2953/2959` | E2 mirrors this minus HITL |
| Factory (FAMILY-level) | `main.py:1944-1945` (`is_live_family = mode=="LIVE" and division.broker in --brokers`); polymarket branch `:2020-2050` | keys off `division.broker`, **not slug** |
| Current config | `config/divisions.yaml:171` PCT `broker: paper`; `:161` arb `broker: polymarket` | flipping PCT live would arm arb too |
| FillEvent | `trading_corp/persistence/models.py:72-87` | a live `venue="polymarket"` FillEvent flows through all consumers unchanged (no `venue=="paper"` branch anywhere) |
| Order type SDK | `py_clob_client 0.17.5 clob_types.py:170-173` | only `GTC/FOK/GTD` — **no FAK/IOC** |
| DB schema | `trading_corp/persistence/db.py:41-57` (`proposed_order`), `:132-158` (`paper_trade_record`) | **no paper/live column** anywhere |

## Resolved sub-questions
1. **Order type (operator: synthesized FAK).** 0.17.5 has no native FAK/IOC. v1 = `fak_synth`:
   `post_order(GTC)` → poll a **short configurable** window (`fak_poll_seconds`) → `cancel_order`
   any unfilled remainder (E1·4) → `FillEvent` of the filled portion (no phantom). `order_type`
   stays config-driven so FOK / GTD-with-expiry can replace it without a rebuild.
2. **Sizing (operator: flat ≈$1 default).** Ship the full schema; default config reproduces ~$1
   with the conviction multiplier OFF and tight min/max caps.

## The 6 decisions → concrete scope
1. **token_id** → add `"token_id": activity.asset` to the `extra` dicts (`activity.asset` is the ERC-1155 id, `data/polymarket_data_api_client.py:149/191`) + pass through `main.py` base_payload (`:3410-3435`). Makes the broker's direct token_id path the norm; gamma lookup the fallback.
2. **Order type** → resolved above (synthesized FAK, configurable).
3. **HITL = none** → mirror Bitunix's two-level gate (`execution_mode` + `auto_execute`) but DROP the `pending_registry.wait()` HITL step. Whale-promotion is the approval.
4. **Sizing** → resolved above (flat ≈$1 default; schema below).
5. **Per-division live isolation** → a real increment: PCT live while arb paper despite same family.
6. **DB paper/live flag** → first-class column written at placement time.

**Sizing schema (E2·3):** `copy_usdc = clamp(bankroll × per_trade_fraction × conviction_mult, min_usdc, max_usdc)`; config under `polymarket_copy_trader.sizing: {mode: flat|proportional, bankroll_usdc (static, default ~120), per_trade_fraction, conviction: {enabled: false, signal: composite_score, floor, cap}, min_usdc, max_usdc}`. `conviction_mult = 1.0` when disabled. Conviction signals available in `whale_meta` (`composite_score`, `decision_win_rate`, `wilson_lcb`, `realized_roi`, `pnl_inflation_ratio`; loaded `_process_whale_activity:198`) — plumbed in but OFF by default. **Default = flat ≈$1.**

## Increments (thin vertical; fundless-first; live LAST)
| # | Deliverable | Decision(s) | Validation (fundless) | Agent/Operator | Deps |
|---|---|---|---|---|---|
| **E2·1** | `token_id` → `extra` (`_emit_entry`/`_emit_exit`) + main.py base_payload | D1 | unit: `extra`/payload carry `token_id`; paper audit shows it | Agent | none |
| **E2·2** | `order_type` config (`fak_synth` default) + `fak_poll_seconds`; broker `place_order(order_type)`: fak_synth = GTC→poll→cancel remainder→filled-portion FillEvent; gtc/fok/gtd passthrough | D2 | mock client: GTC posted, poll, cancel-on-remainder, FillEvent = filled qty | Agent | E1·3/4 (done) |
| **E2·3** | replace `_size_tier_usdc` with the clamp formula + schema; plumb `whale_meta`; default flat ≈$1, conviction off | D4 | pure-fn tests: flat→~$1; proportional math; clamp; mult | Agent | none |
| **E2·4** | per-division live select: `--live-divisions <slug…>` (+/or divisions.yaml `live: true`); `is_live_division = mode=="LIVE" and slug in live_divisions`; polymarket branch uses it (family still picks the broker class) | D5 | factory tests: PCT slug→live broker; arb→read-only even w/ `--brokers polymarket` (division-level anti-half-flip) | Agent | none |
| **E2·5** | add `execution_mode TEXT NOT NULL DEFAULT 'paper'` to `proposed_order` + `paper_trade_record` (idempotent ALTER-if-absent migration); write paper→'paper', live→'live' at placement | D6 | migration test (col added, old rows 'paper') + write-path test | Agent | none |
| **E2·6** | PCT loop wiring: two-level gate (`execution_mode` + `auto_execute`, fail-closed) **no HITL**; on live → `data_exec.place(order,"polymarket_copy_trading")` → record FillEvent with **ACTUAL filled qty** + `execution_mode='live'` + broker_order_id (so `_emit_exit` sells the real lot, not the paper-assumed size); paper path unchanged | D3 + integrates D1/2/3/5 | mock data_exec+broker: paper when off (no place), place when both on, NO HITL wait, live row recorded | Agent (wiring + mocked) | E2·1/2/3/5 + E2·4 |
| **E2·7** | **live enablement + OP·E**: flip divisions.yaml PCT `broker→polymarket`; `execution_mode: live` + `auto_execute` (start false/1-order); launch `mode LIVE + --brokers polymarket + --live-divisions polymarket_copy_trading`; **OP·E = first live copy end-to-end**, then enable `auto_execute` | live flip | first real-money validation (OP·E) | **Operator-only** | all + deps-deploy |

**Ordering:** E2·1–6 are agent-buildable, fundless, mergeable in the E1·1–7 cadence (E2·1–5 mostly independent; E2·6 integrates them). E2·7 is operator-only.

**Where deps-deploy + OP·E slot:** the **deps deploy** (the `requirements.lock` `setuptools<81` fix already on main `fe0666a`, but still needing the linux `--require-hashes` install smoke + the `e1_lock_input.txt` pin update; deployed via `deploy_e1_lock.sh`) is a **prerequisite to E2·7** — the live broker can't `import web3`/`py_clob_client` on prod without it. **OP·E** is the first exercise of E2·6's path under E2·7's enablement. Both come AFTER E2·1–6 are built + merged.

## Carry-forwards (noted, not E2 scope)
- Synthesized-FAK partial fills → recorded position must reflect ACTUAL filled qty (handled in E2·6); deeper post-trade reconciliation (E1·3 `size_matched` overstatement, issue #245) is **E5**.
- Dashboard paper/live filter UI = **BACKLOG** (the DB column ships in E2·5; the UI does not).
- The screening track (option (c) SELL-pairing) is separate from E2 (execution).

## Status
Scoped, not built. Each increment is its own session/branch. No E2 code, no deploy, no live flip in this work.
