# ITF TENNIS CATEGORY -- STAGED DEPLOY MANIFEST (2026-09-16)

New `itf` prediction-markets category (KXITFMATCH men + KXITFWMATCH women, ONE category/matcher/ctx builder).
Ships **INERT** behind its own `itf_moneyline` market_types token. Branch `pm-itf-build-2026-09-16 @ 602d98f3`,
built off `origin/prod-live c7e580ec` (git truth; box matches). **NO migration** (no schema change; head stays 24).

## WHY (evidence, not the overturned deferral)
KXITFMATCH has 220 + KXITFWMATCH 180 open markets vs KXATPMATCH 0 / KXWTAMATCH 36 today -- ITF is the dominant
live tennis surface, and the live-attached whale 0xfb07f48542 holds 84 ITF positions the atp/wta matcher rejects.
The dry-run (below) confirmed 4,103 real ITF bets across 23,214 live markets, 903 matched with ZERO wrong.

## FILES (7) -- base (prod-live c7e580ec) -> target (602d98f3), CR-stripped sha16
| file | base | target |
|---|---|---|
| data/itf_poly_kalshi_match.py (NEW, force-added) | ABSENT | eb104b4ddf046eeb |
| prediction_markets/execution.py | d12058b331b3205d | 7bff7d51ec5a861e |
| prediction_markets/live_driver.py | 667a7b2b2bd48b46 | 2b89c33a3e66d597 |
| prediction_markets/category.py | f9d8a18e3617bd78 | 9ca972d066cf1218 |
| prediction_markets/search.py | 9c199967d6ab70e1 | 77868f765d203501 |
| prediction_markets/tradable_categories.py | 450ff54f39e6c570 | 046e4e1454598600 |
| prediction_markets/web/live_view.py | ed614d505adf1177 | 05691d237eac3884 |

The 6 base shas were verified == the box's live files during the box gate (live-sha BEFORE == these). The NEW
matcher is force-added past the `data/` gitignore (git add -A would silently skip it).

## PROCESSES
- **Engine (trading-corp)** reads execution/live_driver/category/search + the new data/itf matcher -> needs a
  restart to load the matcher/ctx/adapter. A restart bounces ALL divisions (MACE/PMCC/PEAD/bitunix/coinbase +
  31 PM subs), ~3.5min boot. This is where boot-safety (the ITF import-closure) is realized LIVE.
- **pm_web (prediction-markets-web)** reads category/search/tradable_categories/web/live_view -> restart shows the
  itf Farm tile + tile-filter tradable set + "Tennis" label. Display-only (ITF has no prospects until a Search
  sweep), credential-free, low-risk; deferrable at Jack's discretion but included for a complete deploy.

## ORDERING (all box-writes/restarts are Jack-run; RO verifies are agent-run)
1. **GRAFT** (Jack): `powershell -ep bypass -f "C:\Users\AA Incorporado\cc\pm_itf_graft.ps1"`
   - scp+tar 7 files; drift-gates all 6 modified == base (aborts on any drift), new file ABSENT (or == target for
     idempotent re-run); backs up the 6; CR-strips on apply (box stays LF); re-verifies each == target; py_compiles
     all 7; rolls back + aborts on ANY mismatch. NOTHING restarted. Backup at ~/pm_itf_graft_backup_<ts>.
2. **ENGINE RESTART** (Jack): `powershell -ep bypass -f "C:\Users\AA Incorporado\Desktop\restart_tc.ps1"`
   - az-root `systemctl restart trading-corp`. ~3.5min boot. ★ TIMING: bounces PEAD/equities -- restart clear of
     the 9:30 ET (13:30Z) open (or after close) per the PEAD note; Jack rules timing.
3. **BOOT-VERIFY** (agent, RO): `powershell -ep bypass -f "C:\Users\AA Incorporado\cc\pm_itf_bootverify_ro.ps1"`
   - 7 files == target; NEW engine PID active/running; **itf matcher imported with 0 ImportError/NameError**
     (whole-engine risk cleared); all divisions back; 0 boot Tracebacks (chronic Fidelity/BTC noise excepted);
     ITF INERT (0 itf subs, 0 itf_moneyline tokens, 0 KXITF* orders); arm state ~31 armed / 0 latched UNCHANGED.
4. **PM_WEB RESTART** (Jack): `powershell -ep bypass -f "C:\Users\AA Incorporado\Desktop\restart_pmweb.ps1"`
   - az-root `systemctl restart prediction-markets-web`. Engine untouched.
5. **FF PUSH** (Jack, after box is proven): `git push origin pm-itf-build-2026-09-16:prod-live`
   - Clean fast-forward (branch is c7e580ec -> 6cee594f -> 602d98f3, linear off prod-live; prod-live unmoved).
   - Then tag: `git tag pm-itf-deploy-2026-09-16 602d98f3 && git push origin pm-itf-deploy-2026-09-16`.

## POST-CHECK (what a green deploy looks like)
- Boot-verify: 7 shas == target; 0 import/name errors; MACE 4-loops / PMCC / PEAD / bitunix / coinbase /
  kalshi_jack (18 cat) / kalshi_karen (15 cat) all back; INERT (0/0/0); arm unchanged.
- pm_web after restart: /farm renders an `itf` tile (empty until a Search sweep); /live tile-filter treats itf as
  tradable; no regressions on the existing surfaces.
- prod-live == 602d98f3; box == prod-live (7 files at target sha).

## STOP CONDITIONS (abort / do-not-proceed)
- GRAFT prints any `DRIFT ABORT` / `STAGE MISMATCH` / `APPLY MISMATCH` / `py_compile FAIL` -> it rolls back and
  leaves the box == prod-live base; DO NOT restart; re-investigate.
- BOOT-VERIFY shows import/name error > 0, a missing division, a NEW Traceback class, OR ITF not inert
  (any itf sub / itf_moneyline token / KXITF* order) -> the engine is at risk; roll back the graft (restore from
  ~/pm_itf_graft_backup_<ts>) and restart, then re-investigate. Do NOT FF-push.
- Arm count deviates from ~31 armed / 0 latched -> stop, reconcile before anything else.
- Any live DB write, arm, or token-enable is OUT OF SCOPE for this deploy (ITF ships inert; enabling is a later,
  separately-authorized step).

## DRY-RUN GATE RESULT (already PASSED, box gate 2026-09-16T12:11Z, read-only)
4,103 real ITF bets x 23,214 live KXITFMATCH+KXITFWMATCH markets -> 903 matched; dispatch parity 0 mismatches
(deployed MATCHER_ADAPTERS['itf']); INDEPENDENT audit (ticker code + Kalshi resolution title) WRONG
match/player/type/leg = 0/0/0/0; futures safe-miss (378 non-match-shaped, 0 matched); same-day surname collision
(877 seen, 0 wrong-bound); corpus 0xfb07f48542 84 bets -> 16 matched 0 wrong. Full PM differential = 21 (the
known env-gap baseline) + 0 new. Live tree provably untouched (before==after sha).

## INERT GUARANTEE
`itf_moneyline` is NOT in the legacy default market_types (moneyline,total,spread) a blank/NULL row resolves to,
AND no ITF sub-division exists. Trading requires (a) an ITF sub created+attached+armed and (b) `itf_moneyline`
added to that sub's market_types -- both separately authorized, neither in this deploy.
