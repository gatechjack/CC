@echo off
REM Thread B call 3 (az-split): B3 Robinhood pickle state, isolated. READ-ONLY.
cd /d "%~dp0"
call az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm --command-id RunShellScript --scripts "@probe_b3.sh" --query "value[0].message" -o tsv
