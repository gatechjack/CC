@echo off
REM Thread C call 3 (az-split): extra_json key enumeration (sim_filled_legs/sim_r) + lengths. READ-ONLY.
cd /d "%~dp0"
call az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm --command-id RunShellScript --scripts "@probe_c3.sh" --query "value[0].message" -o tsv
