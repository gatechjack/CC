# secrev_batch6a_run.ps1 -- JACK RUNS (root). Batch 6a: install the 25 esm-apps security updates (NEEDRESTART_MODE=l,
# no service restart). Take a snapshot first (see report). Gated: dry-run no-remove/no-sensitive + 6-PID-unchanged check.
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "bash /home/azureuser/secrev_batch6a_deploy.sh" -o json
