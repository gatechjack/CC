# secrev_batch6_token_cleanup_run.ps1 -- OPTIONAL. JACK RUNS (root). Deletes only waagent run-command files containing
# "pro attach" (grep -l filenames-only; token contents never read/printed). Jack decides whether to run this.
az vm run-command invoke -g rg-shared-prod -n tc-prod-vm --command-id RunShellScript --scripts "bash /home/azureuser/secrev_batch6_token_cleanup.sh" -o json
