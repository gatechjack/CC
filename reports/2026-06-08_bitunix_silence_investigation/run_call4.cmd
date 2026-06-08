@echo off
REM Thread A Round 2, call 4 (az-split): regime distribution + gate verdict (H1a-vs-H1b). READ-ONLY.
cd /d "%~dp0"
call az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm --command-id RunShellScript --scripts "@probe_a4.sh" --query "value[0].message" -o tsv
