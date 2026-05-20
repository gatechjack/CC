# Next-session pickup prompt (2026-05-20)

*Written 2026-05-20 ~04:30 UTC at end of the kalshi_weather price-floor session. Supersedes `session_start_2026_05_19.md` (BitUnix-focused, now stale for the weather work that's actually on the working tree).*

---

Paste this into a fresh Claude Code session in `C:/Users/AA Incorporado/CC`:

---

Resuming from 2026-05-20 ~04:30 UTC wrap. One work thread last session: kalshi_weather post-cutoff performance review + side-asymmetric entry-price floor. The floor change is sitting in the local working tree, tested, and **not deployed**. Read the EOS snapshot at the top of `BACKLOG.md` first.

## What landed last session (uncommitted; on disk)

**No prod changes. No commits. No `git push`.**

```
config/strategies.yaml                              | +8
tests/test_kalshi_weather_fixes.py                  | +53
trading_corp/agents/strategies/_weather_math.py     | +35
trading_corp/agents/strategies/kalshi_weather_arb.py| +17
4 files changed, 113 insertions(+)
```

- `_weather_math.apply_entry_price_floor` — NEW pure helper. Side-asymmetric: YES skipped at `<= 0.10` (inclusive), NO skipped at `< 0.50` (strict). The asymmetric comparator is deliberate so NO at exactly $0.50 stays in the live `[0.50, 0.60)` band that the post-cutoff RT analysis bucketed it into.
- `kalshi_weather_arb._evaluate_market` — calls the helper between the `share_price` range check and the Kelly sizing block. Reads thresholds off `self._strat_cfg` the same way `bucket_guard_flip_yes_implied_ceiling` is read at line 609. Skips become `code=entry_below_floor` audit rows.
- `config/strategies.yaml` — adds `min_yes_entry: 0.10` and `min_no_entry: 0.50` under `kalshi_weather_arb:`.
- `tests/test_kalshi_weather_fixes.py` — 9 new tests including 8 parametrized boundary cases. Run: `python -m pytest tests/test_kalshi_weather_fixes.py -v -p no:cacheprovider` → 40/40 passed in 0.19s last session.

## What I learned about the strategy (the load-bearing bits)

- **Post-cutoff scorecard (163 RTs since 2026-05-16T19:18Z):** 68.7% WR, **-$65.48 PnL**. Passes the 65% paper→live WR gate yet bleeds on payoff asymmetry. Day-2 (5/19) flipped positive (+$22 / 75% WR) — strategy may already be self-correcting on better-shaped markets.
- **The bucket_guard from the 5/16 fix is dormant.** Direct query of `audit_event.payload_json.bucket_guard` across the entire 245-row post-cutoff `would_have_placed` set: every row carries `null`. Correct behavior (its trigger condition rarely fires on the observed market shape), but means the *date-parse* half of the 5/16 fix is what's carrying the improvement. Don't be surprised that the floor is the first thing that actually moves PnL since 5/16.
- **The only clean structural bleed:** sub-floor entries (YES ≤ $0.10 went 0/5, NO < $0.50 went 0/5; combined -$75 of the $65 net loss). The floor change targets exactly this.
- **Deferred levers (data didn't support shipping yet):** $0.40-0.60 NO fade zone (-$27 / 43.5% WR), $0.80-0.90 NO asymmetry trap (-$5.72 / 82.9% WR), T-ticker underperformance (-$21 / 58.8% WR).

## Read first

1. `BACKLOG.md` — EOS snapshot at top (2026-05-20 ~04:30 UTC; supersedes 2026-05-18 14:30).
2. Memory (auto-loaded):
   - `kalshi-weather-price-floor-pending.md` (NEW — full state of the in-flight change)
   - `verify-before-narrating.md` (NEW — discipline lesson the session shaped; user trusts queries, not narration)
   - `sigma-vs-bucket-width-mismatch.md` (UPDATED — new "Dormant in post-cutoff RTs" section)
   - `procgov-wrapper-non-functional.md` (unchanged but still relevant for workload restraint)
3. `runbooks/deploy_log.md` — last weather-relevant entries are 2026-05-17 03:09 UTC (`target_iso` audit field) and 2026-05-16 19:18 UTC (bucket-guard + date-parse fix). No 2026-05-20 entry — nothing deployed yet.
4. `CLAUDE.md` — invariant #6 says python must go through `scripts\run_capped.ps1`. The wrapper memory says it's non-functional on 25H2, but the rule is still on the books. Last session ran pytest unwrapped on a narrow single-file scope (0.19s wall, no incident). Tightening discipline vs updating the invariant is a decision waiting for you.

## Environment sync state

| Surface | State |
|---|---|
| Local working tree | 4 files modified, uncommitted |
| Local `main` | at `504c992`, 2 commits ahead of `origin/main` (unrelated to weather work) |
| `origin/main` | 2 behind local |
| Prod (`tc-prod-vm`) | pre-floor code; `auto_execute: false`; unchanged |

These are **intentionally** out of sync. Don't reflexively push or deploy.

═══════════════════════════════════════════════════════════════════════════
## PRIORITY 1 — Draft the prod deploy plan (read-only)
═══════════════════════════════════════════════════════════════════════════

This is the user-pre-authored next step. **Read-only inspection plus a written plan only. Execute nothing.**

> Draft the prod deploy plan — do not execute any of it. Read-only inspection plus a written plan only.
>
> 1. Run a read-only md5sum (or hash) comparison between local and prod for exactly these files: `config/strategies.yaml`, `trading_corp/agents/strategies/_weather_math.py`, `trading_corp/agents/strategies/kalshi_weather_arb.py`, and `tests/test_kalshi_weather_fixes.py`. Report which match and which have drifted.
> 2. Specifically diff the `kalshi_weather_arb:` block of the prod yaml against local, since that's the known-drift risk. Show me the actual divergence if any.
> 3. Based on the hash results, tell me for each file whether a whole-file replace is safe (prod matches local outside our change) or whether a surgical anchor-based patch is needed (prod has drifted). For any file needing a surgical patch, write the patch script modeled on `scripts/patch_kalshi_weather_kelly_sizing.py` and show it to me — do not run it.
> 4. Confirm whether prod currently has `auto_execute: false` for `kalshi_weather_arb`, since I want to verify that independently of local config.
> 5. Lay out the full deploy sequence as a numbered plan including how the change takes effect (config reload vs restart) and how I'd roll it back.
>
> Execute nothing — no file writes to prod, no git operations, no restart, no reload. Read-only commands and a written plan only. Stop and show me everything before any step that mutates prod.

## Prod access notes

- SSH: `azureuser@trading.jacksumner.com`. The Claude Code auto-classifier required explicit per-session authorization last session; new session may re-prompt. Approve via the in-terminal "Allow / Deny" dialog for read commands.
- Tests file does NOT exist on prod (tests live in this repo only). The md5-diff for `tests/test_kalshi_weather_fixes.py` is a local-only artifact — note that explicitly.
- Prod's `config/strategies.yaml` is on the known-drift list; surgical anchor patch is the safe default unless md5 matches.

## PRIORITY 2 — If user wants to land more before deploy

The deferred levers, in order of data support:

1. NO `$0.50-$0.60` fade zone skip (n=21, WR=47.6%, -$12.26 retroactive). Speculative.
2. NO `$0.80-$0.90` payoff-asymmetry handling. Needs a separate design conversation — not just a ceiling.
3. T-ticker handling refinement.

Don't bundle these into the floor deploy. Each is its own decision.

## Two cleanup nits (1-line fixes, anytime)

- Add `tmp/` to `.gitignore` so untracked tmp/ files can't be swept into a future commit.
- Delete `tmp/scan_weather.py` (throwaway audit-scan helper from last session).

## Discipline reminder

- User trusts diffs, not narration. Run the verifying query before asserting anything about prod state, file ages, or payload contents. The verify-before-narrating memory exists because this rule was earned, not theoretical.
- Pytest discipline: scope tight to a single file or directory; the wrapper memory says it can't enforce the cap on this OS build, so workload-side restraint is the only protection.
