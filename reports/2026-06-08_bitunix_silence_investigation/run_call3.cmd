@echo off
REM Thread A Round 2, call 3 (az-split): HTF gate verdicts H1-vs-H2. READ-ONLY.
cd /d "%~dp0"
call az vm run-command invoke --resource-group rg-shared-prod --name tc-prod-vm --command-id RunShellScript --scripts "@probe_a3.sh" --query "value[0].message" -o tsv
