#!/usr/bin/env bash
# READ-ONLY stack read for the Caddy+Authelia wiring package. SECRETS REDACTED. Edits NOTHING, reloads NOTHING.
# cat/grep/awk/sed/systemctl-show only.
echo "=== STACK READ (Caddy + Authelia) READ-ONLY; secrets redacted ==="; date -u; echo "whoami=$(whoami)"
# redactor: blank the VALUE of any key whose name looks secret (keeps structure, hides secrets)
RED='s/^([[:space:]]*[A-Za-z0-9_]*(secret|password|passwd|token|encryption_key|private_key|hmac|salt)[A-Za-z0-9_]*[[:space:]]*:).*/\1 <REDACTED>/I'

echo ""; echo "########## CADDY (re-confirm current state) ##########"
echo "--- structure (site openers / proxy targets / imports / snippets) ---"
grep -nE 'jacksumner\.com|reverse_proxy|forward_auth|^[[:space:]]*import|^\(|:8000|:8081|:9091' /etc/caddy/Caddyfile 2>&1
echo "--- Caddyfile sha256 (so I can confirm it is unchanged since my earlier read) ---"
sha256sum /etc/caddy/Caddyfile 2>&1
echo "--- does a predictions block already exist? (expect NO) ---"
grep -c 'predictions\.jacksumner\.com' /etc/caddy/Caddyfile 2>&1

echo ""; echo "########## AUTHELIA ##########"
echo "--- [1] unit + --config path ---"
systemctl cat authelia 2>&1 | grep -iE 'ExecStart|WorkingDirectory|ExecReload' | head
CFG=$(systemctl cat authelia 2>/dev/null | grep -oE '\-\-config[ =][^ ]+' | head -1 | sed -E 's/^--config[ =]//')
if [ -z "$CFG" ]; then for c in /etc/authelia/configuration.yml /etc/authelia/config.yml /etc/authelia/configuration.yaml; do [ -f "$c" ] && CFG="$c" && break; done; fi
echo "CFG=$CFG"; ls -la /etc/authelia/ 2>&1

echo ""; echo "--- [2] access_control section (VERBATIM; not secret) ---"
echo "===== BEGIN access_control ====="
awk '/^access_control:/{f=1;print;next} f&&/^[A-Za-z]/{f=0} f{print}' "$CFG" 2>&1
echo "===== END access_control ====="

echo ""; echo "--- [3] session (domain + provider for session-survival across restart); secrets redacted ---"
awk '/^session:/{f=1;print;next} f&&/^[A-Za-z]/{f=0} f{print}' "$CFG" 2>&1 | sed -E "$RED"

echo ""; echo "--- [4] authentication_backend (file watch? + users path); secrets redacted ---"
awk '/^authentication_backend:/{f=1;print;next} f&&/^[A-Za-z]/{f=0} f{print}' "$CFG" 2>&1 | sed -E "$RED"

echo ""; echo "--- [5] storage provider (session persistence hint); secrets redacted ---"
awk '/^storage:/{f=1;print;next} f&&/^[A-Za-z]/{f=0} f{print}' "$CFG" 2>&1 | sed -E "$RED"

echo ""; echo "--- [6] USERS DB: usernames + groups ONLY (password hashes REDACTED) ---"
for u in /etc/authelia/users_database.yml /etc/authelia/users.yml /etc/authelia/users_database.yaml; do
  [ -f "$u" ] || continue
  echo "===== BEGIN $u (password lines redacted) ====="
  sed -E 's/(password[[:space:]]*:).*/\1 <REDACTED-ARGON2-HASH>/I' "$u" 2>&1
  echo "===== END users db ====="
  break
done

echo ""; echo "--- [7] how authelia reloads (systemd ExecReload / CanReload) ---"
systemctl show authelia -p ExecReload -p CanReload -p ActiveState 2>&1
echo "=== STACK READ (done) ==="
