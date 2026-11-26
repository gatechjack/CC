# secrev_batch2b_run.ps1 -- JACK RUNS THIS (DEPLOY+RESTART reserved). Runs staged secrev_batch2b_deploy.sh as root.
# pip install pyjwt 2.15.1 + urllib3 2.8.0 + pillow 12.3.0 into prod venv + ONE engine restart. Gated + auto-restore.
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "bash /home/azureuser/secrev_batch2b_deploy.sh" -o json
