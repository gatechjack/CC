# PM P2 box-scratch test harness (REUSABLE — bank, do not re-derive each checkpoint)

Two files, run via the sanctioned `.ps1` runner pattern (operator pastes ONE line):
- `pm_p2_boxscratch_probe.ps1` — local: sha256 the staged tarball, `scp` it to the box, then
  `Get-Content -Raw ...\_p2_probe.sh | ssh $h "tr -d '\r\357\273\277' | bash"` (Alt-streamer; strips CR + the
  PS-prepended UTF-8 BOM). **Operator-specific absolute paths** (`C:\Users\AA Incorporado\cc\...`) — adjust per machine.
- `pm_p2_boxscratch_probe.sh` — box-side (portable): the actual probe. Runs over SSH **as azureuser (NEVER
  az-run-command/root)** so every artifact is azureuser-owned from birth (GOTCHA-1).

## Why this exists
There is **NO local Python** on the Windows dev box (verified 3 ways 2026-08-23), so `P1_PLAN §10 "test locally"`
is impossible here. This harness is the Board-approved substitute: it tests the branch's committed bytes on the
box venv in an isolated scratch, read-only to prod.

## The pattern (adapt the pytest + characterization sections per checkpoint; keep the scaffolding)
1. Build the shipment locally: `git -C <worktree> archive --format=tar.gz -o pm_p2_stage.tgz HEAD trading_corp
   tests/prediction_markets tests/conftest.py pyproject.toml`. **MUST include `pyproject.toml`** (`asyncio_mode=auto`;
   omit it and pytest runs STRICT -> all async tests error). Compute local per-file sha256 from the SAME tarball
   (`tar -xzOf pm_p2_stage.tgz <path> | sha256sum`) for the chain-of-custody comparison.
2. `scp` the tarball; the box script: engine PID before -> box-state read (crontab/timers/port/DB) -> tarball sha
   (transport integrity) -> extract to `${HOME:-/home/azureuser}/pm_p2_scratch` (guard the path; NEVER prod) ->
   `__init__.py` present+inert proof -> per-file sha256 (box==local) -> `cat` shipped conftest -> pytest
   (`venv/bin/python -m pytest tests/prediction_markets/ -p no:pytest_ethereum`, **CWD=scratch**,
   **PM_DB_PATH=/tmp file**) -> targeted legacy-guard `-k legacy -v` -> junit line -> [optional read-only
   `mode=ro` live characterization] -> isolation proofs (no `*.db` under scratch; test DB in /tmp) -> delete
   scratch+stage + prove gone -> engine PID after == before.

## Hard rules baked in (do not remove)
- Scratch under `~/` only, path-guarded; `PM_DB_PATH` an explicit **file** not a dir; CWD=scratch (a relative DB
  default must never resolve to prod); `-p no:pytest_ethereum` (that plugin crashes collection in the box venv);
  chain-of-custody per-file sha256 box==local; legacy DB only `stat`-ed (label mtime EXPECTED-TO-DIFFER — engine
  writes it live); engine MainPID bracketed; pure-ASCII, no-BOM, `bash -n`-validated before hand-off.

## Proven
- Pre-CP1 (2026-08-23): P1 baseline 62/1/0.
- CP1 build (2026-08-23): 74/1/0; e5 real-data anchor reproduced the P1 record to the exact count (Kickstand7
  two-sided 1314/489, BetMechanic nba one-sided 1132, Kh4mz4t ufc one-sided 121).
