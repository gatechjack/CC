# secrev_housekeeping_run.ps1 -- JACK RUNS THIS (az-root). Runs staged secrev_housekeeping.sh as root.
# journald 1G->2G + pm_* Tier A delete (exact list, abort-on-newer) + reset-failed xrdp-sesman.
# NO engine restart, NO reboot, NO snapshot deletion. Protected-service PIDs recorded before/after.
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "bash /home/azureuser/secrev_housekeeping.sh" -o json
