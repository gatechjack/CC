az vm run-command invoke `
  --resource-group rg-shared-prod `
  --name tc-prod-vm `
  --command-id RunShellScript `
  --scripts "@deploy/sports_arb_observer/_patch_cap.sh" `
  --query "value[0].message" `
  --output tsv
