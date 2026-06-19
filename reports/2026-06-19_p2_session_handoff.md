# Session handoff — 2026-06-19 (/tpsl/ TP-leg legfix + P2 classifier + maker/taker + yellow_x)

**Branch:** `bitunix-tpsl-rebuild-2026-06-18` · tip **`4f2068c`** · pushed, **UNMERGED to main**.
**Prod engine:** PID **3065623** (active), running the combined-P2 code. Flat, no halt.

## What happened this session (in order)
1. **Section-B verification** of the native `/tpsl/` bracket rebuild (report `c8a426d`). Found the rebuild's
   TP ladder FAILS: `place_tpsl_order` crashed all 3 legs on live trade `cb6b4d4a` with
   `AttributeError: 'list' object has no attribute 'get'` (venue returns a LIST; code assumed a dict).
   Position-SL/B1-coexistence/404-fix all PASSED.
2. **TP-leg fix** `8d3d164`: `_extract_tpsl_order_id` (dict+list defensive parse) + `BitunixUntrackedTpslOrder`
   hardening + `bracket_tp_leg_untracked` audit. → packaged `833ec95` → **DEPLOYED + VERIFIED LIVE 16:29**
   (PID 3046486).
3. **P2 classifier diagnosis** `fa4eece`: the live auto-book hard-coded `result='loss'` + `exit_kind='stop'`
   ignoring the correct PnL → 2 live wins mis-signed (`e1758fc9` +0.035, `7d1a78dc` +0.298). Live-only (paper
   path correct).
4. **P2 fix build** `d83e877`: pure `classify_result` (NET-else-gross sign) + `classify_exit_kind` (order-id
   match → tp/stop, else price, else `'unknown'` — never default `'stop'`); maker/taker recording (`roleType`
   → `FillEvent.role`/`$.entry_role`/`$.exit_role`/`$.maker_taker_mix`); `mc_a_yellow_x` declassified from the
   config; + record-correction dry-run package.
5. **Combined P2 redeploy** `dd9016a` (5-file delta on the legfix, incl. a **§4 Board-override for `models.py`**
   coupled with `bitunix.py`) → **DEPLOYED + VERIFIED LIVE 22:13** (PID 3065623, clean boot, no FillEvent/role
   error).
6. **yellow_x config edit** `4f2068c` → **APPLIED** to prod (`strategies.yaml` 569c38f8 → 3cc3689a, targeted,
   byte-precise) — **INERT until the next restart**.
7. Cleaned up 5 prod deploy staging dirs.

## Prod state (verified)
- **Code:** combined-P2 (classifier + maker/taker + legfix) LIVE on PID 3065623. 5 files at target md5
  (`bitunix.py 3f68473a` / `observer a31a10f1` / `reconciler bd06ea28` / `bitunix_bracket.py f4be4e9b` /
  `models.py d7561d3c`). `bitunix_exceptions.py 62ddd11c` (legfix). main.py `f16e9c24` / db.py `a2c2ff46`
  untouched.
- **Config:** `strategies.yaml` **`3cc3689a`** (yellow_x removed) — **but the engine still has yellow_x ACTIVE
  until the next restart.** execution_mode live, DD-cap 0.99, B2 OFF, staleness gate ON — preserved.
- **Backups on prod:** `*.bak-pre-tpsl-legfix-2026-06-19`, `*.bak-pre-p2-combined-2026-06-19`,
  `strategies.yaml.bak-pre-yellowx-2026-06-19`.

## Environment sync
- **repo ↔ origin:** in sync — branch tip `4f2068c` pushed, working tree clean, 0 ahead.
- **repo ↔ prod (code):** prod runs the `d83e877` code (deployed via the drift-gated packages); the branch
  holds that code + the deploy packages. Consistent.
- **repo ↔ prod (config):** prod `strategies.yaml` `3cc3689a` is a *targeted live edit* and is NOT byte-equal
  to any repo config blob (prod config has always been operator-managed/drifted from the repo branch config).
  Expected. The yellow_x change is the only intentional config delta.
- **branch ↔ main:** UNMERGED. `runbooks/deploy_log.md` on the branch is behind main (missing 06-16/06-17/06-18
  entries) — flagged in the 2026-06-19 deploy_log entry; reconcile on merge.

## Outstanding (next session) — see BACKLOG.md "P2/P3 … follow-ups (filed 2026-06-19)"
1. **VERIFY-B** on the first live trade: tracked TP legs (no `'list'` crash), a win → `result=win` + order-id
   `exit_kind`, `FillEvent(role=)` + role telemetry. No trade fired this session.
2. **Restart** to activate the yellow_x config (then verify 0 bull points + clean boot).
3. **Record correction** (`deploy/2026-06-19_p2_record_correction/`): operator runs `backup.sql` → `apply.sql`
   (2 records, label-only, PnL untouched).
4. **Latent (P3):** `exit_method='server_side_sl_B1'` + `place_position_tpsl` same-pattern hard-codes.

## Gotchas worth carrying forward
- **127-char `az` line breaks on PowerShell paste** at the wrap point → always hand a short file + streamer
  (`Desktop\restart_tc.ps1` + `iex (gc … -Raw)`), never the raw long line.
- **`grep -c $'\r'` is unreliable in Git Bash** (matches every line). Use `tr -cd '\r' | wc -c`, or compare
  as-is-md5 vs LF-md5, to check line endings.
- **Auto-mode classifier blocks live config/prod writes on ambiguous authorization** (a bare "2" was rejected;
  needed an explicit "board approved"). For prod writes, get explicit per-action go.
- **`models.py` is normally forbidden** — only in this package via a one-time §4 Board-override (FillEvent.role
  additive, coupled with bitunix.py). Don't treat it as routinely deployable.
