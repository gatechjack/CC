# Next-session prompt (paste into a fresh session)

New session — Trading Corp. Read your memory (MEMORY.md) and
`reports/2026-06-28_SESSION_HANDOFF.md` first, then confirm git + prod state before any work.

**Where things stand (2026-06-28):**
- `main` @ `7283cc1` == `origin/main` == **live prod runtime** (deploy-clean; reconciliation strategy A is
  done). **Start from `main` — cut a fresh branch.** The old `prod-reconcile-2026-06-28` candidate is fully
  merged; don't keep working on it.
- Prod engine: systemd `trading-corp` PID 3730922, `--live-divisions bitunix_sfp robinhood_pead`.
  `bitunix_sfp` is LIVE + armed + FED (ws hybrid on NAT-gw IP 168.62.60.79), account FLAT, **no first live
  SFP trade yet**. `bitunix_futures` HALTED-INERT, replay disabled. PEAD live.
- SFP cockpit (`/sfp`) just got a nav bar + flicker fix (idiomorph morph + REFRESH chip), deployed hot.

**Start by (read-only):** confirm `main`==`origin/main`, engine PID/uptime healthy, SFP feed fresh +
account flat. Then pick up one of:

1. **First live SFP→BOS trade validation (highest value).** When/if a real fill happens, verify the 2-leg
   bracket (B1 stop + venue TP via `place_tpsl_order`) round-trips cleanly and the cockpit TIER-A/C panels
   populate from real data. (May just be a watch.)
2. **Wire the cockpit TIER-B mocks to real reads** — `_mock_armed_watch` / `_mock_near_miss` /
   `_mock_bos_confirm` need the observer `sfp_watch_state` emit (armed-watch overlay, near-miss, BOS-confirm).
3. **Phase 3 Group B/C prune (operator-gated)** — Group A done; Group B (20, held) is your call; the 2
   dirty+locked `worktree-agent-*` can be force-removed on your OK. Kill-list in
   `reports/2026-06-28_phase3_prune_candidate_killlist.md`.
4. **Bitunix research/levers** (BACKLOG.md, open): P1-A/B TP-structure + silence-window backtest;
   fee/slippage; arm ETH/SOL/XRP (add to SFP `config.symbols`).

**Deploy discipline:** drift-gate vs prod md5; prod deploys as **forced-LF blobs** (`git show HEAD:f | tr -d
'\r' | ssh "cat > prod/f"`, NOT `git archive` — it applies CRLF). Restarts flat-guarded. Operator has no
sudo password (NOPASSWD = systemctl/journalctl/sqlite3 only). Never `git clean`/`git stash`; push-first
before any branch delete.
