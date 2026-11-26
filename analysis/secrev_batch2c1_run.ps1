# secrev_batch2c1_run.ps1 -- JACK RUNS THIS (DEPLOY+RESTART reserved). Executes the staged 2c-1 deploy as root.
# aiohttp 3.14.5 + yarl 1.25.1 + fastapi 0.165.0 + starlette 1.10.0 + opentelemetry-api 1.45.2 into the shared
# prod venv (--require-hashes --no-deps), gated + freeze-diff + auto-restore, then restarts all 4 app services.
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "bash /home/azureuser/secrev_batch2c1_deploy.sh" -o json
