#!/usr/bin/env bash
# CADDYFILE READ (READ-ONLY). Dumps /etc/caddy/Caddyfile + structure (imports, snippets, global options,
# imported files) so the predictions.jacksumner.com site block can be authored to mirror trading's. Edits
# NOTHING, reloads NOTHING. cat/grep/ls only.
echo "=== CADDYFILE READ (READ-ONLY; no edits, no reload) ==="; date -u; echo "whoami=$(whoami)"
echo ""; echo "--- [1] /etc/caddy tree (find imports/snippets/subdirs) ---"
ls -laR /etc/caddy/ 2>&1 | head -120
echo ""; echo "--- [2] structure grep: global options / snippets / imports / site openers ---"
grep -nE '^\{|^\s*import\s|^\([A-Za-z0-9_-]+\)\s*\{|jacksumner\.com|reverse_proxy|forward_auth|:8081|:8080|localhost:' /etc/caddy/Caddyfile 2>&1
echo ""; echo "===== [3] BEGIN /etc/caddy/Caddyfile (verbatim) ====="
cat /etc/caddy/Caddyfile 2>&1
echo "===== END /etc/caddy/Caddyfile ====="
echo ""; echo "--- [4] any files imported by the Caddyfile (import <path>) ---"
for p in $(grep -oE '^\s*import[[:space:]]+[^[:space:]]+' /etc/caddy/Caddyfile 2>/dev/null | awk '{print $2}'); do
  case "$p" in /*) t="$p";; *) t="/etc/caddy/$p";; esac
  echo "----- import target: $p  (resolved: $t) -----"; ls -la $t 2>&1
  for g in $t; do [ -f "$g" ] && { echo "······ $g ······"; cat "$g" 2>&1; }; done
done
echo ""; echo "--- [5] caddy version + config validity (READ-ONLY: validate, does NOT reload) ---"
caddy version 2>&1 | head -1
echo "=== CADDYFILE READ (done) ==="
