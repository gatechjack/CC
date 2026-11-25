# secrev_batch3_run.ps1 -- JACK RUNS THIS (az-root). Runs the staged, hash-verified secrev_batch3.sh as root.
# NEEDRESTART_MODE=l inside the script => nothing auto-restarts; only chrony/snapd may restart. NO engine/reboot.
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "bash /home/azureuser/secrev_batch3.sh" -o json
