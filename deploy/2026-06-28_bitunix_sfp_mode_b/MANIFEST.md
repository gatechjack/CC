# Deploy MANIFEST — bitunix_sfp Mode B (15m SFP → 3m BOS), 2026-06-28

Branch `bitunix-sfp-mode-b-2026-06-28` @ `f795778` (off `main` @ `80f2c43`).
LF blobs come from `git show HEAD:<f> | tr -d '\r'` (NOT git archive — CRLF).

## Files + md5 (LF-normalized)

| File | PROD now (base) | TARGET (HEAD) | Deploy kind |
|------|-----------------|---------------|-------------|
| `trading_corp/agents/strategies/bitunix_sfp.py` | `5c71a103` | `91fd7672` | full blob (additive: SfpModeBDetector) |
| `trading_corp/agents/divisions/bitunix_sfp_observer.py` | `18da45f2` | `8a916526` | full blob (additive: Mode-B wiring) |
| `trading_corp/main.py` | `2c1bb1dc` | `2ff188c7` | full blob (SFP 3m caches + loop select) |
| `scripts/bitunix_prod_surface_md5diff.py` | (n/a) | `f9e2979b` | full blob (drift MANIFEST +2 SFP) |
| `config/strategies.yaml` | `0cd6e45d` | `84001f67` | full blob — **2 variants** (paper/live) |

## Drift preconditions (VERIFIED 2026-06-28, read-only)

All four currently-deployed files: **prod == `main@80f2c43`** (the branch base).
- `main.py` base LF md5 `2c1bb1dc` == prod ✓
- `strategies.yaml` base LF md5 `0cd6e45d` == prod ✓
- `bitunix_sfp.py` prod `5c71a103` == main base ✓
- `bitunix_sfp_observer.py` prod `18da45f2` == main base ✓

→ Clean diff off main; no prod-only drift any full-file blob would clobber.
Re-run the drift gate (`scripts/bitunix_prod_surface_md5diff.py`, now incl. both
SFP modules) immediately before the deploy to re-confirm.

## strategies.yaml — two variants (one key differs)

- **Restart #1 (paper dry-run):** `bitunix_sfp.execution_mode: paper`. Everything
  else identical to HEAD. With execution_mode=paper the Mode-B path runs, arms
  watches, writes paper records — but never places a live order (all 4 symbols).
- **Restart #2 (live flip):** HEAD `config/strategies.yaml` (`84001f67`,
  `execution_mode: live`). The per-symbol `arm` then takes effect: BTC/ETH
  `trading` (live), SOL/XRP `watch` (forced paper).

ExecStart `--live-divisions` is UNCHANGED (`bitunix_sfp` is already live); Mode B
is an in-division change. No NSG/cred/env change.
