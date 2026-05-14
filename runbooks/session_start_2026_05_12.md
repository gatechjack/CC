# Tomorrow's session-start prompt (2026-05-12)

Paste this into a fresh Claude Code session in `C:/Users/AA Incorporado/CC`:

---

```
directory is cc

State check before any new work. Yesterday (2026-05-11) shipped TWO copy-trading
divisions on the same day:

  - **K3 Kalshi Copy Trading** at 18:17 UTC (+ bug-fix at 18:30 UTC)
    - Apify Starter $29/mo Bronze plan
    - 4 selected whales (smedtoshi, NovaRex, tom14cat14, 9187234)
    - 5-min poll cadence, $1/$2/$3 sizing tiers
    - **12 would_have_placed events fired on day 1** — real copy trades flowing
    - Visibility-gradient finding: top-of-leaderboard whales hide; mid-tier
      rank 20-100 is the addressable pool (~7% visibility)

  - **Polymarket Copy Trader** at 20:17 UTC
    - FREE Polymarket Data API ($0/mo, no recurring cost)
    - 12 selected whales via Rule B (top-2/cat × 5 cats + top-2 global)
    - 60s poll cadence, $1/$2/$5 USDC sizing tiers
    - Cold-start fired clean for 11/12 whales (Talvez10 had empty feed)
    - Top whale 248188374: 197 resolved trades, 100% win rate, $133K lifetime
    - New `division` column on `polymarket_round_trips` for cross-division
      sharing with polymarket_arbitrage

Both shipped paper-mode + enabled:true. K3 + Polymarket Copy Trader run side by
side on the same prod VM.

Today's planned work: **Observation + dashboard parity for both copy traders.**
See BACKLOG.md "P0 NEXT — Observation + dashboard parity (2026-05-12)" for
scope. ~5-7h estimated.

Read on session start (in this order):

  1. memory/MEMORY.md (always loaded — has both polymarket + kalshi index lines)
  2. memory/trading_corp_polymarket.md (Polymarket Arbitrage + Copy Trader,
     freshly extended at end-of-day yesterday)
  3. memory/trading_corp_kalshi.md (K1-K3 phasing through visibility-gradient
     + max_results gotcha)
  4. memory/pm_dashboard_architecture.md (division-list-driven dashboard;
     today's P0a "Whales tab" will extend it)
  5. memory/trading_corp_audit_payload_allowlist.md (already bitten twice;
     applies to any new copy-trader payload field)
  6. memory/check_prior_research_first.md (before spawning a research agent
     for "is there a library/repo for X?", grep BACKLOG + CLAUDE + memory)
  7. runbooks/deploy_log.md — top entries are yesterday's K3 + Polymarket
  8. BACKLOG.md "P0 NEXT — Observation + dashboard parity (2026-05-12)"

Five verification queries to run FIRST before any new work:

  1. **K3: did the 12 would_have_placed events accumulate more overnight?**

       ssh azureuser@trading.jacksumner.com "sqlite3 -header /home/azureuser/trading_corp/data/trading_corp.db '
         SELECT COUNT(*) AS n, MIN(ts) AS first_ts, MAX(ts) AS last_ts
         FROM audit_event
         WHERE actor=\"kalshi_copy_trader\" AND kind=\"would_have_placed\";
       '"
       # Yesterday EOD: 12 events. Should grow as whales fire entries.

  2. **Polymarket: any would_have_placed events yet?** (cold-start fired
     20:16-27 UTC; first real emissions land when one of the 12 whales
     opens a new position)

       ssh azureuser@trading.jacksumner.com "sqlite3 -header /home/azureuser/trading_corp/data/trading_corp.db '
         SELECT COUNT(*) AS n, COUNT(DISTINCT json_extract(payload_json,\"\$.whale_user_name\")) AS distinct_whales
         FROM audit_event
         WHERE actor=\"polymarket_copy_trader\" AND kind=\"would_have_placed\";
       '"

  3. **Round-trip resolutions accumulating?** (binary markets only — multi-leg
     sports trades won't resolve until the P0b resolver extension lands)

       ssh azureuser@trading.jacksumner.com "sqlite3 -header /home/azureuser/trading_corp/data/trading_corp.db '
         SELECT division, won, COUNT(*) AS n, ROUND(SUM(realized_pnl),2) AS pnl
         FROM polymarket_round_trips GROUP BY division, won;
         SELECT division, COUNT(*) FROM kalshi_round_trips GROUP BY division;
       '"

  4. **No K3/Polymarket strategy errors overnight?**

       ssh azureuser@trading.jacksumner.com "journalctl -u trading-corp --since '12 hours ago' --no-pager | grep -iE 'kalshi_copy|polymarket_copy' | grep -iE 'ERROR|Traceback' | head -10 || echo '(no errors)'"

  5. **Apify burn against the $29/mo Starter cap** (K3 still using Apify
     Bronze; Polymarket is free)

       curl -sS -H 'Authorization: Bearer ${APIFY_API_TOKEN}' https://api.apify.com/v2/users/me | python -c "
         import json,sys; d=json.load(sys.stdin)['data']; print('Plan:', d['plan']['id'], 'cap:', d['plan']['maxMonthlyUsageUsd'])"
       # Token in Azure KV: kv-tc-vtwbowt3wtkpy / APIFY-API-TOKEN
       # If burn approaches $29 cap, spending limit needs raising in Apify dashboard

Today's design conversation (BEFORE coding the dashboard tab):

  - **Cross-venue Whales tab vs per-venue tabs?** Both venues have shared
    concepts (selected whale → their open positions → our copies → resolved
    round-trips) but different data shapes (Kalshi contracts vs Polymarket
    USDC; Kalshi market_ticker vs Polymarket condition_id+outcome_index).
    Discuss: how much can be unified vs split?
  - **What to surface on the tab?**
    - Selected whale roster: name, category, composite score, last activity
    - Their currently-open positions (from Apify /open_positions for K3;
      from Polymarket /positions for Polymarket)
    - OUR copies: entry price, size, current paper P&L, resolution status
    - Resolved round-trips: from kalshi_round_trips + polymarket_round_trips
      (the latter now has a `division` column)
  - **Refresh cadence on the tab?** HTMX auto-refresh every 30s? Click-to-refresh?
  - **Should the multi-leg resolver extension (P0b) ship FIRST** so the
    dashboard can render Polymarket round-trips for sports markets?

Today's build order (after design conversation):

  1. **P0b first**: multi-leg resolver extension (~1-2h). Touch
     `polymarket_resolver._compute_round_trip_row` to handle the multi-leg
     case (outcome_index match → win, regardless of human label). Verify
     on a real recent multi-leg Polymarket trade.
  2. **P0a**: Whales dashboard tab (~3-4h). Mirror the existing PM Dashboard
     architecture. Probably a single cross-venue tab with HTMX swap, hidden
     unless division ∈ {kalshi_copy_trading, polymarket_copy_trading}.
  3. **Observation (background)**: let the bots run, check audit counts at
     intervals. Goal ≥10 resolved round-trips per division before tuning.

Don't reintroduce:
  - New payload fields without checking main.py orchestrator allowlist
    (memory `trading_corp_audit_payload_allowlist` — bit us twice now)
  - Polymarket sells emitted in USDC; qty must be in CONTRACTS for resolver
    math (see polymarket_copy_trader._emit_entry — `contracts = copy_usdc /
    entry_price`)
  - Trusting agent-cited URLs without a fresh probe (recon agent hallucinated
    `/leaderboards` plural — real endpoint is `/v1/leaderboard` singular)

Open reminders (lower priority but real):
  - K3 Apify spending limit (~$300/mo cap) in Apify dashboard — Jack action
  - Hashdive email response — if cheap + programmable, refactor K3 source
  - K3 whale pool could expand 4 → 12 after observation week + budget check

Backup tags from yesterday (rollback recipes in deploy_log.md):
  pre-kalshi-k3-20260511-1816                  (K3 first deploy)
  pre-k3-bugfix-tradetape-20260511-1830        (K3 trade-tape fix)
  pre-pm-enable-20260511-2017                  (Polymarket enable flip)
  pre-polymarket-copy-20260511-2011            (Polymarket main deploy)
```
