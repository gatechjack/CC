# Batch 5 (non-Pro parts) result -- 2026-11-24 (EXECUTED)

Ran as root via az vm run-command on staged+hash-verified secrev_batch5.sh. No reboot, no engine restart.

## xrdp
- Before: active + enabled. Now: `xrdp` inactive+**masked**, `xrdp-sesman` masked (state `failed`, benign -- masked + not listening).
- **3389/3350: 0 listeners** (closed). Packages NOT purged (per Jack).
- Optional tidy: `systemctl reset-failed xrdp-sesman` to clear the cosmetic failed state.

## journald
- Drop-in `/etc/systemd/journald.conf.d/00-secrev-maxuse.conf`: `SystemMaxUse=1G`, `SystemKeepFree=2G`, `SystemMaxFileSize=100M`.
- Usage **3.9G -> 1008M** (cap applied on journald restart). Disk **84% -> 80%** (13G free).

## Protected services (unchanged)
trading-corp 1060188, prediction-markets-web 1020846, sfp-card-watcher 656, market-context-recorder 645,
caddy 1067704, authelia 1070681 -- all active; authelia /api/health 200.
