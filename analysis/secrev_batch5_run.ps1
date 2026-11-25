# secrev_batch5_run.ps1 -- JACK RUNS THIS (az-root). Runs staged, hash-verified secrev_batch5.sh as root.
# xrdp stop/disable/mask + journald cap/vacuum. Touches NO trading service. No reboot, no engine restart.
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "bash /home/azureuser/secrev_batch5.sh" -o json
