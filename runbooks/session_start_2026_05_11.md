# Tomorrow's session-start prompt (2026-05-11)

Paste this into a fresh Claude Code session in `C:/Users/AA Incorporado/CC`:

---

```
directory is cc

State check before any new work. Yesterday (2026-05-10/11) was a 6-deploy
Kalshi sprint — see runbooks/deploy_log.md entries from 22:29 UTC through
01:08 UTC for the full chronology. Currently running on prod paper-mode:

  - Polymarket arbitrage (LLM divergence, K=20, 30s poll, enabled)
  - Kalshi tail-price arb (structural, 5min poll, enabled)
  - Kalshi temporal+bucket arb (structural, 5min poll, enabled)
  - Kalshi LLM arbitrage (LLM divergence, K=20 + Semaphore(8), 60s poll,
    enabled at 01:08 UTC after first scan hit Anthropic 429s without cap)
  - BitUnix Phase 3.2a (still awaiting Cypher 4h/1D bias to populate)

Read on session start (in this order):
  1. memory/MEMORY.md (always loaded)
  2. memory/trading_corp_kalshi.md (full Kalshi phasing; K1→K6.1 SHIPPED)
  3. memory/anthropic_concurrent_connections.md (NEW yesterday — semaphore
     lesson from kalshi_llm 429 storm)
  4. runbooks/deploy_log.md — top 7 entries are yesterday's Kalshi sprint
  5. BACKLOG.md "P0 NEXT — Kalshi K2.4 / K3 / K4 / K7" (forward roadmap)

Six verification queries to run FIRST before any new work:

  1. Kalshi LLM overnight performance — how many divergence detections,
     any 429 storms?

       ssh azureuser@trading.jacksumner.com "
         sqlite3 /home/azureuser/trading_corp/data/trading_corp.db '
           SELECT actor, kind, COUNT(*) FROM audit_event
             WHERE actor LIKE \"kalshi%\" AND ts > \"2026-05-11T01:00:00\"
             GROUP BY actor, kind ORDER BY COUNT(*) DESC;
         '
         echo
         echo \"=== 429 count overnight ===\"
         sudo journalctl -u trading-corp --since \"2026-05-11 01:00\" --no-pager | grep -c \"429\"
       "

  2. Kalshi LLM divergence detections that emitted orders:

       ssh azureuser@trading.jacksumner.com "sqlite3 /home/azureuser/trading_corp/data/trading_corp.db '.headers on' '.mode column' '
         SELECT ts,
                json_extract(payload_json,\"\$.symbol\") AS symbol,
                json_extract(payload_json,\"\$.divergence_pct\") AS div_pct,
                json_extract(payload_json,\"\$.llm_prob_estimate\") AS llm,
                json_extract(payload_json,\"\$.implied_prob_at_entry\") AS mkt
           FROM audit_event
           WHERE actor=\"kalshi_llm_arbitrage\" AND kind=\"would_have_placed\"
             AND ts > \"2026-05-11T01:00:00\"
           ORDER BY ts DESC LIMIT 20;
       '"

  3. Kalshi structural arb (tail + temporal/bucket) — any opportunities
     above threshold overnight?

       ssh azureuser@trading.jacksumner.com "sqlite3 /home/azureuser/trading_corp/data/trading_corp.db '.headers on' '.mode column' '
         SELECT ts, actor, kind, substr(payload_json,1,180) FROM audit_event
           WHERE actor IN (\"kalshi_tail_price_arb\", \"kalshi_temporal_bucket_arb\")
             AND kind = \"would_have_placed\"
             AND ts > \"2026-05-10T23:00:00\"
           ORDER BY ts DESC LIMIT 10;
       '"

  4. BitUnix Phase 3.2a — bias state finally populated?

       ssh azureuser@trading.jacksumner.com "sqlite3 /home/azureuser/trading_corp/data/trading_corp.db '.headers on' '
         SELECT * FROM bitunix_observer_bias;
         SELECT COUNT(*) FROM paper_trade_record WHERE strategy=\"bitunix_futures\";
       '"

  5. Polymarket overnight — round-trip count toward Phase 2.5 Backtester
     30-trade gate (was 2/0 sports yesterday):

       ssh azureuser@trading.jacksumner.com "sqlite3 /home/azureuser/trading_corp/data/trading_corp.db '.headers on' '
         SELECT category, won, COUNT(*) FROM polymarket_round_trips
           GROUP BY category, won;
       '"

  6. Anthropic cost rough estimate — count LLM calls overnight (each
     ~$0.0035-0.0044 with prompt cache active):

       ssh azureuser@trading.jacksumner.com "sqlite3 /home/azureuser/trading_corp/data/trading_corp.db '
         SELECT
           SUM(CASE WHEN actor=\"polymarket_arbitrage\" THEN 1 ELSE 0 END) AS poly_calls,
           SUM(CASE WHEN actor=\"kalshi_llm_arbitrage\" THEN 1 ELSE 0 END) AS kalshi_calls
         FROM audit_event
         WHERE kind IN (\"polymarket_llm_probability_called\", \"kalshi_llm_probability_called\")
           AND ts > \"2026-05-10T23:00:00\";
       '"

Decision tree after verification:

  - **Kalshi LLM produced many would_have_placed (>30)** → ship K2.4
    (round-trips + equity-snapshot data layer) FIRST so they don't
    accumulate un-resolved. ~2-3h, mostly internal Polymarket-pattern
    reuse.
  - **429s reappeared overnight** → ship K7 (polymarket semaphore +
    consider shared LLM client semaphore). ~30 min for polymarket cap;
    ~1.5h for shared client-layer cap.
  - **BitUnix bias state populated + 1+ paper trade fired** →
    Phase 3.2b multi-leg scale-out unblocked (4-5h focused work).
  - **Polymarket round-trips ≥ 30** → Phase 2.5 Backtester decision
    point reached; can flip polymarket from paper to live.

If audit looks clean and no urgent items surface, my pick for next move
is K2.4 (data layer) since both Kalshi divisions are paper-trading
without round-trip resolution today.

Don't reintroduce: K=20 LLM fan without Semaphore(N) cap (memory
anthropic_concurrent_connections); pykalshi get_all_series(limit) trusted
as a true cap (memory trading_corp_kalshi K2.0 cap-fix lesson); per-trade
HITL on polymarket_arbitrage or kalshi_llm_arbitrage (Board approved
risk caps as the gate).

Backup tags from yesterday (rollback recipes in deploy_log.md):
  pre-kalshi-k1-20260510-2229
  pre-kalshi-k2-20260510-2328
  pre-kalshi-k22-discoveryfix-20260510-2343
  pre-kalshi-k23-dashboard-20260511-0004
  pre-kalshi-k231-percandidate-20260511-0012
  pre-kalshi-k61-llm-20260511-0048
```
