# pk_pm_infra_discovery.ps1 -- READ-ONLY P2 infra discovery: reverse proxy, Authelia wiring + access_control,
# loopback ports, TLS/ACME, engine systemd unit, DNS. Direct-echo (NO box /tmp writes); secrets redacted.
# Run: powershell -ep bypass -f .\pk_pm_infra_discovery.ps1
$ErrorActionPreference = 'Stop'
$enc = New-Object Text.UTF8Encoding($false)
$tf = Join-Path $env:TEMP 'pk_infra_box.sh'
$bash = @'
RED='s/(secret|password|passwd|token|apikey|api_key|hmac|private_key|encryption_key)([:= ]+).*/\1\2[REDACTED]/I'
echo "===== (a) REVERSE PROXY PRESENT ====="
for s in nginx caddy apache2 haproxy traefik docker; do printf "%s=" "$s"; (systemctl is-active "$s" 2>/dev/null || echo absent); done
echo "-- binaries --"; command -v nginx caddy 2>/dev/null
echo "-- nginx dirs --"; ls -la /etc/nginx/sites-enabled /etc/nginx/conf.d 2>/dev/null
echo "-- caddy --"; ls -la /etc/caddy 2>/dev/null
echo "-- docker ps --"; docker ps --format '{{.Names}}  {{.Image}}  {{.Ports}}' 2>/dev/null
echo ""
echo "===== (a/b) TRADING VHOST (files mentioning trading.jacksumner.com) ====="
HITS=$(grep -rl "trading.jacksumner.com" /etc/nginx /etc/caddy 2>/dev/null)
echo "$HITS"
for f in $HITS; do echo "======== $f ========"; sed -E "$RED" "$f"; done
echo ""
echo "===== (b/c) AUTHELIA CONFIG + ACCESS_CONTROL (secrets redacted) ====="
ACFG=$(find /etc /opt /srv /home/azureuser -maxdepth 6 -iname "configuration.yml" -path "*authelia*" 2>/dev/null)
echo "config candidates: $ACFG"
for c in $(docker ps --format '{{.Names}}' 2>/dev/null | grep -i authelia); do
  echo "-- docker[$c] configuration.yml (access_control + session, redacted) --"
  docker exec "$c" sh -c 'cat /config/configuration.yml 2>/dev/null || cat /etc/authelia/configuration.yml 2>/dev/null' 2>/dev/null | sed -E "$RED" | sed -n '/^access_control:/,/^[a-zA-Z]/p'
  docker exec "$c" sh -c 'cat /config/configuration.yml 2>/dev/null || cat /etc/authelia/configuration.yml 2>/dev/null' 2>/dev/null | grep -nE "^\s*domain:|default_policy" | sed -E "$RED"
done
for f in $ACFG; do
  echo "======== $f :: session domain(s) + default_policy ========"; grep -nE "^\s*domain:|default_policy" "$f" | sed -E "$RED"
  echo "======== $f :: access_control block (redacted) ========"; sed -n '/^access_control:/,/^[a-zA-Z][a-zA-Z_]*:/p' "$f" | sed -E "$RED"
done
echo ""
echo "===== (d) LOOPBACK LISTENERS (find a free port) ====="
ss -ltnp 2>/dev/null | grep -E "127.0.0.1:|\[::1\]:" || ss -ltn 2>/dev/null | head -40
echo ""
echo "===== (e) TLS / ACME ====="
command -v certbot 2>/dev/null && echo "(certbot present)"
ls -la /etc/letsencrypt/live 2>/dev/null
echo "-- caddy auto-TLS? (Caddyfile tls / on-demand) --"; grep -nE "tls|acme|on_demand" /etc/caddy/Caddyfile 2>/dev/null | sed -E "$RED"
echo ""
echo "===== (f) ENGINE SYSTEMD UNIT (house style; redacted) ====="
systemctl cat trading-corp.service 2>/dev/null | sed -E "$RED"
echo ""
echo "===== DNS RESOLVE (from box) ====="
getent hosts trading.jacksumner.com predictions.jacksumner.com 2>/dev/null || echo "(getent n/a)"
echo "DISCOVERY_DONE"
'@
$bash = $bash -replace "`r", ""
[IO.File]::WriteAllText($tf, $bash, $enc)
Write-Host "== P2 INFRA DISCOVERY (read-only) =="
az vm run-command invoke -g RG-SHARED-PROD -n tc-prod-vm --command-id RunShellScript --scripts "@$tf" --query "value[0].message" -o tsv
