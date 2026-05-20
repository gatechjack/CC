# Next-session pickup prompt (2026-05-21) — kalshi_weather floor post-deploy

*Written 2026-05-20 ~11:50 UTC at end of the kalshi_weather price-floor deploy session. Companion to `session_start_2026_05_21.md` (BitUnix v2 fix verification — independent thread). This file is the canonical pickup for the kalshi work; both files can pick up the next session depending on which thread you want to advance.*

---

Paste this into a fresh Claude Code session in `C:/Users/AA Incorporado/CC`:

---

Resuming from 2026-05-20 session that deployed the side-asymmetric entry-price floor for `kalshi_weather_arb` to paper prod at 11:34:59 UTC. **The floor is live and firing** — 3 `entry_below_floor` skips on the first scan cycle. Read the EOS snapshot at the top of `BACKLOG.md` first; full hybrid deploy story is in `runbooks/deploy_log.md` 2026-05-20 11:35 UTC entry.

## Known hazards (read before touching anything)

- **Two parallel sessions writing the same working tree is a real risk.** This nearly bit us this session — the vol-v2 session whole-file-scp'd `_weather_math.py` and `strategies.yaml` to prod, and those files also carried this session's uncommitted floor content as a side effect. Net result was OK because the bundled content was eventually-intentional, but rollback for the two side-effect files is now manual (no true pre-floor backup for them). If another session is active on this checkout, stop and reconcile before any prod-mutating action.
- **Prod is Python 3.10.12.** Do NOT use Python 3.11+ stdlib features in any prod-targeted script. Specifically: `Path.read_text(newline=...)` / `Path.write_text(newline=...)` are 3.13+ — use `Path.read_bytes().decode("utf-8")` / `Path.write_bytes(src.encode("utf-8"))` instead. Memorized at [[prod-python-version-3.10]].

## Where it landed

**On prod (live, paper-mode):**

```
trading_corp/agents/strategies/_weather_math.py           prod md5 e6ea67d5…  (lines 382-414 = apply_entry_price_floor)
trading_corp/agents/strategies/kalshi_weather_arb.py      prod md5 31595b5d…  (import + call site)
config/strategies.yaml                                    prod md5 d7209e36…  (min_yes_entry: 0.10, min_no_entry: 0.50)
```

Service: trading-corp PID 865556, restarted 2026-05-20 11:34:59 UTC, active+running, paper mode confirmed.

**On local (still uncommitted; superset of prod):**
- Same 3 files modified
- `tests/test_kalshi_weather_fixes.py` modified (9 new tests, 40/40 pass in 0.13s — local only; tests don't run on prod)
- New untracked: `scripts/patch_kalshi_weather_entry_price_floor.py` (the surgical patcher; local md5 `8e24345d…`, also on prod at `~/trading_corp/scripts/`)
- Vol-v2 changes (modify): `trading_corp/agents/strategies/kalshi_crypto_arb.py`, `trading_corp/data/crypto_spot_provider.py`, `trading_corp/main.py` — all already on prod via the 05:52 UTC parallel-session deploy
- Vol-v2 untracked new file: `trading_corp/data/crypto_vol_provider.py` — already on prod via 05:52
- Updated this session: `BACKLOG.md`, `runbooks/deploy_log.md`
- tmp/ throwaways from this and prior sessions (multiple)

**Local committed (`main`):** `06aa94a` (in sync with origin/main as of session end). None of the kalshi work has been committed yet.

## Hybrid deploy summary (one-paragraph version)

Two sessions worked on overlapping content in the same working tree. The vol-v2 session (parallel) finalized at 05:52 UTC and whole-file-scp'd `_weather_math.py` and `strategies.yaml` to prod for its own changes — but those files also carried my uncommitted kalshi_weather floor function and yaml entries as a side effect. By the time my surgical patcher ran at 11:10 UTC, the only missing piece was the call site in `kalshi_weather_arb.py`. Patcher idempotently skipped the two pre-shipped files; surgically patched the third. Phase A's hard gate on prod hashes was honored at the planning moment — drift happened in the gap before Phase B. Net result on prod: the same end-state a clean independent floor deploy would have produced, except only `kalshi_weather_arb.py.pre-floor-20260520-1110` is a true pre-floor backup. Soft rollback (disable the floor) is one command; hard rollback (revert all floor content) is manual.

## Latent bug caught + fixed pre-deploy

Patcher v1 used `Path.read_text(encoding="utf-8", newline="")`. The `newline=` kwarg is Python 3.13+; prod is **3.10.12**. First prod run safe-failed with `TypeError` at the first `_read(p)`, before any write or backup. Fix: switched to `Path.read_bytes().decode("utf-8")` + `Path.write_bytes(src.encode("utf-8"))`. Memorized at [[prod-python-version-3.10]]. Sandbox replay verified the fix produces byte-identical output to working-tree state (CR-normalized; the working-tree has CRLF on Windows). Re-scp'd and re-ran successfully.

## Read first

1. `BACKLOG.md` — EOS snapshot at top (2026-05-20 ~11:45 UTC; supersedes 04:30).
2. `runbooks/deploy_log.md` § 2026-05-20 11:35 UTC — the full deploy entry incl. rollback recipes.
3. Memory (auto-loaded):
   - `kalshi-weather-price-floor-deployed.md` (NEW — replaces ...-pending — load-bearing facts for this thread)
   - `prod-python-version-3.10.md` (NEW — patcher discipline rule)
   - `kalshi-crypto-vol-v2-deployed.md` (parallel session's deploy that inadvertently bundled this one)
   - `sigma-vs-bucket-width-mismatch.md` (bucket_guard still dormant in observed market)
   - `verify-before-narrating.md` (unchanged but still load-bearing)
4. `runbooks/session_start_2026_05_21.md` — BitUnix v2 verification (independent thread; pick that one up instead if BitUnix is the focus).

## Environment sync state

| Surface | State |
|---|---|
| Local working tree | floor + vol-v2 + tests + tmp/ untracked. Uncommitted superset of prod. |
| Local committed (`main`) | `06aa94a` (BitUnix v2 deploy commits + wrap; the floor and vol-v2 are NOT committed) |
| `origin/main` | in sync with HEAD |
| Prod (`tc-prod-vm`) | live: floor + vol-v2 + bitunix-v2-lifecycle. PID 865556. `auto_execute: false`. |
| Backups on prod | `pre-floor-20260520-1110` × 3 files; only `kalshi_weather_arb.py.<tag>` is a true pre-floor baseline |

═══════════════════════════════════════════════════════════════════════════
## PRIORITY 1 — Commit the running code
═══════════════════════════════════════════════════════════════════════════

**Prod is the leading edge. `main` has none of it.** The only record of what's running is prod plus an uncommitted working tree — an unrecoverable state if the checkout is lost. This comes before anything else.

Two scoped commits:

- **vol-v2 commit** — references `runbooks/deploy_log.md` 2026-05-20 05:52 UTC. Files:
  - new: `trading_corp/data/crypto_vol_provider.py`
  - modified: `trading_corp/data/crypto_spot_provider.py`, `trading_corp/agents/strategies/kalshi_crypto_arb.py`, `trading_corp/main.py`
  - the `kalshi_crypto_arb.realized_vol` block + `max_divergence_pct: 35.0` line from `config/strategies.yaml`
- **kalshi_weather floor commit** — references `runbooks/deploy_log.md` 2026-05-20 11:35 UTC. Files:
  - new: `scripts/patch_kalshi_weather_entry_price_floor.py`
  - modified: `trading_corp/agents/strategies/_weather_math.py`, `trading_corp/agents/strategies/kalshi_weather_arb.py`, `tests/test_kalshi_weather_fixes.py`
  - the `min_yes_entry: 0.10` / `min_no_entry: 0.50` + 6 comment lines from `config/strategies.yaml`

Doc updates from this session — `BACKLOG.md`, `runbooks/deploy_log.md`, the new `session_start_2026_05_21_kalshi_post_deploy.md` — can be a third commit or folded into the floor commit. Don't bundle them into vol-v2.

**Don't lump vol-v2 and floor into one commit.** They're two distinct features deployed at two different times by two different sessions; the commit graph should reflect that. Each commit message must reference its deploy_log UTC timestamp so future-Claude can trace code ↔ deploy.

**Hunk-level staging required for `config/strategies.yaml`.** The working-tree file has two distinct hunks: the vol-v2 hunk (`kalshi_crypto_arb` block) and the kalshi_weather floor hunk (`kalshi_weather_arb` block). Use `git add -p config/strategies.yaml` to stage them separately so the two commits don't share the file. (`max_per_day_pct: 120.0` is already in HEAD and unchanged in the working tree — not a hunk; nothing to skip.)

## PRIORITY 2 — Add `tmp/` to `.gitignore`

Do this BEFORE any `git add -A` in PRIORITY 1. The working tree has these untracked `tmp/*` paths from this and prior sessions, all scratch, none belong in git:

```
tmp/deploy_log_with_parallel_wip.bak
tmp/floor_replay/
tmp/kalshi_entry.txt
tmp/scan_weather.py
tmp/session_wrap_commit_msg.txt
tmp/vol_v2_backtest/
tmp/vol_v2_paper/
tmp/vol_v2_poc.py
tmp/vol_v2_verify.py
```

One-line fix:

```
echo 'tmp/' >> .gitignore
```

Then commit `.gitignore` alongside the doc-updates commit (or as its own commit). Either ordering works as long as it lands before any `git add -A` in PRIORITY 1.

## PRIORITY 3 — Floor forward-validation watch

The floor is firing. **That is not the same as the strategy being profitable.** Floor firing is evidence the suppression logic is doing its job; it tells you nothing about whether the remaining (non-sub-floor) trades clear breakeven. Forward paper-data is the only thing that can answer the open question.

**The open question:** Over the next several hundred round-trips with the floor live, does the weather book actually clear breakeven?

Pre-floor baseline: -$65.48 PnL / 68.7% WR / 163 RTs (2026-05-16T19:18Z → 2026-05-20 deploy). Sub-floor entries contributed ~-$75 of that bleed. Removing them retroactively flipped the sample to ~+$10 — but **that's a counterfactual on the same 163 trades, not forward evidence.** Forward evidence requires forward trades.

**Same caveat vol-v2 carries:** a clean deploy is NOT a validated edge. Do not flip `kalshi_weather_arb.auto_execute: false → true` based on a clean restart, a clean first cycle, or even a clean first week. Wait for the forward window.

**Indicator queries (run any time):**

```sql
-- count of entry_below_floor skips since deploy (floor firing, not profitability)
SELECT COUNT(*) FROM audit_event
WHERE kind = 'kalshi_weather_skipped_entry_below_floor'
  AND ts >= '2026-05-20T11:34:59+00:00';

-- breakdown by side (which buckets are catching)
SELECT
  json_extract(payload_json, '$.outcome') AS side,
  COUNT(*) AS n
FROM audit_event
WHERE kind = 'kalshi_weather_skipped_entry_below_floor'
  AND ts >= '2026-05-20T11:34:59+00:00'
GROUP BY side;

-- THE actual question: forward PnL. Only valid once a meaningful sample
-- of round-trips lands post-deploy. Run after >= 50 RTs since
-- 2026-05-20T11:34:59. A few hundred is the right horizon for a verdict.
SELECT COUNT(*) AS n_rts,
       SUM(realized_pnl) AS total_pnl,
       AVG(CASE WHEN realized_pnl > 0 THEN 1.0 ELSE 0.0 END) AS wr
FROM kalshi_round_trips
WHERE division = 'kalshi_weather'
  AND entry_ts >= '2026-05-20T11:34:59+00:00';
```

**At deploy + 6 min:** 1 scan cycle, 29 evaluations, 3 entry_below_floor skips, 0 weather would_have_placed. The floor is exercising. The profitability question is open.

## Other open items (defer; ranked by data support)

Same as the 04:30 snapshot:

1. **$0.40–$0.60 NO fade zone** (n=23 RT slice, WR=43.5%, -$27). Speculative — data suggestive but not conclusive. Would skip ~14% of NO trades.
2. **$0.80–$0.90 NO payoff-asymmetry trap** (n=35, WR=82.9%, -$5.72). High-WR alone is not enough; needs entry-price ceiling or stake reshape.
3. **T-ticker handling** (n=17, WR=58.8%, -$21). Bucket-guard doesn't reliably apply when `σ < |forecast - threshold|`.
4. **`bucket_guard` is NULL in `kalshi_round_trips.extra_json`**. Resolver builds RT extra from a different source than audit allowlist. Low priority — guard is dormant.

If any of these get pursued, do them as separate small surgical patches, not bundled into one mega-deploy. None of them precede PRIORITY 1-3.

Also safe to delete (scratch from this and prior sessions; defer to after the gitignore commit lands): `tmp/scan_weather.py`, `tmp/vol_v2_poc.py`, `tmp/vol_v2_verify.py`, `tmp/floor_replay/`.

**`max_per_day_pct=120.0` is reconciled** (local==prod since `00e0c45`, 2026-05-15); only the stale `# backport to main pending` portion of the inline yaml comment remains to delete — a one-line edit in `config/strategies.yaml` line 1519, folded into the wrap commit.

## Things to NOT do without explicit approval

- **Don't flip `kalshi_weather_arb.auto_execute: false → true`.** Forward paper data over the new 60-day window from 2026-05-20 is the gate. Same standard as kalshi_crypto vol-v2.
- **Don't tighten or loosen `min_yes_entry` / `min_no_entry`** without a fresh data-backed rationale. Current thresholds came from post-cutoff RT analysis with explicit `0/5 -$37.50` evidence on both sides. Move them only with comparable evidence.
- **Don't whole-file scp from this working tree to prod.** The working-tree files differ from prod by both line endings (CRLF vs LF on `_weather_math.py`) and unrelated content. Use the surgical patcher pattern.
- **Don't hard-rollback the floor without first explicitly authorizing the manual surgery on `_weather_math.py` and yaml** — the auto-restore only handles the call site (soft rollback). Hard rollback requires line-level edits on prod files that don't have a pre-floor backup.
- **Don't use `Path.read_text(newline=...)` or `Path.write_text(newline=...)` in any prod-targeted script.** Prod is Python 3.10.12; these kwargs are 3.13+. Memorized at [[prod-python-version-3.10]].
- **Don't ship a "hot-patch" to `max_per_day_pct` without recording it in `deploy_log.md`** — the existing 120.0 hot-patch on prod has its origin documented in a comment inline; future drift will need similar provenance.

## Lessons recorded (memory updates this session)

- **NEW** `kalshi-weather-price-floor-deployed.md` — replaces `kalshi-weather-price-floor-pending.md` (deleted). Captures deploy state, hybrid story, forward-validation target.
- **NEW** `prod-python-version-3.10.md` — feedback memory; prod is Py 3.10.12; use `read_bytes`/`write_bytes` not `read_text(newline=)`.
- (unchanged but still load-bearing) `verify-before-narrating.md`, `sigma-vs-bucket-width-mismatch.md` (§ Dormant), `kalshi-crypto-vol-v2-deployed.md`, `scp-deploy-count-diff-files.md` (the parallel session's matching lesson from the other side).

## Honest assessment

The deploy is right. The floor is firing. The first scan caught 3 real sub-floor proposals — that's better than I expected on a single cycle. The hybrid path (vol-v2 bundling the floor via shared working tree) was a procedural mess but landed clean because both sessions were working against coherent end states.

The deeper lesson is **two sessions writing to one working tree is a hazard**. We got lucky here because the bundled content was eventually-intentional. Next time it could be eventually-not-intentional. Memory `scp-deploy-count-diff-files` plus this session's surgical-only-not-scp discipline are the durable forms of that lesson.

The other lesson is **AST passing locally is not deploy-ready**. Local was Python 3.14; prod is 3.10. The patcher safe-failed (zero state change), but a less-defensive patcher could have left prod half-patched. The bytes-mode-IO rewrite is the durable form of THAT lesson.

Pickup with the two scoped commits from PRIORITY 1 (and `.gitignore` from PRIORITY 2 first if you'll be using `git add -A`) before doing anything weather-shaped. Then PRIORITY 3 (floor forward-validation watch) is observation, not action — let the paper data accumulate.
