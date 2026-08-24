# Prediction Markets — Ops Gotchas (standing)

## ★ GOTCHA 1: root-owned artifacts vs the azureuser runtime (bit us on the first cron fire, 2026-08-23)

**Symptom:** the nightly cron (`20 3 * * *`, runs as **azureuser**) fired on time but died with
`sqlite3.OperationalError: attempt to write a readonly database` at `stats.py:89 rollup`.

**Root cause:** every box operation in the P1 build ran as **root** — because the sanctioned deploy channel
`az vm run-command invoke` executes as root (control-plane), not sudo. So every artifact root created —
here `data/prediction_markets.db` — was **root:root, mode 644**. The runtime user is **azureuser** (the cron,
and any future azureuser-run job), which then has read-only access to a 644 root file: it can pull data but
cannot write the rollup.

**Why it hid until the cron:** all my manual backfills/refreshes ALSO ran via `az run-command` = root, so they
wrote the root-owned DB fine. The mismatch only surfaced the first time the artifact was written by the actual
runtime user (azureuser), i.e. the cron.

**Fix applied 2026-08-23 (Board-authorized):** `chown azureuser:azureuser data/prediction_markets.db`
(+ `-wal`/`-shm` if present — absent here), then re-ran `refresh --cap 50000` **as azureuser** (`runuser -u
azureuser`) to prove it: `REFRESH_EXIT=0`, 12/12 complete, 0 failed/partial, pulled==stored, rollup updated_ts
advanced, ownership now azureuser:azureuser. The `data/` dir was already azureuser-owned; only the .db file blocked.

**STANDING RULE — this will bite again on ANY file P1 writes (logs, exports, new DBs, new migrations' sidecars):**
1. **Prove writes as the runtime user, not root.** After any change that touches a P1 artifact, verify the
   write path works as **azureuser** — e.g. `runuser -u azureuser -- bash -c '… pm_cli … '` — NOT as root.
   A root re-run silently "works" while leaving the azureuser cron broken. (This is exactly what would have
   masked the bug if I'd re-run the failed refresh as root.)
2. **Own artifacts as azureuser from creation.** Future runners that create/write PM files should run the
   mutating step via `runuser -u azureuser --` (or `sudo -u azureuser`) so the file is azureuser-owned at
   birth. If a root step is unavoidable, immediately `chown azureuser:azureuser <artifact>` afterward.
3. **The PM DB, its `-wal`/`-shm`, `pm_refresh.log`, and any export must all be azureuser-writable.** The
   `data/` dir is azureuser-owned (good); the risk is individual files created by a root process.
4. **Never chown broadly.** Touch only the specific PM artifact. `data/trading_corp.db` (legacy) is
   azureuser-owned and off-limits.

**Operational note (not a defect):** the nightly refresh takes ~15-18 min (full 12-wallet re-pull, ~28k rows,
rate-limited by the public data-api). It fires 03:20 UTC, finishes ~03:38, no overlap with other 03:xx jobs.
429 backoff + the per-wallet completeness gate make a throttled run safe (it just takes longer, or marks a
wallet PARTIAL and excludes it from ranking rather than corrupting).

---

## ★ GOTCHA 2: box PM-code ownership is a MIXED MESS from prior deploy channels (found at CP1 Stage-2, 2026-08-23)

**Symptom:** the CP1 live-apply deployed the 3 P2 PM files by `cp` (as azureuser over SSH). The BYTES deployed
correctly (sha256 matched the approved refs), but the deployed files were `root:root` mode **666**
(world-writable), and the parent dirs were worse: `trading_corp/prediction_markets/` = `root:root` **777**;
`trading_corp/scripts/` = owned by Windows-numeric **`197609:197121`** (no such user on the box).

**Root causes (pre-existing P1 debt — NOT introduced at CP1):**
- `az vm run-command` (root) created files/dirs `root:root` (chmod'd 666/777 somewhere along the P1 build).
- `197609:197121` is a **Windows UID/GID baked into a tar built on the Windows dev box and extracted on the box
  as root with `-p`/`--same-owner`** — tar preserved the numeric Windows ownership. `197121` is exactly the local
  git-bash GID on the Windows machine.
- A plain **`cp` onto an existing file preserves that file's inode owner+mode**, so the mess propagates through
  every `cp`-based deploy. It "works" only because the dirs/files are world-writable.

**Why it did NOT break CP1 (and is NOT a GOTCHA-1 failure):** GOTCHA-1 is about artifacts the runtime **writes**
(DB/`-wal`/`-shm`/logs) — those are correctly `azureuser:644` (the rollup wrote the DB fine). Code is only
**read** (imported) by the runtime; `666` is world-readable, so import works. Residue = a hygiene/security smell
(world-writable executable code + root/phantom ownership). Board ruled DEFER (2026-08-23) with the conditions below.

**THE FIX (every future PM deploy, esp. the P2 web-app deploy):**
1. **Build the artifact WITHOUT owner metadata / extract WITHOUT `-p`.** Prefer `git archive` (carries no owner
   metadata); if tarring on Windows use `tar --owner=0 --group=0 --numeric-owner` and extract with
   `--no-same-owner` so files inherit the EXTRACTING (azureuser) ownership — never the Windows numeric IDs.
2. **After any root-context step, `chown -R azureuser:azureuser` the PM paths + set explicit modes** (files 644,
   dirs 755). To flip an existing file's owner via a plain deploy, `rm` then copy (a new file created by azureuser
   in an azureuser-owned dir is azureuser-owned) — but the DIR must be azureuser-owned first.
3. **★ HARD, TESTED requirement for the P2 web-app deploy runner (Board ruling 2026-08-23):** the deploy MUST
   `chown -R azureuser:azureuser` the PM code paths AND set modes, with an **acceptance check that FAILS the
   deploy** if, under the PM paths, ANY entry is still root-owned or numeric-owned, ANY **DIRECTORY is 777**
   (must be 755), or any file is world-writable (must be 644). **Check the DIRECTORIES, not just the files.**

**Open item:** the CP1-deployed code is currently `root:root 666` in `777`/`197609`-owned dirs (functional,
deferred). Recorded in `P2_KICKOFF_2026-08-23.md` as **OPEN-A**; remediated by requirement (3) at the P2 web-app
deploy — NOT silently absorbed. **UPDATE 2026-08-23: OPEN-A PARTIALLY RESOLVED at CP2 Phase-1** — the PM-only
paths (`prediction_markets/` dir+files+`web/`) + the two PM scripts (`pm_cli.py`/`pm_web.py`) are now
`azureuser` 755/644 (gate PASS). Remainder (shared `scripts/` dir `197609:755` + broader engine-tree mix) folds
into the STANDING SECURITY ITEM below.

---

## ★ GOTCHA 3: the box code lives at the DOUBLE path `~/trading_corp/trading_corp/` — deploy THERE, not `~/trading_corp/prediction_markets/` (found at CP2 Phase-2, 2026-08-24)

**Symptom:** the CP2 Phase-2 file deploy extracted the tarball with `tar -C ~` so the package landed at
`~/trading_corp/prediction_markets/…` (SINGLE `trading_corp`). Backup-before-overwrite then failed
(`cp: cannot stat ~/trading_corp/prediction_markets/stats.py`) and the GOTCHA-2 gate tripped on a stale
pre-existing `775` dir there → the runner **ABORTED before restarting pm_web**. No harm: nothing real was
overwritten, nothing restarted (the two safeguards did their job).

**Root cause / the real layout (proven read-only by import-resolution):** the box is a **repo-root checkout**.
`~/trading_corp/` is the REPO ROOT (holds `venv/`, `data/`, and the `trading_corp/` PACKAGE dir). The importable
package is therefore at **`~/trading_corp/trading_corp/prediction_markets/…`** (DOUBLE `trading_corp`). pm_web
runs `WorkingDirectory=~/trading_corp` + `PYTHONPATH=~/trading_corp`, so `import trading_corp.prediction_markets…`
resolves to the DOUBLE path (`s.__file__` → `~/trading_corp/trading_corp/prediction_markets/stats.py`). The live
PM DB is `~/trading_corp/data/prediction_markets.db` (repo-root `/data`). The box tree is **NOT a git checkout**
(`git rev-parse` → "not a git repository") → deploys are **file-copy**, not `git pull`.

**STANDING RULE for every PM box deploy:**
1. **Extract with `tar -C ~/trading_corp`** so the tarball's `trading_corp/…` paths land at
   `~/trading_corp/trading_corp/…` (DOUBLE). **Never `-C ~`** (that makes the inert single path).
2. **Prove the target by import-resolution BEFORE restart**, never by assumption:
   `cd ~/trading_corp && venv/bin/python -c "import trading_corp.prediction_markets.stats as s; print(s.__file__)"`.
3. **Keep backup-before-overwrite + the sha/gate abort** — the backup failing to find the file at the target is
   the tell that the path is wrong; the gate refusing to restart is what kept it safe.
4. A stray single-path `~/trading_corp/prediction_markets/` is dead/inert (nothing imports it); if a botched
   deploy leaves one, remove it **guarded** (it lacks `ingest.py`, and the real double-path `stats.py` exists).

---

## STANDING SECURITY ITEM (CP2 Phase-1 threat scan + follow-up, both 2026-08-23) — WHOLE-PLATFORM, JACK'S HANDS, DO NOT FIX IN A CHECKPOINT

Same class as the Authelia trading-rule tightening and the VM geo-migration: a deliberate, separately-planned
whole-platform change with real blast radius (touches live code that scheduled timers execute + the live proxy).
**NOT a P2 build task, NOT a tidiness note** — logged because "one local compromise reaches live money" is the
severity on a box that trades real funds. All findings are READ-ONLY observations; nothing was changed and no
secret VALUES were ever printed. **Ordered by what an attacker actually needs** (remote / no-foothold first).

**#1 — Engine dashboard listens on `0.0.0.0:8000` (all interfaces), not loopback. [HIGHEST — remote, no local foothold needed.]**
CONFIRMED it is the engine dashboard: `ss -tlnp` shows `LISTEN 0.0.0.0:8000` owned by `python pid=851007`, which
is the child of `trading-corp.service` (`MainPID=850993`). If reachable from the internet it bypasses
Caddy+Authelia entirely. Whether it IS reachable depends on THREE gates, and none was verifiable from the box:
  - The NIC has **no direct public IP** (IMDS `privateIpAddress:"10.0.0.4"`, `publicIpAddress:""`) — the box is
    fronted by a separate public-IP/LB resource (the inbound `172.171.189.116` from the DNS work), so an
    internet→:8000 path first needs **that resource to forward :8000**.
  - Then the **Azure NSG must allow :8000 inbound** — NSG rules are NOT in IMDS → UNVERIFIED from the box.
  - Then on-box **`ufw` must permit :8000** — `ufw` is **active**, but listing its rules needs root → UNVERIFIED.
  All three UNVERIFIED-from-box → **ACTION (Jack, Azure portal): the NIC's NSG inbound rules — is `:8000`
  permitted? That single rule is the whole risk gate.** If the front resource doesn't forward :8000 and/or the
  NSG denies it, this is latent; if any path forwards :8000, the engine dashboard is internet-exposed with no
  auth in front. CONTRAST (the correct pattern): `pm_web` binds **`127.0.0.1:8081` loopback-only** and CANNOT
  drift — the unit pins `PM_WEB_HOST=127.0.0.1` and the launcher default is also `127.0.0.1`; reachable ONLY via
  the proxy.

**#2 — Mixed / phantom ownership across the engine tree. [LOCAL, latent — needs a foothold first.]**
`root:root` + `197609:197121` (a Windows UID/GID baked into a tar built on the Windows dev box) + world-writable
entries. The nested `scripts/` dir (`197609:755`) holds timer-scheduled engine code (pct-pruner /
watchlist-stats / watchlist-deep). Mixed-owner-or-world-writable executable code + scheduled execution = a local
privilege-escalation path. (The PM-only subset was already cleaned at CP2 Phase-1 — GOTCHA-2 OPEN-A; this is the
remaining engine-tree mix.) Fix folds into Jack's ownership pass: `chown -R` to the correct owner + strip
world-write, per GOTCHA-2 requirement (3).

**#3 — World-readable credential-pattern configs. [RESOLVED — false positive → ordinary hygiene.]**
The CP2 follow-up ran a value-SHAPE classifier (READ-ONLY; it never printed a value, a prefix, or a length) over
the three representative config files. Result: all three are `0o644` world-readable, and **8/8** credential-named
scalar keys classified `INLINE-LOOKING` **by the classifier** — BUT every one of those keys ends in **`_env`**:
`.../tastytrade/provider_secret_env`, `.../tastytrade/refresh_token_env`, `.../eodhd/provider_secret_env`,
`.../finnhub/provider_secret_env` (data_providers.yaml) and `/lord_otter/webhook_secret_env` +
`/market_cypher/webhook_secret_env` (strategies.yaml + its `.bak`). The `_env` naming convention means the value
is the **NAME of an environment variable** (a pointer), not the secret itself — so these are REFERENCE in effect;
the `INLINE-LOOKING` verdict is a classifier artifact (it keys off `${`/`$`/`!env` prefixes and does not know the
`_env` convention). **No inline secret was found; the original pattern-match was on key NAMES → FALSE POSITIVE →
drops to ordinary hygiene.** Residual hygiene only: the ~25 world-readable `.bak/.pre-*` strategies.yaml copies
are historical duplicates left `644/664` — same `_env` shape, same conclusion, but worth a `chmod 640` sweep when
Jack does the ownership pass (each un-sampled `.bak` was NOT individually parsed — the classifier ran on 3 files).

Read-only context (reassuring): login-shell accounts = only `root` + `azureuser`; azureuser has 1 SSH key (600);
processes run as least-privilege users (authelia, caddy, azureuser, root, system) — no unexpected login user or
process owner; `pm_web` confirmed loopback-only. Remediation is Jack's, deliberate, on the live stack — do NOT
fix inside a P2 checkpoint (the same rule that kept the Caddy/Authelia edits out of CP2).
