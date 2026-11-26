# secrev_batch7_rollback_run.ps1 -- JACK RUNS (root). Rolls back Batch 7 by reinstalling the exact purged versions.
# The <TS> is printed by the deploy as "secrev_batch7_reinstall_<TS>.txt"; this auto-picks the latest. Alternative =
# restore the pre-Batch-7 OS snapshot.
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "bash /home/azureuser/secrev_batch7_rollback.sh `$(ls -t /home/azureuser/secrev_batch7_reinstall_*.txt 2>/dev/null | head -1)" -o json
