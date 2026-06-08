@echo off
REM Thread A Round 1, call 2 (az-split): A2 + A2b. READ-ONLY.
cd /d "%~dp0"
call az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm --command-id RunShellScript --scripts "@probe_a2.sh" --query "value[0].message" -o tsv
