# secrev_batch4_run.ps1 -- JACK RUNS THIS (az-root). Runs staged secrev_batch4_deploy.sh as root (kernel+libc+reboot).
# The box reboots ~6s after the script returns. Verify after boot with secrev_batch4_verify.ps1.
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "bash /home/azureuser/secrev_batch4_deploy.sh" -o json
