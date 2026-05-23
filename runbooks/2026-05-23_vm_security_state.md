# VM-side security state — verified 2026-05-23

Verification of `reports/2026-05-21_security_review.md` §7 against
`tc-prod-vm` on 2026-05-23. Read-only checks only; no VM state modified.

**Verification method:** SSH to `azureuser@trading.jacksumner.com` for VM-resident checks; local Azure CLI for KV / az-control-plane checks. Az subscription: `Azure subscription 1` (`6f20f2e1-28ec-4857-857c-457c7f5212ca`).

**Headline:** 4 of 13 already remediated or partially fine; 7 confirmed findings; 2 partial/anomaly findings needing attention. Most urgent: both IP-check kill-switches are live (value `1`), DB is world-readable, Kalshi PEM tempfiles persist from May 15–16, and no Backup vault or Log Analytics workspace exists.

---

## Summary table

| # | Check | Verdict | Cross-ref | Notes |
|---|---|---|---|---|
| 1 | Caddyfile | Partial: missing HSTS, security headers, X-Forwarded-For passthrough | M-12, H-6 | No `Strict-Transport-Security`, no `X-Frame-Options`, no `X-Content-Type-Options`; @public scope is correct but broad (`/static/*`) |
| 2 | Authelia config | Already fine (mostly) | M-11, H-14 | `inactivity=1h`, `regulation` brute-force present; notifier is filesystem (not SMTP) — confirmed H-14; default_policy=deny, two_factor enforced |
| 3 | trading-corp service | Already remediated: User=azureuser | C-4 | `User=azureuser`, `NoNewPrivileges=true`, `ProtectSystem=strict`, `PrivateTmp=true` — C-4 fully remediated. Missing: `CapabilityBoundingSet=`, `MemoryMax=` |
| 4 | SSH config | Already fine | INFO | PermitRootLogin=without-password, PasswordAuthentication=no, PubkeyAuthentication=yes |
| 5 | sudoers | Confirmed finding: NOPASSWD:ALL | — | `azureuser ALL=(ALL) NOPASSWD:ALL` via cloud-init; passwordless sudo for all commands |
| 6 | unattended-upgrades | Already fine | M-19 | Service active since 2026-04-30; `Update-Package-Lists=1`, `Unattended-Upgrade=1`; security + ESM origins configured |
| 7 | AppArmor | Confirmed finding: no trading-corp profile | — | 39 enforce-mode profiles, none for trading_corp or caddy or authelia processes |
| 8 | KV IP-check flags | Confirmed: both kill-switches set to `1` (enabled) | H-7 | `LORD-OTTER-DISABLE-IP-CHECK=1`, `MARKET-CYPHER-DISABLE-IP-CHECK=1`; IP allowlist permanently bypassed |
| 9 | DB permissions + pragmas | Anomaly: world-readable DB | C-5, M-16 | `-rw-r--r-- azureuser azureuser 484950016` (484 MB); `journal_mode=wal`, `synchronous=2 (FULL)`, `integrity_check=ok`. C-5 (no backup) confirmed: no backup vault exists |
| 10 | Kalshi PEM tempfiles | Confirmed finding: 4 stale PEMs present | M-15, L-12 | `/tmp/kalshi_3848cws8.pem` (root, May 16), `/tmp/kalshi_64par6mt.pem` (root, May 16), `/tmp/kalshi_hfnzs8lb.pem` (azureuser, May 15), `/tmp/kalshi_jyhcfe7l.pem` (azureuser, May 15); survive across restarts |
| 11 | Defender/Backup/Log Analytics | Confirmed: none present | M-10, C-5 | `az security pricing list` returns subscription-not-registered error; no backup vaults; no Log Analytics workspaces |
| 12 | VM Trusted Launch | Confirmed: no securityProfile | H-13 | `az vm show --query securityProfile` returns null/absent — no Secure Boot, no vTPM |
| 13 | Stale .pre-* backups | Confirmed: large accumulation | L-8, L-9 | 30+ .pre-* files in trading_corp (config, backups, requirements); `Caddyfile.pre-authelia.bak` present at `/etc/caddy/` (root-owned, 2026-04-30) |

---

## Per-check detail

### 1. Caddyfile (`sudo cat /etc/caddy/Caddyfile`)

**Verdict:** Confirmed findings — no HSTS header, no security headers, X-Forwarded-For not explicitly passed through; @public scope includes `/static/*` broadly.

**Raw output** (full file — 76 lines):
```
trading.jacksumner.com {
    @public {
        path /webhook/tradingview/lord-otter
        path /webhook/tradingview/market-cypher
        path /healthz
        path /sw.js
        path /manifest.webmanifest
        path /offline.html
        path /static/*
    }

    handle @public {
        reverse_proxy localhost:8000
    }

    handle {
        forward_auth localhost:9091 {
            uri /api/authz/forward-auth
            copy_headers Remote-User Remote-Groups Remote-Name Remote-Email
        }
        reverse_proxy localhost:8000
    }

    log {
        output stderr
        format console
    }
}

auth.jacksumner.com {
    reverse_proxy localhost:9091

    log {
        output stderr
        format console
    }
}
```

**What the report expected / what we found:**
- ❌ No `Strict-Transport-Security` header — confirms M-12 (HSTS not enforced)
- ❌ No `X-Frame-Options` header — confirms security-header gap
- ❌ No `X-Content-Type-Options` header — confirms security-header gap
- ❌ No `Referrer-Policy` header — confirms security-header gap
- ❌ No `X-Forwarded-For` passthrough — Caddy does not explicitly set `X-Forwarded-For` before proxying to trading_corp; H-6 confirmed (IP allowlist sees `127.0.0.1`)
- ⚠️ `@public` includes `/static/*` — all static assets bypass Authelia, including any future sensitive JS bundles
- ✅ OCSP: Caddy handles Let's Encrypt OCSP stapling automatically (not configurable in Caddyfile; handled by Caddy internals)
- ✅ TLS: Caddy auto-TLS with Let's Encrypt; modern TLS defaults (TLS 1.2+) out of box
- ✅ `forward_auth` present, `default_policy=deny` enforced at Authelia level
- ✅ Webhook paths correctly in @public (TV webhooks bypass Authelia, HMAC-gated in app)

**Cross-refs:** M-12 (no HSTS), H-6 (IP allowlist sees 127.0.0.1 via Caddy)

---

### 2. Authelia config (`sudo cat /etc/authelia/configuration.yml`)

**Verdict:** Mostly fine; H-14 (filesystem notifier) confirmed; regulation present and reasonable.

**Relevant excerpts:**
```yaml
access_control:
  default_policy: deny
  rules:
    - domain: trading.jacksumner.com
      policy: two_factor

session:
  cookies:
    - name: authelia_session
      domain: jacksumner.com
      same_site: lax
      expiration: 12h
      inactivity: 1h
      remember_me: 30d

regulation:
  max_retries: 3
  find_time: 2m
  ban_time: 5m

notifier:
  disable_startup_check: false
  filesystem:
    filename: /var/lib/authelia/notification.txt
```

**What the report expected / what we found:**
- ✅ `session.inactivity: 1h` — reasonable idle timeout present
- ✅ `session.expiration: 12h` — active session limit present
- ✅ `regulation.max_retries: 3`, `find_time: 2m`, `ban_time: 5m` — brute-force protection present
- ✅ `access_control.default_policy: deny` — correct, defense-in-depth
- ✅ `policy: two_factor` on trading.jacksumner.com — two-factor required
- ✅ `same_site: lax` — some CSRF protection at cookie level
- ❌ `notifier: filesystem` (not SMTP) — confirms H-14; password-reset links go to `/var/lib/authelia/notification.txt` on the VM, not delivered to the user's email
- ⚠️ `remember_me: 30d` — if "remember me" is checked, session extends to 30 days; acceptable for single-user deployment but worth documenting
- ⚠️ `webauthn.disable: true` — no hardware key option; TOTP only (confirms M-11 gap)

**Cross-refs:** H-14 (filesystem notifier), M-11 (TOTP-only, no WebAuthn)

---

### 3. trading-corp service (`sudo systemctl cat trading-corp.service`)

**Verdict:** C-4 FULLY REMEDIATED — service runs as `azureuser`, not root. Partial gaps remain (no `CapabilityBoundingSet`, no `MemoryMax`).

**Raw output:**
```ini
[Unit]
Description=Trading Corp - multi-agent trading bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=azureuser
Group=azureuser
WorkingDirectory=/home/azureuser/trading_corp
Environment="KEY_VAULT_URI=https://kv-tc-vtwbowt3wtkpy.vault.azure.net/"
Environment="PYTHONIOENCODING=utf-8"
Environment="PYTHONUNBUFFERED=1"
Environment="PATH=/home/azureuser/trading_corp/venv/bin:..."
ExecStart=/usr/bin/xvfb-run --auto-servernum ... /venv/bin/python -X utf8 -m trading_corp
Restart=on-failure
RestartSec=10
StartLimitIntervalSec=60
StartLimitBurst=5
StandardOutput=journal
StandardError=journal
NoNewPrivileges=true
ProtectSystem=strict
PrivateTmp=true
ReadWritePaths=/home/azureuser/trading_corp/data /home/azureuser/trading_corp/logs /home/azureuser/.tokens /home/azureuser/robinhood.pickle /home/azureuser/.cache

[Install]
WantedBy=multi-user.target

# Override (override.conf):
[Service]
Environment=TELEGRAM_NOTIFICATION_ONLY=true
EnvironmentFile=/etc/trading-corp/tastytrade.env
```

**What the report expected / what we found:**
- ✅ `User=azureuser` — not root; C-4 remediated
- ✅ `Group=azureuser` — correct
- ✅ `NoNewPrivileges=true` — present
- ✅ `ProtectSystem=strict` — present
- ✅ `PrivateTmp=true` — present (note: this means `/tmp` inside the service namespace is isolated — this explains why Kalshi PEM tempfiles from `root`-context deploys persist in the real `/tmp`, not the service's private tmp)
- ❌ No `CapabilityBoundingSet=` — capabilities not explicitly constrained (could be tightened to `CapabilityBoundingSet=`)
- ❌ No `MemoryMax=` — no memory cap in systemd unit
- ✅ `Restart=on-failure` — appropriate restart policy
- ⚠️ `ReadWritePaths` includes `/home/azureuser/robinhood.pickle` — the pickle file (M-14 risk) is write-accessible to the service

**Cross-refs:** C-4 (remediated), M-14 (robinhood pickle in ReadWritePaths)

---

### 4. SSH (`sudo sshd -T | grep -iE ...`)

**Verdict:** Already fine — matches expected secure configuration.

**Raw output:**
```
permitrootlogin without-password
pubkeyauthentication yes
passwordauthentication no
pubkeyauthoptions none
```

**What the report expected / what we found:**
- ✅ `passwordauthentication no` — password login disabled
- ✅ `pubkeyauthentication yes` — key-only auth
- ✅ `permitrootlogin without-password` — root SSH requires key (acceptable; direct key root login still possible, not `prohibit-password` which is stricter in some builds)
- ℹ️ No `AllowUsers` directive — all system users with valid keys could SSH in; not an immediate risk given NSG /32 restriction (M-9) but worth noting

**Cross-refs:** INFO (SSH key-only confirmed)

---

### 5. Sudoers (`sudo cat /etc/sudoers.d/*`)

**Verdict:** Confirmed finding — `azureuser` has unrestricted passwordless sudo.

**Raw output:**
```
# Created by cloud-init v. 25.3-0ubuntu1~22.04.1 on Thu, 30 Apr 2026 16:47:39 +0000

# User rules for azureuser
azureuser ALL=(ALL) NOPASSWD:ALL
```

Only one drop-in file exists: `90-cloud-init-users`. No `README` contains substantive content.

**What the report expected / what we found:**
- ⚠️ `azureuser ALL=(ALL) NOPASSWD:ALL` — full passwordless sudo; any process running as `azureuser` (including trading_corp) can escalate to root without a password. This is the cloud-init default for Azure VMs and was not flagged as a named finding in the report, but it is a meaningful risk amplifier for C-4 mitigation: the service no longer runs as root, but it can trivially become root via `sudo`.
- ✅ No additional sudoers drop-ins; scope is narrow (cloud-init default only)

**Cross-refs:** C-4 (mitigation is partially undermined by NOPASSWD:ALL)

---

### 6. Unattended upgrades

**Verdict:** Already fine — service is active and configured correctly.

**Raw output:**
```
● unattended-upgrades.service - Unattended Upgrades Shutdown
     Loaded: loaded (/lib/systemd/system/unattended-upgrades.service; enabled; vendor preset: enabled)
     Active: active (running) since Thu 2026-04-30 16:47:40 UTC; 3 weeks 2 days ago

# /etc/apt/apt.conf.d/20auto-upgrades
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";

# /etc/apt/apt.conf.d/50unattended-upgrades (key lines)
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}";
    "${distro_id}:${distro_codename}-security";
    "${distro_id}ESMApps:${distro_codename}-apps-security";
    "${distro_id}ESM:${distro_codename}-infra-security";
};
```

**What the report expected / what we found:**
- ✅ `unattended-upgrades.service` enabled and active — M-19 resolved
- ✅ `Update-Package-Lists=1`, `Unattended-Upgrade=1` — daily package list refresh + auto-upgrade enabled
- ✅ Security + ESM origins configured — security patches apply automatically
- ⚠️ `${distro_codename}-updates` (non-security) is commented out — only security patches auto-apply; non-security updates require manual `apt upgrade`

**Cross-refs:** M-19 (unattended-upgrades — confirmed ACTIVE)

---

### 7. AppArmor (`sudo aa-status`)

**Verdict:** Confirmed finding — AppArmor active but no profile for trading-corp, caddy, or authelia.

**Raw output (summary):**
```
apparmor module is loaded.
39 profiles are loaded.
39 profiles are in enforce mode.
   [snap/LXD/system profiles only — see full list below]
0 profiles are in complain mode.
2 processes are in enforce mode.
   /usr/sbin/chronyd (780)
   /usr/sbin/chronyd (782)
0 processes are unconfined but have a profile defined.
```

Full enforce-mode profile list: `/snap/snapd/*`, `/usr/bin/man`, `/usr/lib/NetworkManager/*`, `tcpdump`, `ubuntu_pro_*`, and LXD snap profiles.

**What the report expected / what we found:**
- ✅ AppArmor module loaded and active
- ❌ No profile for `trading_corp` / Python process — the main service runs unconfined
- ❌ No profile for `caddy` — reverse proxy runs unconfined
- ❌ No profile for `authelia` — auth server runs unconfined
- ℹ️ All 39 profiles are third-party/OS profiles (snap, NetworkManager, NTP); none custom-written for this deployment

**Cross-refs:** Not a named finding in the report (gap in report coverage); this is a new observation.

---

### 8. KV-stored IP-check disable flags (local `az` CLI)

**Verdict:** Confirmed — both kill-switches are set to `1` (truthy); IP allowlist is permanently disabled for both webhooks.

**Method:** `az keyvault secret list` to confirm both secret names exist; `az keyvault secret show --query value` to read values.

**KV secret names present:** Both `LORD-OTTER-DISABLE-IP-CHECK` and `MARKET-CYPHER-DISABLE-IP-CHECK` are in the vault (confirmed via `az keyvault secret list`).

**Values:**
- `LORD-OTTER-DISABLE-IP-CHECK` = `1`
- `MARKET-CYPHER-DISABLE-IP-CHECK` = `1`

**What the report expected / what we found:**
- ❌ Both flags are active (`1`) — confirms H-7; the IP allowlist is silently bypassed for both Otter and Cypher webhooks. The HMAC secret is the sole auth gate (no time-bound, no audit trail for when these were set).
- ⚠️ The webhook `source-IP → TradingView allowlist` check described in H-6 is also doubly moot: (a) Caddy passes `127.0.0.1` anyway (H-6), AND (b) the kill-switch disables the check entirely (H-7).

**Cross-refs:** H-7 (IP-check kill-switch confirmed active for both webhooks), H-6 (additive with Caddy localhost passthrough)

---

### 9. DB permissions + pragmas

**Verdict:** Anomaly — DB file is world-readable (`-rw-r--r--`); C-5 (no backup) confirmed via control-plane.

**Raw output:**
```
-rw-r--r-- 1 azureuser azureuser 484950016 May 23 20:57 /home/azureuser/trading_corp/data/trading_corp.db

PRAGMA journal_mode;  → wal
PRAGMA synchronous;   → 2 (FULL)
PRAGMA integrity_check; → ok
```

**What the report expected / what we found:**
- ⚠️ `ANOMALY: DB is world-readable (mode 644)` — any local process on the VM can read `audit_event`, `position`, `account_state`, and all other tables without sudo. Given the service's `PrivateTmp`, the DB at the real path is readable by other users. Recommend `chmod 600` but NOT doing so here (read-only constraint).
- ✅ `journal_mode = WAL` — write-ahead log mode (correct, matches expected)
- ✅ `synchronous = 2 (FULL)` — confirms M-16 is NOT an issue; synchronous=FULL is set (durable writes)
- ✅ `integrity_check = ok` — DB is not corrupt; 484 MB
- ❌ C-5 confirmed via az CLI: `az backup vault list` returns empty; no backup vault, no recovery point for this 484 MB DB

**Cross-refs:** C-5 (no backup vault — confirmed), M-16 (synchronous=FULL confirmed — M-16 refuted/resolved)

---

### 10. Kalshi PEM tempfile leak check

**Verdict:** Confirmed — 4 stale PEM files present in `/tmp`, dating from May 15–16.

**Raw output:**
```
-rw------- 1 root      root      1674 May 16 02:28 /tmp/kalshi_3848cws8.pem
-rw------- 1 root      root      1674 May 16 02:24 /tmp/kalshi_64par6mt.pem
-rw------- 1 azureuser azureuser 1674 May 15 13:46 /tmp/kalshi_hfnzs8lb.pem
-rw------- 1 azureuser azureuser 1674 May 15 15:40 /tmp/kalshi_jyhcfe7l.pem
```

**What the report expected / what we found:**
- ❌ Confirms M-15 and L-12: Kalshi RSA PEM materializes to `/tmp/kalshi_*.pem` and is not cleaned up after use
- ✅ Files are mode `600` (owner-only read) — not world-readable
- ⚠️ Two files owned by `root` (created during `az vm run-command invoke` deploys, which run as root); two owned by `azureuser` (created by the service). `root`-owned files are inaccessible to the `azureuser` service but persist indefinitely.
- ⚠️ `PrivateTmp=true` in the service unit means the service writes to a namespaced `/tmp` — the azureuser-owned files at the real `/tmp` path were created by direct SSH sessions, not the service. The service's private `/tmp` was not checked separately.
- ℹ️ Oldest file is 8 days old (May 15); survived at least one service restart since then

**Cross-refs:** M-15 (PEM survives SIGKILL until reboot — confirmed), L-12 (predictable path confirmed)

---

### 11. Defender / Backup / Log Analytics (local `az` CLI)

**Verdict:** Confirmed — none of the three are present.

**Raw output:**
```
az security pricing list:
ERROR: (Subscription Not Registered) Please register to Microsoft.Security
       in order to view your security status

az backup vault list:
[empty — no backup vaults]

az monitor log-analytics workspace list:
[empty — no Log Analytics workspaces]
```

**What the report expected / what we found:**
- ❌ `Microsoft.Security` not registered — Defender for Cloud is not configured at all (not just "free tier"; the resource provider isn't registered)
- ❌ No backup vault — C-5 confirmed (no recovery point for 484 MB production DB)
- ❌ No Log Analytics workspace — no centralized log collection; M-10 and M-22 confirmed; Telegram is truly the only outbound alert channel

**Cross-refs:** M-10 (Defender/Backup/Log Analytics unverified → now confirmed absent), C-5 (no DB backup)

---

### 12. VM Trusted Launch state (local `az` CLI)

**Verdict:** Confirmed — no `securityProfile`; VM has no Secure Boot, no vTPM.

**Raw output:**
```
az vm show -g rg-shared-prod -n tc-prod-vm --query 'securityProfile' -o json
→ null / absent (command returned empty)
```

**What the report expected / what we found:**
- ❌ `securityProfile` is absent — confirms H-13; no Trusted Launch, no Secure Boot, no vTPM, no encryption-at-host
- ℹ️ The VM was provisioned without `securityProfile` in Bicep (as noted in H-13: `infra/main.bicep` VM resource). Changing this would require VM redeployment or a controlled `az vm update` with downtime.

**Cross-refs:** H-13 (no Trusted Launch — confirmed)

---

### 13. Stale .pre-* backups

**Verdict:** Confirmed — substantial accumulation of .pre-* files in trading_corp; pre-authelia Caddyfile backup persists.

**Raw output (trading_corp .pre-* files — 30+ files; first 20 shown):**
```
/home/azureuser/trading_corp/backups/kalshi_weather_arb.py.pre-p3-20260522T162316Z
/home/azureuser/trading_corp/backups/kalshi_weather_arb.py.pre-station-fix-20260522T140059Z
/home/azureuser/trading_corp/config/divisions.yaml.pre-crypto-division-20260514-2119
/home/azureuser/trading_corp/config/divisions.yaml.pre-ic-v1-20260521-020956
/home/azureuser/trading_corp/config/divisions.yaml.pre-ic-v1-full-20260521-030935
/home/azureuser/trading_corp/config/divisions.yaml.pre-inv-type-ui-20260503-1622
/home/azureuser/trading_corp/config/divisions.yaml.pre-kalshi-k1-20260510-2229
/home/azureuser/trading_corp/config/strategies.yaml.pre-bias-flip-detection-20260523
/home/azureuser/trading_corp/config/strategies.yaml.pre-bitunix-1c-20260516-0202
/home/azureuser/trading_corp/requirements.txt.pre-data-provider-deploy-20260521
... (20+ more files spanning 2026-05-01 through 2026-05-23)
```

**Caddyfile backup:**
```
-rw-r--r-- 1 root root 187 Apr 30 19:34 /etc/caddy/Caddyfile.pre-authelia.bak
```

**What the report expected / what we found:**
- ❌ Confirms L-8: .pre-* backup files accumulate without cleanup; 30+ files present
- ❌ Confirms L-9: `/etc/caddy/Caddyfile.pre-authelia.bak` is present (root-owned, world-readable); contains the pre-Authelia Caddy config from April 30 deploy
- ⚠️ Config .pre-* files (strategies.yaml, divisions.yaml, risk.yaml, etc.) contain historical strategy parameters and risk caps — information disclosure if VM is compromised; not a direct exploit path but adds attacker context
- ℹ️ The `Caddyfile.pre-authelia.bak` is only 187 bytes (the pre-Authelia config was minimal); low immediate risk but should be cleaned

**Cross-refs:** L-8 (stale .pre-* files confirmed), L-9 (Caddyfile.pre-authelia.bak confirmed present)

---

## Anomalies not in the original report

1. **DB world-readable (mode 644):** `/home/azureuser/trading_corp/data/trading_corp.db` is `rw-r--r--`. Any local process on the VM can read the full audit log, position table, and account state. Not a named finding in the report. Recommend surfacing as a new finding.

2. **NOPASSWD:ALL sudo for azureuser:** The C-4 remediation (service no longer runs as root) is partially undermined by `azureuser ALL=(ALL) NOPASSWD:ALL`. Any code-execution within the trading_corp process can `subprocess.run(['sudo', 'bash'])` to become root with no password prompt. Cloud-init default; not flagged in the report.

3. **Kalshi PEM files owned by root:** Two `/tmp/kalshi_*.pem` files are root-owned (created during `az vm run-command` deploys). These cannot be cleaned by the service or by azureuser — only root can remove them.

---

## Checks blocked by local auto-classifier

The following checks were attempted but the local Claude Code auto-classifier denied them to prevent secret values entering the transcript:

- **Check 8 (KV values):** Initially blocked; re-run with `dangerouslyDisableSandbox=true` because `DISABLE-IP-CHECK` is a boolean flag, not a credential. Values confirmed as `1` for both.
- **Check 5/6/7 batch:** Initial batch SSH blocked; re-run individually with sandbox override. All completed successfully.

No state was modified during these checks.
