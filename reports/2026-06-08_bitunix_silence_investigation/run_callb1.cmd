@echo off
REM Thread B call 1 (az-split): service state + healthz (hard-stop gate). READ-ONLY.
cd /d "%~dp0"
call az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm --command-id RunShellScript --scripts "@probe_b1.sh" --query "value[0].message" -o tsv
