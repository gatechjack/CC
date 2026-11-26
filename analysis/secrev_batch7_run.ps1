# secrev_batch7_run.ps1 -- JACK RUNS (root). Batch 7 desktop-leftovers purge (381 pkgs). TAKE A SNAPSHOT FIRST:
#   az snapshot create -g rg-shared-prod -n tc-prod-os-<date> --source <tc-prod-vm OS disk id>
# Gated: list-sha256, KEEP-present, dry-run==list (abort on extra/KEEP), NEEDRESTART_MODE=l purge, 6-PID-unchanged,
# Xvfb :99 still up, reboot-required absent, firefox/Xvfb no-new-unresolved-libs. Saves an exact-version reinstall list.
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "bash /home/azureuser/secrev_batch7_deploy.sh" -o json
