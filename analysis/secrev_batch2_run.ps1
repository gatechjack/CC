# secrev_batch2_run.ps1 -- JACK RUNS THIS (az-root). Runs the staged, hash-verified secrev_batch2_deploy.sh as root.
# Drift-gate + quiet-60s gate + backup + python3.12 debs + starlette 1.0.1 + graft main.py (bind 127.0.0.1) + ONE restart.
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "bash /home/azureuser/secrev_batch2_deploy.sh" -o json
