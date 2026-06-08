@echo off
REM Thread A Round 1, call 1 (az-split): connectivity + A1 + A5. READ-ONLY.
cd /d "%~dp0"
call az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm --command-id RunShellScript --scripts "@probe_a1.sh" --query "value[0].message" -o tsv
