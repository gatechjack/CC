@echo off
REM Thread C call 4 (az-split): confirm recorded filled_legs/current_sl for the 3 trades. READ-ONLY.
cd /d "%~dp0"
call az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm --command-id RunShellScript --scripts "@probe_c4.sh" --query "value[0].message" -o tsv
