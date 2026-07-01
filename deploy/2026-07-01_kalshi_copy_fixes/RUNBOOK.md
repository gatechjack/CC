# Kalshi Copy-Trader Fixes — Deploy Runbook (operator-executed, §4)

Branch `kalshi-copy-recording-shortfilter-2026-07-01` @ `41ad9d6` (off main `79cbbef`).
**No prod touch by the agent.** Operator runs apply + restart at a chosen flat window.

## What ships (5 code files + 1 config) — TWO groups

### GROUP A — correctness fixes (take effect on restart; NO behavior change)
1. **NO-leg fill price** (`brokers/kalshi_live.py`) — FillEvent records the outcome-leg
   per-contract cost (NO = 1−yes_price), not the YES-centric 0.987. Kills the $163.84
   phantom; fixes `copy_size_usd`/residual/PnL. **Count logic was already correct**
   (`usd_to_contracts = floor(copy_usd / no_price)`; 166 = $3 / $0.018 — see below).
2. **Round-trip recording** (`agents/kalshi_resolver.py`) — live copies book a
   `kalshi_round_trips` row with realized PnL on market **settlement**. Gated on a
   `leg_priced` marker (`main.py`) so the 4 pre-fix trades (poisoned price) are SKIPPED,
   not backfilled as −$163 phantoms.
3. **expiration_time** surfaced by `brokers/kalshi.py` `get_market_resolution` (additive).

### GROUP B — ultra-short-market filter (STRATEGY BEHAVIOR CHANGE — SHIPS OFF)
`agents/strategies/kalshi_copy_trader.py` skips a whale entry whose market resolves
within `min_minutes_to_resolution`. **Config ships this at `0` (filter OFF) — the deploy
is correctness-only, zero behavior change.** The code is present and tested but inert
until the knob is set > 0. **It hot-reloads — enable WITHOUT a restart** (see step 6).

**Impact if enabled (historical resolved copies, resolution−entry < threshold):**
| whale | @30min filtered | @60min filtered | retained @60 |
|---|---|---|---|
| pritz786 | ~50% (46/92) | **~77% (71/92)** | 21 copies |
| MaggieTheEagle | ~17% | ~17% (3/18) | 15 |
| AI.EDGE | ~19% | ~19% (3/16) | 13 |
pritz786 avg market life = 55 min (46 of 92 resolve in <30 min). @60 it does NOT go to
zero (23% retained) but is heavily cut. Operator picks the value.

## Drift gate — RESULT (LF-normalized content md5; prod is CRLF but content-clean)
All 6 files: **prod content == main base (CLEAN)** — no real divergence, no targeted-hunk
needed. (Prod files carry CRLF line terminators; verification below is LF-normalized so it
is line-ending-agnostic.) Re-confirm before applying:
```bash
# run in git-bash from the branch worktree
for f in trading_corp/brokers/kalshi_live.py trading_corp/brokers/kalshi.py \
  trading_corp/agents/strategies/kalshi_copy_trader.py trading_corp/agents/kalshi_resolver.py \
  trading_corp/main.py config/strategies.yaml; do
  p=$(ssh azureuser@trading.jacksumner.com "tr -d '\r' < /home/azureuser/trading_corp/$f | md5sum | cut -d' ' -f1")
  b=$(git show main:$f | md5sum | cut -d' ' -f1)
  [ "$p" = "$b" ] && echo "CLEAN $f" || echo "*** DRIFT $f (stop, re-scope) ***"
done
```
Expected: `CLEAN` ×6. Any `DRIFT` → STOP (prod changed since this package was cut).

## TARGET md5 (LF-normalized content, post-apply)
| file | target |
|---|---|
| brokers/kalshi_live.py | `bbd851a6194c638df4bb3a9f2c3d3e63` |
| brokers/kalshi.py | `18626cf0ddcdf6c3663be7d9602abbba` |
| agents/strategies/kalshi_copy_trader.py | `b2a2d1f1a2e432c30c2d1cba55b4918c` |
| agents/kalshi_resolver.py | `b7a884eb1209cd3a4d4f2b89d1825f2f` |
| main.py | `3eb61f8c110ee74b720d3ac1df525c85` |
| config/strategies.yaml | `f4a93c701d66217e1fa679324a5791d2` |

## Steps

**1. Pre-flight** — run the drift-gate above; require `CLEAN ×6`.

**2. Apply (azureuser; no root; files inert until restart)** — run `apply.sh` from a
   **git-bash** checkout of the branch (git-bash preserves LF; PowerShell pipes convert
   LF→CRLF — content is still correct either way since verify is LF-normalized, but
   git-bash keeps prod tidy). Backs up each file to `*.bak-pre-copyfix-2026-07-01`, then
   streams the branch blob byte-exact. Applying while the engine runs is safe (changes
   load at restart).

**3. Verify bytes** — run `verify.sh` → require `OK ×6` (LF-normalized content == target).

**4. Restart at a FLAT WINDOW (operator; NOPASSWD)** — no unit change, just:
   `ssh azureuser@trading.jacksumner.com "sudo -n systemctl restart trading-corp"`
   **Bounces bitunix_sfp + bitunix_futures + robinhood_pead + kalshi** and triggers the
   **RH pickle re-auth** — confirm bitunix + PEAD are bounce-safe first.

**5. Verify live (read-only, post-restart)**
   - `systemctl show trading-corp -p MainPID -p ActiveState -p NRestarts` → active, new PID.
   - journal: `Registered kalshi-live broker for division=kalshi_copy_trading (paper=False)`
     + `KalshiLiveBroker connected` + `RobinhoodBroker logged in` + no Traceback.
   - Filter OFF confirmed: `grep min_minutes_to_resolution config/strategies.yaml` → `0`;
     no `kalshi_copy_entry_skipped_ultra_short` audit events.
   - Recording live (on the next NEW post-fix live copy that settles): a
     `kalshi_round_trips` row appears for `division='kalshi_copy_trading'` with sane PnL;
     and NO `-$163`-scale phantom row for the 4 pre-fix trades (they carry no `leg_priced`
     flag → skipped).
   - Next NO copy: `kalshi_copy_placed_live.fill_price` is the NO-leg cost (small), not a
     ~0.9x YES-centric value; residual (if any) is ~$ tier, not $100s.

**6. (SEPARATE, when approved) Enable the ultra-short filter — HOT-RELOAD, no restart**
   Edit `config/strategies.yaml` `kalshi_copy_trader.min_minutes_to_resolution: 0 -> 30`
   (or `60`). Takes effect within ≤1 poll (~10 min). Disable = set back to `0`. Use
   `editprod.ps1 strategies.yaml` (nano) or a one-line sed. No restart, no code change.

## Rollback
- Code: restore `*.bak-pre-copyfix-2026-07-01` (6 files) + restart.
- Filter only: set `min_minutes_to_resolution: 0` (hot-reload).

## Parity (post-deploy)
Merge branch → `main` (`--no-ff`) + push so `main == origin == prod-content`. Config knob
on prod (`0`) will match main; if the filter is later enabled on prod, update main's
`strategies.yaml` value to match (or note the intentional prod-only value in deploy_log).
