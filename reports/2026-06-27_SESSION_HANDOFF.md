# SESSION HANDOFF — Bitunix SFP / IP-block / two-state (2026-06-27)

Pick-up doc for a fresh session. **No new work started here — commit + handoff only.**

## 1. GIT STATE
- **Branch:** `bitunix-sfp-division-2026-06-25` (worktree: `cc/.claude/worktrees/bitunix-sfp-2026-06-25`). UNMERGED.
- **Working tree:** clean of deliverables. Only untracked = two throwaway run-logs
  (`scripts/_expA2_run.log`, `scripts/_expB2_run.log`) — transient, NOT committed (noise).
- **Commits this session (newest first):**
  - `1537de3` audit: two-state model (TRADING|HALTED-INERT) — paper/replay behavior map + collapse plan
  - `52c7e75` scope: retire replay BitUnix API pulls → read local bitunix_bar_history (1m→3m repoint)
  - `98cffc1` plan: prod egress IP swap (Step-1 assessment + NAT-gw runbook) + bitunix kline inventory
  - `3490b1a` backfill: throw-away BitUnix 3m history puller (assessment + plan + sample-proof)
  - `152a81c` research: 15m-SFP × LTF-BOS (3m/1m) 4-coin — mechanic VALID (BTC control), data-starved
  - `24fef21` research: ETH SFP→BOS × matched-target — nearest miss, NOT verdict-grade
  *(earlier research: `7026ace`, `22bcf3e`, etc. — the ETH/XRP SFP battery arc.)*

## 2. STAGED-BUT-NOT-DEPLOYED ITEMS
- **NAT-gw egress-swap runbook** (az CLI, operator-run in Cloud Shell): full sequenced commands in
  `Desktop/bitunix_reports/2026-06-27_prod_egress_swap_plan.md` (committed `98cffc1`). Creates
  `tc-prod-natgw-pip` + `tc-prod-natgw`, associates to `tc-prod-subnet`, test-before-rely (public-kline
  200), one-line rollback. **Not executed.** Subnet confirmed CLEAN (1 NIC, no existing NAT gw, zero blast radius).
- **Throw-away 3m backfill puller:** `scripts/backfill_bitunix_3m_history.py` (committed `3490b1a`);
  also STAGED ON PROD at `~/backfill_bitunix_3m_history.py` + runner `~/run_3m_backfill.sh`
  (`bash ~/run_3m_backfill.sh`). Sample-proof passed; bulk pull never ran (blocked by the IP 403, then
  superseded — see plan (c) below). **Reconsider given the TradingView-historical decision.**
- **Replay-repoint scope** (`52c7e75`) and **two-state audit** (`1537de3`): analysis only, no code change.
- All reports mirrored to `Desktop/bitunix_reports/`.
- Prior SFP runner scripts (in main cc dir from earlier deploys): `sfp_phase2_restart.ps1`,
  `sfp_phase4_rearm.ps1`, `rearm_sfp.py` — still present, not used this session.

## 3. CURRENT PROD STATE
- **Engine:** PID **3641539** (azureuser, ~7h+ uptime), event loop healthy (webhooks/kalshi/polymarket active).
- **SFP:** `bitunix_sfp` LIVE + armed (`execution_mode:live`, `auto_execute:true`, BTC/USDT.P) — but
  **INERT in practice (blind):** its BitUnix bar feed is dead.
- **★ Bitunix BLOCKED:** Cloudflare **managed bot-challenge** (`cf-mitigated: challenge`, "Just a
  moment…") on prod egress IP **20.51.145.253**, since **~23:12 UTC**. Affects the **whole**
  `fapi.bitunix.com` host:
  - **Live capture DOWN** — `bitunix_bar_history` 3m frozen at 23:12 (all 4 coins).
  - **Authed ALSO 403** — `snapshot()` errors on the dashboard ("no prior snapshot"); the reconciler's
    clean `0/0/0` is a **false-flat** (`get_pending_positions` swallows 403→[]).
  - Egress IP == bound key IP (no drift) → NOT key-binding; it's the IP's reputation (datacenter +
    the engine's relentless polling). Local/residential IPs pass; 16 global datacenter nodes pass →
    it's *our IP specifically*, not Azure-wide.
- **Account: FLAT** (SFP never traded; futures=paper). Any hypothetical position is protected by a
  venue-side B1 stop. Staleness gate IS firing (`entry_rejected_stale_bar`) → no stale-bar entries.
- **Last deploy:** the watch-emit (detector `bitunix_sfp.py` md5 `5c71a103` / observer
  `bitunix_sfp_observer.py` md5 `18da45f2`). **Nothing deployed since.**
- Network: `tc-prod-vm`, eastus, `rg-shared-prod`, private 10.0.0.4, behind an Azure LB whose
  frontend public IP = 20.51.145.253 (also the inbound IP for `trading.jacksumner.com` + SSH).

## 4. DECIDED PLAN — NOT YET EXECUTED (in order)
**(a) FIRST — two-state collapse build** (TRADING | HALTED-INERT). Kill the IP-flagging traffic before
   swapping IPs (else the new IP gets re-flagged). Per `1537de3`:
   - Add a real inert gate (repurpose `standby` or add `mode: trading|halted`) that short-circuits the
     observer before scoring/`would_have_placed`/`insert_paper_trade_record`.
   - Gate the global bar-cache + replay loop to run only when ≥1 bitunix division is LIVE.
   - **bitunix_futures → HALTED-INERT** (no live account; no functional dependency on the sim).
   - Keep: `paper_trade_record` table (universal live ledger), reconciler, live bar-cache (SFP needs
     them). SFP exits are venue-side (B1 + `place_tpsl_order`), reconciler-driven — independent of replay.
   - Net: removes the replay historical bulk (~15–23k kline calls/day + boot bursts) = the dominant
     bot-like footprint. Residual = benign live bar-cache poll (~1.9k/day) for live SFP.
   - Gated like any prod change: tests, drift-gate vs PROD md5 (main.py / paper_trade_replay.py /
     observers), runbook + operator restart.
**(b) THEN — NAT-gw egress swap + key re-bind** (runbook in the egress plan / `98cffc1`). New clean
   egress IP via NAT gateway (inbound `.253` untouched), test public-kline 200, then re-bind the
   Bitunix API key to the new egress IP. Rollback = detach NAT gw.
**(c) Historical data from TradingView, NOT the Bitunix API, going forward.** The engine pulls Bitunix
   ONLY for live trading + small outage gap-fills — never bulk history. (This supersedes the 3m backfill
   puller and the replay's historical re-fetch.)

## SEQUENCING NOTE
Order matters: **(a) before (b).** Swapping to a clean IP while the replay bulk traffic still runs would
just re-flag the new IP. Build inert + gate replay first, then swap. SFP stays blind until (b) restores
the feed (+ key re-bind); it's flat and the B1 stop + staleness gate keep it safe meanwhile.

## OPEN VERIFICATION (for whoever resumes)
- After (b): confirm public-kline 200 from new egress, then authed 200 after key re-bind, then live
  capture resumes (`bitunix_bar_history` 3m advances past 23:12) and reconciler is real (not false-flat).
- Consider disarming SFP during the swap window (live-armed but blind; placement would fail mid-swap).
