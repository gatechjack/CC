# Apply Package MANIFEST — bitunix_sfp (2026-06-25)

Staged on branch `bitunix-sfp-division-2026-06-25`. Pre-deploy gates GREEN (full suite 28 baseline + 38 SFP,
zero new regressions, PARITY + k=1 passing; Gate-A all prod blobs == pins; manifest change = strategies.yaml
only). Sizing: `risk_pct_real=0.0025`, `risk_pct_considerable=0.0025`, `leverage=2.0` (operator-confirmed).
**Operator runs every prod write/restart/root step. Agent drives read-only SSH only.**

## Package contents
- `staged/` — 7 LF target files at prod-relative paths (scp to `~/sfp_staged` on prod).
- `apply_paper.sh` — RESTART ① apply (Gate-A/Gate-B md5, backups, atomic swap, py_compile, **no restart**).
- `RUNBOOK.md` — full procedure.

## File map + md5 (prod = `~/trading_corp`)
| file | method | Gate-A base (prod-pre) | Gate-B target (post) |
|---|---|---|---|
| `trading_corp/main.py` | splice (8 hunks onto prod blob) | `ec7bd696…` | `2b504cbc…` |
| `config/strategies.yaml` | append bitunix_sfp block (paper) | `36f5b323…` | `930a146f…` |
| `config/divisions.yaml` | insert bitunix_sfp entry | `090174da…` | `6dcbe16f…` |
| `trading_corp/utils/divisions.py` | copy (prod==base) | `2ef1e3e8…` | `91b09f50…` |
| `trading_corp/brokers/bitunix_symbols.py` | copy (prod==base) | `aa770082…` | `4d5f87ee…` |
| `trading_corp/agents/strategies/bitunix_sfp.py` | new (clean add) | — (absent) | `ad8e36f5…` |
| `trading_corp/agents/divisions/bitunix_sfp_observer.py` | new (clean add) | — (absent) | `b2b856be…` |

main.py was spliced by `patch` onto the live prod blob (prod is ahead of main — PEAD + bitunix fixes); all 8
hunks applied with line-offset, py_compile OK, diff vs prod blob = exactly my 133 changed lines (no PEAD
content disturbed). strategies.yaml appended (patch context failed because PEAD edited robinhood_pead on
prod — append is position-independent). All staged files verified pure LF (0 CR bytes).

## STEP 2 — RESTART ① (paper dry-run)  [operator runs the writes]
1. (operator) `scp -r staged/* azureuser@trading.jacksumner.com:~/sfp_staged/` ; `scp apply_paper.sh azureuser@…:~/`
2. (operator) `ssh … bash ~/apply_paper.sh` — Gate-A → backup `*.bak-pre-sfp-2026-06-25` → atomic swap → Gate-B → py_compile. Aborts on any md5 mismatch (prod write only if all gates pass).
3. (operator) `ssh … sudo -n systemctl restart trading-corp` (SSH-NOPASSWD). **bitunix_futures stays LIVE; bitunix_sfp = paper. ExecStart UNCHANGED at this step.**
4. (agent, read-only) BOOT SMOKE ①: `journalctl` for `bitunix_sfp observer wired: … execution_mode=paper` + `bitunix_sfp 15m loop spawned`; 15m cache primed (`last_refresh_count >= 101`); boot-guard passed (only futures live); a fired signal → `bitunix_sfp` PAPER `paper_trade_record` row with `json_extract(extra_json,'$.sfp_mode')` ∈ {REAL,CONSIDERABLE}; no SFP-loop errors. **STOP for operator confirm.**

## STEP 3 — RESTART ② (live flip)  [only after ① confirmed clean AND account FLAT + reconciler clean]
Exact edits on the ①-deployed prod files (strategies.yaml will then be `930a146f…`):
- **strategies.yaml line 1022:** `bitunix_futures … execution_mode: live → paper`
- **strategies.yaml line 1931:** `bitunix_sfp … execution_mode: paper → live`
  (these are the ONLY two bitunix `execution_mode` lines; agent will produce a Gate-A `930a146f…`/Gate-B
  apply_live.sh after ① confirms, so the flip is md5-gated like ①.)
- **ExecStart (ROOT, `az vm run-command`):** `/etc/systemd/system/trading-corp.service` `--live-divisions`:
  remove `bitunix_futures`, add `bitunix_sfp` (keep `robinhood_pead`) → `--live --brokers bitunix
  --live-divisions bitunix_sfp robinhood_pead`. Back up the unit first. No cred/env change (Option B).
- (operator) `sudo -n systemctl daemon-reload && sudo -n systemctl restart trading-corp`.
- (agent, read-only) BOOT SMOKE ②: `bitunix_sfp` broker `paper=False` AND `bitunix_futures` `paper=True`
  (half-flip marker); per-account reconciler bound to SFP startup-clean; boot-guard passed (exactly one
  bitunix live); flat; SFP loop online; 4-coin 15m+3m record-only caches archiving.

## Rollback
`apply_paper.sh` aborts before any write on a gate mismatch. Post-deploy: restore
`*.bak-pre-sfp-2026-06-25` for the 5 existing files, delete the 2 new modules, restore the unit backup,
`daemon-reload` + restart. Or pre-live: set `bitunix_sfp.auto_execute: false` (hot kill switch) /
`execution_mode: paper` (restart).
