# secrev_batch2c2_run.ps1 -- JACK RUNS THIS (DEPLOY+RESTART reserved). PHASE 2 -- only after 2c-1 green 30+ min
# AND Jack's click-through confirmed. Executes the staged 2c-2 deploy as root:
# cryptography 50.0.2 + msal 1.39.0 into the shared prod venv (--require-hashes --no-deps), gated + freeze-diff +
# auto-restore, then restarts all 4 app services. Base gate requires 2c-1 already applied.
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "bash /home/azureuser/secrev_batch2c2_deploy.sh" -o json
