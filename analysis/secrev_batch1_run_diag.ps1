# secrev_batch1_run_diag.ps1 -- JACK RUNS THIS. Diagnostic + root execution of Batch 1 with FULL output.
# (1) proves az vm run-command runs as root on tc-prod-vm (trivial echo), then
# (2) runs the staged, hash-verified bash /home/azureuser/secrev_batch1.sh -- FULL JSON so no error is hidden.
# Does NOT restart trading-corp. Fail-safe script (validate-before-install caddy; sha256+validate-config authelia).
Write-Output "========== PROBE: az vm run-command as root =========="
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "echo RC_OK; id -u; hostname" -o json
Write-Output ""
Write-Output "========== BATCH 1: bash /home/azureuser/secrev_batch1.sh (full JSON) =========="
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "bash /home/azureuser/secrev_batch1.sh" -o json
