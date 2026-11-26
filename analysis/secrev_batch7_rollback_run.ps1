# secrev_batch7_rollback_run.ps1 -- JACK RUNS (root). Rolls back Batch 7 by reinstalling the exact purged versions.
# The script AUTO-SELECTS the latest /home/azureuser/secrev_batch7_reinstall_*.txt (prints which; aborts if none).
# Alternative rollback = restore the pre-Batch-7 OS snapshot tc-prod-os-20261126.
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "bash /home/azureuser/secrev_batch7_rollback.sh" -o json
