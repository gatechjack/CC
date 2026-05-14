# Tomorrow's session-start prompt (2026-05-13)

Paste this into a fresh Claude Code session in `C:/Users/AA Incorporado/CC`:

---

```
directory is cc

State check before any new work. Yesterday (2026-05-12) was a heavy session
— closed three big things and one architectural piece:

  1. **PM dashboard renders copy_trading divisions** (02:19 UTC)
     - Patched kalshi_resolver wiring gap + 4 web/data.py PM queries to
       recognize *_copy_trader actors + new `division` column on
       polymarket_round_trips. Both /prediction-markets/{kalshi,polymarket}
       _copy_trading dashboards went from "empty" → "renders open trades."

  2. **Apify Starter throttled to fit $200/mo hard cap** (02:34 UTC)
     - Discovered actual burn $10.68/day = ~$320/mo (way over the $200
       Starter cap, which CAN NOT be raised — it's hard-cap). Throttled K3
       `poll_interval_sec` 300 → 600 → ~$160/mo with $40 buffer. mtime
       hot-reload, no restart needed.

  3. **K3 dashboard legibility + copy-trader EXIT pairing** (03:45 UTC)
     - Fix A: K3 strategy captures trade-tape entry price + broker.quote()
       exit price (inverted for NO holdings) → limit_price + whale_entry_price
       + whale_exit_price now populate. main.py allowlist updated.
     - Fix B: dashboard renders "—" for null/0 prices, surfaces @whale_handle
       in SIGNAL column, "whale exit" badge in History tab.
     - Fix C: both resolvers got `_pair_pending_exits` — SELL audit rows
       pair to prior BUY by (whale, market, outcome), compute realized PnL,
       insert round-trip with new `entry_order_id` column linking the pair.
     - Day-1 outcome: 73 K3 exits paired (all $0 PnL because pre-Fix-A had
       null prices — going-forward will have real PnL). 1 PM whale-closed
       round-trip at +$0.20 (PM had prices from day-1).

Both copy traders running on prod. K3 polling 4 whales every 10min; PM
polling 12 whales every 60s.

Today's planned work: TBD. Likely candidates from yesterday's deferred backlog:

  - **P0b multi-leg Polymarket resolver extension** — currently the resolver
    handles binary markets; multi-leg sports trades stay pending forever.
    ~1-2h. Look in `polymarket_resolver._compute_round_trip_row`.
  - **P0a Whales tab** — dedicated cross-venue UI for whale roster + per-whale
    stats. Was scoped at ~3-4h but pieces of the underlying data (whale_handle
    in PMOpenTrade/PMRoundTrip) already landed yesterday — could be smaller
    now. Mostly a template + new aggregation query.
  - **Equity-history wiring for copy_trading divisions** — the EQUITY card on
    both copy_trading dashboards is empty because no equity_history rows are
    being written (orchestrator doesn't spawn snapshot loops for them).
    Forward-compat query already in place (IN-clause); just needs an
    orchestrator change to start the snapshot loops.

Read on session start (in this order):

  1. memory/MEMORY.md (always loaded)
  2. memory/pm_dashboard_architecture.md (extended with exit-pairing section
     yesterday — read the "Copy-trader exit pairing (2026-05-12)" section)
  3. memory/trading_corp_kalshi.md (extended with the K3 dashboard + exit
     pairing bullet)
  4. memory/trading_corp_audit_payload_allowlist.md (still active — main.py
     allowlist gained whale_entry_price + whale_exit_price yesterday)
  5. runbooks/deploy_log.md — top 4 entries are yesterday's (PM dashboard,
     Apify throttle, exit pairing in three sub-deploys)
  6. BACKLOG.md (P0a/P0b status — both still deferred)

Five verification queries to run FIRST before any new work:

  1. **K3 + PM continuing to fire? + paired round-trips growing?**

       (run via az vm run-command since SSH is blocked from current IP — use
       the `az vm run-command invoke -g rg-shared-prod -n tc-prod-vm
       --command-id RunShellScript --scripts "..." --query "value[0].message"
       -o tsv` pattern; memory `trading_corp_az_run_command` has details)

       sqlite3 -header /home/azureuser/trading_corp/data/trading_corp.db '
         SELECT actor, json_extract(payload_json, "$.side") AS side,
                COUNT(*) AS n, MIN(ts) AS first_ts, MAX(ts) AS last_ts
         FROM audit_event
         WHERE actor IN ("kalshi_copy_trader", "polymarket_copy_trader")
           AND kind = "would_have_placed"
         GROUP BY actor, side;
       '

       # Yesterday EOD: K3 = 68 buy + 73 sell. PM = 68 buy + 0 sell (only the
       # +$0.20 one had been paired; new PM exits would have been paired by
       # the resolver tick).

  2. **Paired round-trips by division?**

       sqlite3 -header /home/azureuser/trading_corp/data/trading_corp.db '
         SELECT division, market_result, COUNT(*) AS n,
                ROUND(SUM(realized_pnl),2) AS pnl
         FROM kalshi_round_trips WHERE division="kalshi_copy_trading"
         GROUP BY market_result;
         SELECT division, COUNT(*) AS n, ROUND(SUM(realized_pnl),2) AS pnl
         FROM polymarket_round_trips WHERE division="polymarket_copy_trading"
         GROUP BY division;
       '

       # Yesterday EOD: K3=73 (whale_closed, $0). PM=1 (+$0.20).
       # Today should show K3 going up with REAL PnL on new pairings + PM
       # growing as whales close positions.

  3. **Apify burn — did the 10min throttle land us under $200/mo?**

       az keyvault secret show --vault-name kv-tc-vtwbowt3wtkpy \
         --name APIFY-API-TOKEN --query value -o tsv | xargs -I {} \
         curl -sS -H "Authorization: Bearer {}" \
         https://api.apify.com/v2/users/me/usage/monthly | py -c "
       import json, sys, datetime
       r=json.load(sys.stdin)['data']
       cycle=r['usageCycle']
       total=r.get('totalUsageCreditsUsdAfterVolumeDiscount', 0)
       start=datetime.datetime.strptime(cycle['startAt'][:19], '%Y-%m-%dT%H:%M:%S')
       hours=(datetime.datetime.utcnow()-start).total_seconds()/3600
       daily=total/(hours/24) if hours>0 else 0
       print(f'Cycle: {cycle[\"startAt\"][:10]} -> {cycle[\"endAt\"][:10]}')
       print(f'Burn: \${total:.2f} in {hours:.1f}h')
       print(f'Daily rate: \${daily:.2f}/day')
       print(f'Projected monthly: \${daily*30:.2f} (cap=\$200)')
       "

       # Yesterday EOD throttle was at 02:34 UTC. Daily rate should be ≈
       # \$5.34/day by today (cycle ~48h old). Projected ≈ \$160/mo. If
       # higher than \$8/day, the throttle isn't working as expected.

  4. **Any K3 errors overnight (esp. the new async _emit_exit)?**

       journalctl -u trading-corp --since '12 hours ago' --no-pager 2>&1 \
         | grep -iE 'kalshi_copy|polymarket_copy|_pair_pending|_emit_exit' \
         | grep -iE 'ERROR|Traceback|RuntimeWarning' | head -10

  5. **Dashboard sanity probe (curl localhost, bypasses Authelia):**

       for DIV in kalshi_copy_trading polymarket_copy_trading; do
         curl -sS http://127.0.0.1:8000/partials/prediction-markets/$DIV > /tmp/d.html
         echo "$DIV: size=$(wc -c </tmp/d.html), open=$(awk '/pm-tab-open/{f=1}f;/pm-tab-history/{f=0}' /tmp/d.html | grep -c '<tr'), history=$(awk '/pm-tab-history/{f=1}f;/pm-tab-portfolio/{f=0}' /tmp/d.html | grep -c '<tr'), whale_exit_badges=$(grep -c 'whale exit' /tmp/d.html)"
       done

       # Yesterday EOD K3: open=15, history=147 (73 round-trips × 2), 73 badges.
       # PM: open=121, history=3 (1 round-trip × 2 + headers), 1 badge.

Don't reintroduce:
  - Adding fields to ProposedOrder.extra without updating main.py base_payload
    allowlist for the relevant strategy (memory `trading_corp_audit_payload_
    allowlist`).
  - Resolver functions assuming side='buy' across all audit rows — both
    resolvers now distinguish BUY (market-settle path) from SELL (pairing
    path). Mixing them re-introduces the "exit treated as fresh bet" bug.
  - az run-command --scripts >~64KB inline. Split per-file or use chunks
    (yesterday's 131KB script silently aborted; 4×30KB scripts worked).

Open reminders (lower priority but real):
  - Hashdive email response — if cheap + programmable, refactor K3 data
    source from Apify ($160/mo) to whatever Hashdive offers.
  - K3 whale pool could expand 4 → 6-7 after observation week + budget
    check (current burn projects ≈\$160/mo at 4 whales × 10min cadence;
    expansion would re-hit the \$200 cap).
  - Pre-existing test failures (test_pmcc_logic date-drift,
    test_webhooks_return_fast _Deps fixture missing bitunix_observer) —
    not blocking but worth a once-over.

Backup tags from yesterday (rollback recipes in deploy_log.md):
  pre-pm-dashboard-copy-20260512-0215     (PM dashboard fix, AM session)
  pre-k3-throttle-20260512-0234           (Apify throttle)
  pre-exit-pairing-d1-20260512-0333       (Fix C tracked-file patches)
  pre-exit-pairing-d2-20260512-*          (Fix C untracked-file transfers, 4 files)
  pre-k3-pair-relax-20260512-0345         (K3 pre-existing-row pairing relax)
```
