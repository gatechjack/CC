"""ONE-TIME overnight global re-Analyze (Board-authorized 2026-11-19; NOT a schedule -- no cron entry may
point here). Re-runs the SAME Analyze code path the dashboard button uses -- loss-grounding fetch (now
RL-supplemented, skill v6) + analyze.analyze_whale (deterministic report, Sonnet narrator, $20/day cap,
pm_analysis_cache / pm_whale_score / pm_analysis_cost writes) + the pm_loss_grounding_cache upsert the
button route performs -- for every ACTIVE pm_watchlist pair. ★ Runs as its OWN process (never through
pm_web HTTP: a long request inside pm_web is how the old refresh button locked pm_web for 16.5 minutes).

Scope + order: active watchlist pairs; pairs attached live on any account first, then pinned, then
candidates. GROUPED BY WALLET: each wallet's books (activity window, full live CLOSED walk, REDEEMABLE_LOST,
gamma per category) are fetched ONCE and every category grounds from them.

Guardrails (enforced here, not left to watching):
- PAUSE WINDOW 04:50Z-06:15Z (refresh/adjudicate/rollup crons own the API + DB).
- ENGINE PROTECTION: per wallet, the engine's recent Polymarket 429/HARD-FAIL count (journalctl) is compared
  to the pre-batch baseline; above it -> pause 15 min; three pauses in a row -> stop and report.
- Batch-side 429: exponential backoff; 5 consecutive -> stop and report.
- SPEND: analyze_whale enforces the $20/day cap itself; on daily_cap_hit this runner stops for the UTC day,
  records where, and resumes after rollover. No override.
- DB: 'database is locked'/SQLITE_BUSY appearing in the engine journal or cron logs during the run -> pause
  10 min; twice -> stop and report.
- WRITES: Analyze's own tables only. Nothing here touches arm/attachment/roster/config.

Resumable: the pm_analysis_cache at skill v6 IS the checkpoint (a restart skips pairs already cached);
failures are tracked in a JSON state file and retried at most MAX_ATTEMPTS times across runs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from collections import OrderedDict, defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading_corp.prediction_markets import analyze, db, loss_grounding  # noqa: E402
from trading_corp.prediction_markets.category import derive_category_from_slug  # noqa: E402

log = logging.getLogger("pm_batch_reanalyze")

PAUSE_WINDOW = ((4, 50), (6, 15))        # UTC daily cron window: start (hh,mm) inclusive -> end exclusive
ENGINE_UNIT = "trading-corp"
ENGINE_GREP = ("429", "HARD-FAIL")
BUSY_TOKENS = ("database is locked", "SQLITE_BUSY")
BACKOFF_SCHEDULE = [30, 60, 120, 240, 480]   # batch-side 429 backoff (s); len == the 5-in-a-row stop budget
MAX_ATTEMPTS = 3                          # per-pair failure budget across runs (state file)
ENGINE_PAUSE_SEC = 900
BUSY_PAUSE_SEC = 600


def _row_category(row) -> str:
    """The SAME tier-1 slug deriver the pm_web Analyze route injects (web/app.py:_row_category)."""
    return derive_category_from_slug(getattr(row, "event_slug", None), getattr(row, "slug", None))[0]


def load_env_file(path: str) -> int:
    """Python-direct KEY=VALUE loader (never bash-source; feedback_never_bash_source_env_files). Only sets
    keys that are not already in the environment. Returns how many keys were set."""
    n = 0
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            v = v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v
                n += 1
    return n


def utc_now() -> float:
    return time.time()


def in_pause_window(ts: float) -> bool:
    t = time.gmtime(ts)
    mins = t.tm_hour * 60 + t.tm_min
    lo = PAUSE_WINDOW[0][0] * 60 + PAUSE_WINDOW[0][1]
    hi = PAUSE_WINDOW[1][0] * 60 + PAUSE_WINDOW[1][1]
    return lo <= mins < hi


def seconds_until_pause_end(ts: float) -> int:
    t = time.gmtime(ts)
    now_min = t.tm_hour * 60 + t.tm_min
    hi = PAUSE_WINDOW[1][0] * 60 + PAUSE_WINDOW[1][1]
    return max(60, (hi - now_min) * 60)


def seconds_until_utc_rollover(ts: float) -> int:
    t = time.gmtime(ts)
    return max(60, (24 * 3600) - (t.tm_hour * 3600 + t.tm_min * 60 + t.tm_sec) + 60)


def _journal_count(since: str, tokens: tuple) -> int | None:
    """Count matching lines in the engine unit's journal since `since` (journalctl date spec). None when the
    journal is unreadable -- the caller treats that as 'check unavailable' and says so loudly ONCE."""
    try:
        out = subprocess.run(
            ["journalctl", "-u", ENGINE_UNIT, "--since", since, "--no-pager", "-q"],
            capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return None
        return sum(1 for line in out.stdout.splitlines() if any(tok in line for tok in tokens))
    except Exception:  # noqa: BLE001
        return None


def engine_429_count(window_min: int = 15) -> int | None:
    return _journal_count("-%dmin" % window_min, ENGINE_GREP)


def busy_count_since(start_iso: str, cron_log_glob: str | None) -> int | None:
    j = _journal_count(start_iso, BUSY_TOKENS)
    total = j if j is not None else 0
    if cron_log_glob:
        import glob as _glob
        for p in _glob.glob(cron_log_glob):
            try:
                if os.path.getmtime(p) < time.mktime(time.strptime(start_iso, "%Y-%m-%d %H:%M:%S")):
                    continue
                with open(p, "r", encoding="utf-8", errors="replace") as fh:
                    txt = fh.read()
                total += sum(txt.count(tok) for tok in BUSY_TOKENS)
            except OSError:
                continue
    return total if (j is not None or cron_log_glob) else None


def load_scope(conn) -> list[dict]:
    """Active watchlist pairs with their priority class: 0=attached live (any account), 1=pinned, 2=candidate."""
    rows = conn.execute(
        """
        SELECT w.wallet, w.category,
               MAX(CASE WHEN a.wallet IS NOT NULL THEN 1 ELSE 0 END) AS attached,
               MAX(CASE WHEN w.status='pinned' THEN 1 ELSE 0 END) AS pinned
        FROM pm_watchlist w
        LEFT JOIN pm_subdivision_attachment a
          ON a.wallet = w.wallet AND a.category = w.category AND a.active = 1
        WHERE w.active = 1
        GROUP BY w.wallet, w.category
        """).fetchall()
    out = []
    for r in rows:
        prio = 0 if r["attached"] else (1 if r["pinned"] else 2)
        out.append({"wallet": r["wallet"].lower(), "category": r["category"], "prio": prio})
    return out


def group_by_wallet(pairs: list[dict]) -> "OrderedDict[str, list[dict]]":
    """Wallets ordered by their best pair's priority (attached wallets first), pairs ordered within."""
    best: dict = defaultdict(lambda: 9)
    for p in pairs:
        best[p["wallet"]] = min(best[p["wallet"]], p["prio"])
    by_w: dict = defaultdict(list)
    for p in sorted(pairs, key=lambda x: (x["prio"], x["wallet"], x["category"])):
        by_w[p["wallet"]].append(p)
    ordered = OrderedDict()
    for w in sorted(by_w, key=lambda w: (best[w], w)):
        ordered[w] = by_w[w]
    return ordered


def upsert_loss_grounding(conn, wallet: str, category: str, g, now_ts: int) -> None:
    """Mirror of web/app.py:_upsert_loss_grounding (same columns, same INSERT OR REPLACE) so the Prospects
    list reads this batch's groundings exactly as it reads a button-click's. Kept in parity BY HAND -- if
    app.py's upsert changes, change this too."""
    conn.execute(
        "INSERT OR REPLACE INTO pm_loss_grounding_cache(wallet, category, honest_wins, honest_losses, "
        "a_only_losses, loss_omission_pct, coverage_pct, activity_truncated, n_activity_held_resolved, "
        "completeness, grounded_ts) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (wallet, category, g.honest_wins, g.honest_losses, g.a_only_losses, g.loss_omission_pct,
         g.coverage_pct, 1 if g.activity_truncated else 0, g.n_activity_held_resolved, g.completeness,
         int(now_ts)))


class State:
    """Crash-safe JSON checkpoint: attempts + outcomes per pair, plus run counters."""

    def __init__(self, path: str):
        self.path = path
        self.d = {"pairs": {}, "stopped_reason": None}
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    self.d = json.load(fh)
            except Exception:  # noqa: BLE001
                log.warning("state file unreadable; starting fresh: %s", path)

    def key(self, w: str, c: str) -> str:
        return "%s|%s" % (w, c)

    def get(self, w: str, c: str) -> dict:
        return self.d["pairs"].setdefault(self.key(w, c), {"attempts": 0, "status": "pending"})

    def save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.d, fh, indent=1)
        os.replace(tmp, self.path)


async def run(args) -> int:
    t_start = utc_now()
    start_iso = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(t_start))
    state = State(args.state)

    with db.connect(args.db) as conn:
        pairs = load_scope(conn)
        spent0, calls0 = analyze.daily_cost(conn, analyze._utc_day(int(t_start)))
    by_wallet = group_by_wallet(pairs)
    todo = [(w, ps) for w, ps in by_wallet.items()]
    n_attached = sum(1 for p in pairs if p["prio"] == 0)
    n_pinned = sum(1 for p in pairs if p["prio"] == 1)
    log.info("scope: %d pairs (%d attached-live, %d pinned, %d candidate) over %d wallets; "
             "spend today so far $%.2f (%d calls); cap $%.2f",
             len(pairs), n_attached, n_pinned, len(pairs) - n_attached - n_pinned, len(todo),
             spent0, calls0, analyze.PM_ANALYZE_DAILY_CAP_USD)
    if args.dry_run:
        for w, ps in todo:
            log.info("  %s -> %s", w[:10], ",".join("%s(p%d)" % (p["category"], p["prio"]) for p in ps))
        return 0

    baseline = engine_429_count()
    if baseline is None:
        log.warning("engine journal UNREADABLE -- the engine-429 guardrail cannot run; proceeding with the "
                    "batch-side 429 guardrail only (disclosed in the report)")
    else:
        log.info("engine 429/HARD-FAIL baseline (last 15 min): %d", baseline)

    from trading_corp.data.polymarket_data_api_client import (PolymarketDataAPIClient,
                                                              PolymarketRateLimitError)
    http_calls = {"n": 0}
    stats = {"done": 0, "skipped": 0, "failed": 0, "llm_calls": 0, "usd": 0.0,
             "pauses": [], "consec_429": 0, "busy_events": 0, "engine_pauses_row": 0}

    async with PolymarketDataAPIClient(max_concurrent=args.concurrency) as client:
        _orig = client._get_json

        async def counted(url, **kw):
            http_calls["n"] += 1
            return await _orig(url, **kw)
        client._get_json = counted

        last_busy_total = busy_count_since(start_iso, args.cron_log_glob) or 0
        wallet_i = 0
        for w, ps in todo:
            wallet_i += 1
            # all pairs already cached at v6? -> skip the wallet without any fetch
            with db.connect(args.db) as conn:
                pending = [p for p in ps
                           if not analyze.is_cached(conn, w, p["category"])
                           and state.get(w, p["category"]).get("attempts", 0) < MAX_ATTEMPTS
                           and state.get(w, p["category"]).get("status") != "done"]
            if not pending:
                stats["skipped"] += len(ps)
                for p in ps:
                    st = state.get(w, p["category"])
                    if st["status"] != "done":
                        st["status"] = "skipped_cached"
                state.save()
                continue

            # ── guardrails at the wallet boundary ────────────────────────────────────────────
            if in_pause_window(utc_now()):
                wait = seconds_until_pause_end(utc_now())
                stats["pauses"].append({"ts": time.strftime("%H:%M:%SZ", time.gmtime()),
                                        "why": "cron window 04:50-06:15Z", "sec": wait})
                log.info("PAUSE %ds: inside the 04:50-06:15Z cron window", wait)
                await asyncio.sleep(wait)
            if baseline is not None:
                cur = engine_429_count()
                # "rises above its pre-batch baseline" operationalized with a noise band: the engine's own
                # polling already logs ~60-70 429/HARD-FAIL per 15 min (measured pre-batch 2026-11-19), so a
                # literal > would pause on Poisson noise. A MATERIAL rise = +50% (at least +10). Disclosed.
                if cur is not None and cur > baseline + max(10, baseline // 2):
                    stats["engine_pauses_row"] += 1
                    stats["pauses"].append({"ts": time.strftime("%H:%M:%SZ", time.gmtime()),
                                            "why": "engine 429/HARD-FAIL %d > baseline %d" % (cur, baseline),
                                            "sec": ENGINE_PAUSE_SEC})
                    log.warning("PAUSE %ds: engine 429/HARD-FAIL %d > baseline %d (streak %d)",
                                ENGINE_PAUSE_SEC, cur, baseline, stats["engine_pauses_row"])
                    if stats["engine_pauses_row"] >= 3:
                        state.d["stopped_reason"] = "engine 429 above baseline 3 wallet-checks in a row"
                        state.save()
                        log.error("STOP: %s", state.d["stopped_reason"])
                        return 2
                    await asyncio.sleep(ENGINE_PAUSE_SEC)
                else:
                    stats["engine_pauses_row"] = 0
            busy_total = busy_count_since(start_iso, args.cron_log_glob)
            if busy_total is not None and busy_total > last_busy_total:
                last_busy_total = busy_total
                stats["busy_events"] += 1
                stats["pauses"].append({"ts": time.strftime("%H:%M:%SZ", time.gmtime()),
                                        "why": "SQLITE_BUSY in engine/cron logs", "sec": BUSY_PAUSE_SEC})
                log.warning("PAUSE %ds: database-locked tokens in logs (event %d)", BUSY_PAUSE_SEC,
                            stats["busy_events"])
                if stats["busy_events"] >= 2:
                    state.d["stopped_reason"] = "SQLITE_BUSY in logs twice during the run"
                    state.save()
                    log.error("STOP: %s", state.d["stopped_reason"])
                    return 2
                await asyncio.sleep(BUSY_PAUSE_SEC)

            # ── wallet-level fetch with 429 backoff ──────────────────────────────────────────
            books = None
            for attempt, delay in enumerate([0] + BACKOFF_SCHEDULE):
                if delay:
                    log.warning("429 backoff %ds (attempt %d) on wallet %s", delay, attempt, w[:10])
                    await asyncio.sleep(delay)
                try:
                    books = await loss_grounding.fetch_wallet_grounding_books(client, w)
                    stats["consec_429"] = 0
                    break
                except PolymarketRateLimitError:
                    stats["consec_429"] += 1
                    if stats["consec_429"] >= len(BACKOFF_SCHEDULE):
                        state.d["stopped_reason"] = "5 consecutive rate-limit failures"
                        state.save()
                        log.error("STOP: %s", state.d["stopped_reason"])
                        return 2
                except Exception as e:  # noqa: BLE001 -- a wallet-level fetch failure fails ITS pairs only
                    log.warning("wallet %s book fetch failed: %r", w[:10], e)
                    break

            for p in pending:
                c = p["category"]
                st = state.get(w, c)
                st["attempts"] += 1
                try:
                    grounding = honest = None
                    if books is not None:
                        g, honest_r = await loss_grounding.ground_category_from_books(
                            client, books, c, category_of=_row_category)
                        ok = g is not None and (g.n_activity_held_resolved > 0
                                                or getattr(g, "loss_side_complete", False))
                        grounding, honest = (g, honest_r) if ok else (None, None)
                    now_ts = int(utc_now())
                    # SHORT transaction per pair: one connection, analyze + grounding upsert, commit, close.
                    with db.connect(args.db) as conn:
                        rep = analyze.analyze_whale(conn, w, c, now_ts=now_ts, force=False,
                                                    loss_grounding=grounding, honest=honest)
                        if grounding is not None:
                            upsert_loss_grounding(conn, w, c, grounding, now_ts)
                        conn.commit()
                    if rep.null_reason == analyze.NULL_CAP:
                        wait = seconds_until_utc_rollover(utc_now())
                        st["attempts"] -= 1        # a cap stop is not this pair's failure
                        st["status"] = "pending"
                        state.d.setdefault("cap_stops", []).append(
                            {"ts": start_iso, "at_pair": "%s|%s" % (w, c), "resumed_after_sec": wait})
                        state.save()
                        log.warning("DAILY CAP HIT at %s/%s -- sleeping %ds until the UTC day rolls over",
                                    w[:10], c, wait)
                        await asyncio.sleep(wait)
                        with db.connect(args.db) as conn:
                            rep = analyze.analyze_whale(conn, w, c, now_ts=int(utc_now()), force=False,
                                                        loss_grounding=grounding, honest=honest)
                            if grounding is not None:
                                upsert_loss_grounding(conn, w, c, grounding, int(utc_now()))
                            conn.commit()
                    if rep.cost_usd:
                        stats["llm_calls"] += 1
                        stats["usd"] += rep.cost_usd
                    if rep.verdict is not None:
                        st["status"] = "done"
                        stats["done"] += 1
                        log.info("[%d/%d wallets] %s/%s DONE tier=%s honest=%sW/%sL om=%s rl_added=%s $%.4f",
                                 wallet_i, len(todo), w[:10], c,
                                 (rep.score or {}).get("tier"), rep.honest_wins, rep.honest_losses,
                                 ("%.3f" % rep.loss_omission_pct) if rep.loss_omission_pct is not None else "-",
                                 rep.rl_added_losses, rep.cost_usd)
                    else:
                        st["status"] = "failed:%s" % rep.null_reason
                        stats["failed"] += 1
                        log.warning("%s/%s NOT CACHED (null_reason=%s, attempt %d/%d)",
                                    w[:10], c, rep.null_reason, st["attempts"], MAX_ATTEMPTS)
                except Exception as e:  # noqa: BLE001 -- one pair must never kill the batch
                    st["status"] = "failed:%r" % e
                    stats["failed"] += 1
                    log.warning("%s/%s FAILED: %r (attempt %d/%d)", w[:10], c, e, st["attempts"], MAX_ATTEMPTS)
                state.save()
                await asyncio.sleep(args.sleep_pair)
            await asyncio.sleep(args.sleep_wallet)

    dur = utc_now() - t_start
    with db.connect(args.db) as conn:
        spent1, calls1 = analyze.daily_cost(conn, analyze._utc_day(int(utc_now())))
    log.info("BATCH END: done=%d skipped=%d failed=%d | http=%d llm=%d $%.2f (ledger today $%.2f/%d calls) "
             "| %.1f min | pauses=%d", stats["done"], stats["skipped"], stats["failed"], http_calls["n"],
             stats["llm_calls"], stats["usd"], spent1, calls1, dur / 60.0, len(stats["pauses"]))
    state.d["summary"] = {"done": stats["done"], "skipped": stats["skipped"], "failed": stats["failed"],
                          "http_calls": http_calls["n"], "llm_calls": stats["llm_calls"],
                          "usd": round(stats["usd"], 4), "wall_sec": int(dur), "pauses": stats["pauses"],
                          "finished": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    state.save()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None, help="PM DB path (default: the platform's pm_db_path)")
    ap.add_argument("--env-file", default=None, help="KEY=VALUE env file (the pm_web unit's; python-parsed)")
    ap.add_argument("--state", default="/home/azureuser/pm_batch_reanalyze_state.json")
    ap.add_argument("--cron-log-glob", default="/home/azureuser/pm_*.log",
                    help="the pm_cli crons' logs (paper-poll + 05:00/05:40/05:50 write here, box crontab 2026-11-19)")
    ap.add_argument("--sleep-wallet", type=float, default=3.0)
    ap.add_argument("--sleep-pair", type=float, default=1.0)
    ap.add_argument("--concurrency", type=int, default=2, help="client semaphore (<=2 per the ruling)")
    ap.add_argument("--dry-run", action="store_true", help="print scope + estimate, write NOTHING")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.concurrency > 2:
        log.error("concurrency > 2 is outside the ruling; refusing")
        return 1
    if args.env_file:
        n = load_env_file(args.env_file)
        log.info("env file loaded: %d keys set", n)
    if not args.dry_run and not analyze.is_llm_available():
        # The SAME scoped, Anthropic-only Key Vault fetch pm_web itself performs at boot (Jack-ruled
        # least-privilege design 2026-08-31: ONE secret, never load_secrets(), never written to disk).
        # Needs KEY_VAULT_URI in the environment, exactly like the pm_web unit.
        try:
            from trading_corp.scripts.pm_web import _load_anthropic_key_from_keyvault
            _load_anthropic_key_from_keyvault()
        except Exception as e:  # noqa: BLE001
            log.warning("scoped Anthropic Key Vault fetch failed: %r", e)
    if not args.dry_run and not analyze.is_llm_available():
        log.error("ANTHROPIC key not reachable in this process -- the narrator would reasoned-null every "
                  "pair and cache NOTHING (only successful verdicts are cached). Set KEY_VAULT_URI (the "
                  "pm_web unit's) or pass --env-file.")
        return 1
    return asyncio.run(run(args))


if __name__ == "__main__":
    sys.exit(main())
