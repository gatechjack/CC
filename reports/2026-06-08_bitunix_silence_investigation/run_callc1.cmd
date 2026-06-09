@echo off
REM Thread C call 1 (az-split): paper_trade_record schema discovery. READ-ONLY.
cd /d "%~dp0"
call az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm --command-id RunShellScript --scripts "@probe_c1.sh" --query "value[0].message" -o tsv
