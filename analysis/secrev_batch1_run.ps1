# secrev_batch1_run.ps1 -- JACK RUNS THIS. Root execution of the staged, hash-verified Batch 1 script.
# Runs `bash /home/azureuser/secrev_batch1.sh` as root via the Azure VM agent (az vm run-command).
# Trivially-simple single-line --scripts (no embedded double-quotes, no $). Prints the script's output.
# Does NOT restart trading-corp. If anything fails, the script aborts fail-safe; rollback is a separate runner.
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "bash /home/azureuser/secrev_batch1.sh" --query "value[0].message" -o tsv
