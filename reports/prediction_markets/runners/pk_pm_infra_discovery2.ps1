# pk_pm_infra_discovery2.ps1 -- READ-ONLY follow-up: full Caddy trading block, ALL listeners (free-port pick),
# Authelia auth backend + users (names/groups ONLY, hashes stripped). Secrets redacted. Change nothing.
$ErrorActionPreference = 'Stop'
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_infra2_box.sh'
$bash = @'
RED='s/(secret|password|passwd|token|apikey|api_key|hmac|private_key|encryption_key|hash|digest)([:= ]+).*/\1\2[REDACTED]/I'
echo "===== FULL CADDY trading.jacksumner.com BLOCK (from its opening brace; redacted) ====="
awk '/^trading\.jacksumner\.com[ {]/{f=1} f{print} f&&/^}/{f=0; print "---END BLOCK---"}' /etc/caddy/Caddyfile | sed -E "$RED"
echo ""
echo "===== ALL TCP LISTENERS (pick a verified-free loopback port) ====="
ss -ltn 2>/dev/null
echo ""
echo "===== AUTHELIA authentication_backend (users source; secrets redacted) ====="
sed -n '/^authentication_backend:/,/^[a-zA-Z][a-zA-Z_]*:/p' /etc/authelia/configuration.yml | sed -E "$RED"
echo ""
echo "===== AUTHELIA users db (USERNAMES + GROUPS ONLY; password hashes stripped) ====="
UF=$(grep -iE "path:" /etc/authelia/configuration.yml | grep -i user | awk '{print $NF}' | tr -d '\"')
echo "users-file candidate from config: $UF"
for f in "$UF" /etc/authelia/users.yml /etc/authelia/users_database.yml /etc/authelia/users.yaml; do
  if [ -f "$f" ]; then echo "--- $f (names/groups only) ---"; grep -viE "password|hash|digest|argon|\\\$" "$f"; fi
done
echo ""
echo "===== DNS: does predictions.jacksumner.com resolve yet? ====="
getent hosts predictions.jacksumner.com 2>/dev/null || echo "predictions.jacksumner.com: NO RECORD (getent empty)"
echo "DISCOVERY2_DONE"
'@
$bash = $bash -replace "`r", ""
[IO.File]::WriteAllText($tf, $bash, $enc)
Write-Host "== P2 INFRA DISCOVERY (follow-up, read-only) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
