@echo off
REM Thread C call 2 (az-split): recorded core fields + extra_json (sim) for the 3 trades. READ-ONLY.
cd /d "%~dp0"
call az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm --command-id RunShellScript --scripts "@probe_c2.sh" --query "value[0].message" -o tsv
