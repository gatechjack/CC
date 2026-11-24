---
type: current-state
domain: trading-corp
division: platform
source_path: "vm:/home/azureuser/trading_corp"
source_commit: cf0353818694c7853a14ef14d62be537179a0ec1
last_verified: 2026-11-23
tags: [current-state, platform, security, patch-review, unverified]
---

# Security Patch Review -- 2026-11-23

> PHASE 1 = ASSESS ONLY. Nothing on the box was changed: no apt, no pip, no restart, no reboot, no config edit, no sudo.
> Box facts captured by two READ-ONLY runners on 2026-11-24 UTC (box clock is UTC; Jack-local was 2026-11-23 evening ET):
> `cc\secrev_box_probe.ps1` (ssh-STDIN, redacted) and `cc\secrev_az_probe.ps1` (read-only az control-plane).
> Evidence: `cc-secrev-2026-11-23-wt\analysis\{01_box_probe_redacted.txt, 02_requirements_box.txt, 03_azure_summary.txt, 04_advisory_research.md, 05_synthesis_notes.md}`.
> Citations: `(box:NN)` = line in 01_box_probe_redacted.txt; `(az)` = 03_azure_summary.txt; advisories by URL.

## PROVENANCE / CONFIDENCE (read first)
- **Box version strings + ports + NSG rules are directly observed** = high confidence.
- **CVE/advisory details were gathered by web-research agents on 2026-11-24** in a late-2026 timeline. Citations to `ubuntu.com/security/notices`, `github.com/<project>/security/advisories`, `python.org`, `nvd.nist.gov`, `osv.dev` are authoritative; a few package citations point to third-party mirror URLs and are **FLAGGED -> re-verify at source before any Phase-2 action** (04_advisory_research.md).
- Advisory IDs/dates are **leads for Phase 2**, not warrant to act blindly. Each Phase-2 batch re-verifies its advisories at source first.

---

## Executive summary (5 lines)
1. **Perimeter is tight and that is the headline: the only Internet-reachable ports are 80/443 -> Caddy; SSH (22) is locked to one home /32; RDP (3389) and the app (8000) are NOT allowed inbound** (Azure default-deny) (az). No actively-internet-exposed, unauthenticated finding exists.
2. The real internet attack surface is therefore **Caddy 2.11.4 and Authelia 4.39.19** -- both are behind on security releases (Caddy forward_auth race; Authelia ACL-canonicalization bypass) and are the top priority (box:179-182).
3. The box is **88 days up with a reboot pending**: running kernel `6.8.0-1064-azure` is ~5 USNs behind the already-installed `6.8.0-1073`, and `libc6`/glibc is updated-but-not-active -- a reboot window is required (box:24,27-45).
4. The app's own stack carries a **CISA-KEV web vuln (Starlette 1.0.0 BadHost, CVE-2026-48710)** reachable via the un-authed TradingView webhook path, plus JWT-forgery (PyJWT) and a web3 SSRF->IMDS->managed-identity chain worth closing (box:384,357,400).
5. Host hygiene: **not attached to Ubuntu Pro** (25 esm-apps security updates ungated; standard support ends Apr 2027), disk 83% with 4 GB of journald + stale `pm_*` backups to reclaim (box:109-127,425,429,434-442).

---

## Findings (prioritized)

### HIGH
| # | Item | Current | Fixed/Target | CVE / advisory | Exposure | Risk (incl. AI-assisted attacker) | Fix |
|---|---|---|---|---|---|---|---|
| H1 | **Caddy** reverse proxy (TLS term + forward_auth) | v2.11.4 (box:179) | v2.11.9 (apt, box:53) | GHSA-6365-7ppr-5r92 (Moderate, fixed 2.11.5); GHSA-r766-rf9f-588h (High, fixed 2.11.8) -- `github.com/caddyserver/caddy/security/advisories` | **Internet-facing (80/443, src=Internet)** (az) | forward_auth+reverse_proxy race can route an auth check to the wrong upstream -> intermittent auth-bypass of the dashboard, which fronts live order-execution routes. An LLM-assisted attacker mass-scans for the exact Caddy+forward_auth fingerprint and replays the race automatically. | `apt upgrade caddy` to 2.11.9 + `systemctl reload caddy` (root; web-only blip). |
| H2 | **Authelia** (sole auth gateway: argon2id+TOTP) | v4.39.19 (box:180) | v4.39.28 | CVE-2026-48794 / GHSA-j748-h363-wqj8 (ACL domain canonicalization miss -> possible 1FA downgrade, fixed 4.39.20) -- `github.com/authelia/authelia/security/advisories` | **Internet-facing** (gates all of `trading.jacksumner.com`) (az) | Uppercase-subdomain request can fall through to a more-permissive access-control rule = auth-bypass class on the one component standing between the Internet and the trading UI. 9 releases behind. AI attacker enumerates ACL case-variants at machine speed. | Replace `/usr/local/bin/authelia` binary with 4.39.28 (verify checksum/signature) + `systemctl restart authelia` (root; auth blip, no trading impact). |
| H3 | **Starlette** (under FastAPI) | 1.0.0 (box:384) | 1.0.1 | CVE-2026-48710 "BadHost" / GHSA-86qp-5c8j-p5mr -- **in CISA KEV (actively exploited)** -- `github.com/advisories/GHSA-86qp-5c8j-p5mr` (verify KEV listing) | **Internet-reachable via the Caddy webhook bypass** (`/webhook/tradingview/*` skip Authelia) (Web&Auth note) | Host header with `/` or `?` makes path-based middleware see a different path than the router -> auth/validation bypass. Reachable pre-auth on the webhook paths. KEV = exploit code is public; automated scanners already weaponize it. | `venv/bin/pip install 'starlette>=1.0.1'` (+ fastapi, see H/M below); **engine restart required** -> Robinhood-closed window. |
| H4 | **Kernel + glibc reboot pending** | running `6.8.0-1064-azure`, 88d uptime; `libc6 2.35-0ubuntu3.18` loaded (box:24,199) | boot installed `6.8.0-1073` + activate new libc6 (box:29,43-45) | USN-8761-3/8817-1/8904-1/9026-1 (linux-azure) -- `ubuntu.com/security/notices` | Local (post-access) + availability | 5 kernel USNs' worth of fixes are installed-but-not-running; glibc patched-but-not-active. *Note:* the ARM-TLB LPE (CVE-2025-10263) and AMD-SEV (CVE-2023-20585) do **NOT** apply to this x86-64 Intel D2s_v3; driver is the aggregate + glibc + 88d uptime, not those two. Availability risk: an unplanned reboot (crash) on a 24/7 money-mover is worse than a planned one. | `reboot` in a planned window (root); bounces box + all divisions (~several min). |

### MEDIUM
| # | Item | Current | Fixed/Target | CVE / advisory | Exposure | Risk | Fix |
|---|---|---|---|---|---|---|---|
| M1 | **Not on Ubuntu Pro** | not attached; 25 esm-apps security updates pending; std support ends **Apr 2027** (box:120,126,118-119) | attach free Pro (esm-infra+esm-apps to 2032) | `ubuntu.com/about/release-cycle` | Host | Universe/Multiverse security fixes (incl. the **xrdp** fix, M6) are ungated only with Pro; after Apr 2027 Main/Restricted stops too. AI-assisted attacker favors known-unpatched esm-apps packages. | `pro attach <token>` (root); free for <=5 machines. Jack decision. |
| M2 | **CPython interpreter** (prod venv; from deadsnakes PPA = 3rd-party) | 3.12.13 (box:241) | 3.12.16 (apt, box:81) | CVE-2026-19445 (ssl UAF in SNI cb), CVE-2026-15806 (HTTPS creds over HTTP redirect), CVE-2026-115557 (ssl CA denylist), + XML/tarfile/zip-bomb -- `python.org/downloads/release/python-3121{4,5,6}` | Network-reachable (TLS/HTTP/XML paths) | UAF in the SSL SNI callback and credential-leak-on-redirect are reachable from the engine's normal outbound TLS/HTTP to brokers/data. | `apt upgrade python3.12*` (root); venv uses system libpython3.12 -> **engine restart** to load new stdlib. |
| M3 | **web3 CCIP-read SSRF -> IMDS/Managed Identity** | web3==6.11.0 (box:400) | 7.15.0 (MAJOR bump) | CVE-2026-40072 / GHSA-5hr4-253g-cpx2 -- `nvd.nist.gov/vuln/detail/CVE-2026-40072` (verify web3.py repo path) | Conditional (Polymarket eth_call path) + **outbound unrestricted** (az) | CCIP-read is default-on; an eth_call to an attacker-influenced contract can force an outbound fetch to `169.254.169.254` (IMDS) -> mint a **managed-identity token** -> Azure Key Vault (the box's secret store). The highest-consequence chain here. AI attacker automates contract-sink discovery. | **Dev task** (6->7 is breaking): bump web3>=7.15.0 with code changes, or disable CCIP-read, or egress-block IMDS from the pm process. Do NOT blind-bump. |
| M4 | **PyJWT** | 2.12.1 (box:357) | 2.13.0 | CVE-2026-48526 (RSA-pub-as-HMAC forgery), CVE-2026-48523 (alg allowlist bypass) -- `github.com/advisories/GHSA-xgmm-8j9v-c9wx` | Reachability-dependent (any externally-sourced JWT verify) | Complete token forgery if a public key is accepted as an HMAC secret. Impact depends on whether the app verifies untrusted JWTs (vs only internal). | `venv/bin/pip install 'pyjwt>=2.13.0'`; engine restart. |
| M5 | **App binds `0.0.0.0:8000`** (bypasses Authelia for anything that reaches 10.0.0.4) | uvicorn on `0.0.0.0:8000` (box:157) | bind `127.0.0.1:8000` | config/defense-in-depth | **NOT internet-exposed** (NSG denies 8000) but **intra-VNet reachable** (az) | Any co-tenant VM on the VNet, or any future NSG slip, reaches the full trading dashboard -- incl. order-exec POSTs -- with **no auth** (Authelia is only enforced at Caddy). Zero-trust violation. | Set uvicorn host to 127.0.0.1 (code/systemd); engine restart. (Confirm no other VNet VM -- see Questions.) |
| M6 | **xrdp / RDP listening `*:3389`** on a headless trading box | 0.9.17-2ubuntu3 (box:89,162) | 0.9.17-2ubuntu3.1 (ESM) + ideally remove | USN-8476-1: CVE-2024-39917 (MaxLoginRetry not enforced), CVE-2025-68670, CVE-2023-42822/40184 -- `ubuntu.com/security/notices/USN-8476-1` | **NOT internet-exposed** (NSG denies 3389); intra-VNet only | Needless attack surface + brute-forceable (login-retry cap unenforced) + patch is Pro-gated. No reason for RDP on this VM. | `systemctl disable --now xrdp xrdp-sesman` and/or `apt purge xrdp` (root). Decide why it exists first. |
| M7 | **cryptography / aiohttp** (broker/data clients) | cryptography 47.0.0 (box:273); aiohttp 3.13.5 (box:250) | cryptography 49.0.0; aiohttp 3.14.4 | CVE-2026-69248 (wildcard-SAN name-constraint bypass); CVE-2026-69244 (aiohttp C-parser OOB read) -- `osv.dev` / `github.com/aio-libs/aiohttp/security/advisories` | Network client paths | Cert name-constraint bypass weakens TLS verification; aiohttp OOB read crashes the client on a malicious response. Mostly DoS/verification-weakening, not RCE. | pip bump in venv; engine restart; test (cryptography 47->49 is a notable jump). |
| M8 | **Disk / log hygiene** | `/` 83% (11G free) (box:425); journald 4.0G (box:429); stale `pm_*` backups ~2G+ (box:434-442) | <75%, journald capped, backups pruned | availability | Host | A 24/7 trader that fills `/` stops writing its SQLite WAL/audit -> silent data loss / crash. Improved from 89% (2026-11-21) but the levers (journald vacuum, backup prune) are untouched. | `journalctl --vacuum-size=1G` (root); archive+rm old `/home/azureuser/pm_*_backup_*`; keep <75%. |

### LOW
| # | Item | Current | Note | Fix |
|---|---|---|---|---|
| L1 | OS security debs behind (non-kernel) | krb5 0.8->0.10 (BUGFIX only, no CVE delta -- USN-8585-1 already in 0.8), glib/chrony/snapd/misc (box:54-88) | Mostly bugfix; patch for hygiene with the apt batch. | `apt upgrade` (root). |
| L2 | **Trusted Launch off** | `securityType` empty on a gen2 image (az) | No vTPM/secure-boot/boot-integrity at the Azure layer. | Enable Trusted Launch (requires VM reconfig/redeploy; plan). |
| L3 | encryptionAtHost off | empty (az); OS disk Standard_LRS, platform SSE still on by default | Temp/cache disk not encrypted at host. | Enable encryption-at-host (redeploy). |
| L4 | urllib3 / pillow / fastapi | urllib3 2.6.3 (Brotli bomb, fixed-ver TBC), pillow 12.2.0->12.3.0, fastapi 0.136.1 param-DoS->0.163.2 (box:394,338,287) | Client-side DoS class. Verify fixed versions at source (some mirror-cited). | pip bump; engine restart. |
| L5 | fail2ban absent | not installed; is-active inactive (box:456) | **Negligible** given SSH is key-only (box:239) + NSG /32-locked (az). | Optional; low value here. |
| L6 | Duplicate NSG SSH rules | [110] + [1000] identical src/port (az) | Hygiene only. | Remove one. |
| L7 | Third-party patch channels | Caddy apt repo, Authelia manual binary, deadsnakes python3.12 ("7 packages from third parties", box:112) | These bypass Ubuntu security; each needs its own update discipline. | Track versions explicitly. |
| L8 | Playwright Firefox / snap firefox present | playwright 1.59.0 firefox-1511 (box:340,421); snap firefox 159 (box:227) | Headless browser (Fidelity path = paper) + a desktop Firefox snap = surface; not exposed. | Prune if unused. |

### Positives (explicitly verified good)
- SSH **key-only** (`PasswordAuthentication no`, box:239; Azure `disablePasswordAuthentication True`, az) and **NSG-locked to one /32** (az).
- **unattended-upgrades enabled** (`1`/`1`, box:132-133) and running monthly (box:144-150) -- which is why most debs are only one patch-level behind.
- OpenSSH `8.9p1-3ubuntu0.18` is **current** (regreSSHion + all later SSH USNs applied; box:206-207 / USN-8928-1).
- Jinja2 3.1.6 and requests 2.33.1 already carry their latest fixes (04_advisory_research.md).
- Caddy admin (2019), Authelia (9091), second app (8081) all bound **127.0.0.1** (box:156,158,159). Time sync healthy (box:462-463). VM agent Ready (az).

---

## Current State vs Recommended State
| Dimension | Current | Recommended |
|---|---|---|
| Internet-exposed ports | 80/443 (Caddy), 22 (home /32) (az) | unchanged -- already minimal |
| Caddy | 2.11.4 | 2.11.9 |
| Authelia | 4.39.19 | 4.39.28 |
| Running kernel | 6.8.0-1064 (88d) | 6.8.0-1073 (reboot) |
| glibc active | 2.35-0ubuntu3.18 loaded pre-update | reboot to activate patched libc6 |
| App bind | 0.0.0.0:8000 | 127.0.0.1:8000 |
| RDP/xrdp | listening *:3389 | disabled/removed |
| Ubuntu Pro | not attached (std support -> Apr 2027) | attached (esm to 2032) |
| Patch orchestration | ImageDefault (no Azure Update Mgr; no auto-reboot) | keep unattended-upgrades; add a planned monthly reboot window |
| CPython (venv) | 3.12.13 | 3.12.16 |
| Key app deps | starlette 1.0.0 / pyjwt 2.12.1 / web3 6.11.0 / cryptography 47 | starlette 1.0.1 / pyjwt 2.13.0 / web3 7.15 (dev) / cryptography 49 |
| Disk `/` | 83%, journald 4G | <75%, journald <=1G, backups pruned |
| Trusted Launch | off | on (plan) |

---

## REMEDIATION PLAN -- PHASE 2 (NOT EXECUTED)
General rule: a `trading-corp.service` restart or a reboot **bounces ALL divisions ~3.5 min** (crypto Bitunix 24/7 + Kalshi evenings/weekends never fully idle; Robinhood PMCC/PEAD/MACE idle only when US equity/options market is closed). Prefer a **weekend daytime** slot (Robinhood flat) and first **confirm Bitunix positions are managed and no Kalshi settlement is imminent**. Caddy/Authelia work does NOT touch the engine.

**Batch 0 -- PRE-STEP (Jack; do before any change).**
- Azure OS-disk snapshot (rollback point): `az snapshot create -g rg-shared-prod -n tc-prod-os-YYYYMMDD --source <osDisk.managedDisk.id from az_vm_show.json>`.
- Optional read-only Azure assessment (installs nothing): `az vm assess-patches -g rg-shared-prod -n tc-prod-vm`.
- Capture rollback baselines: `dpkg -l > ~/pkgs_pre.txt`; `venv/bin/pip freeze > ~/pip_pre.txt`.
- Rollback path: swap OS disk back to the snapshot; `pip install -r ~/pip_pre.txt` for venv regressions.

**Batch 1 -- Internet-facing proxy/auth (HIGH; root; NO engine restart; ~seconds of auth/web blip).**
- `apt-get update && apt-get install --only-upgrade caddy` -> 2.11.9; `systemctl reload caddy`.
- Replace Authelia binary with 4.39.28 (download release, **verify checksum/GPG**, swap `/usr/local/bin/authelia`, `systemctl restart authelia`).
- Needs root (az vm run-command = Jack). Verify: `caddy version`=2.11.9; `authelia --version`=4.39.28; `curl -sI https://trading.jacksumner.com/healthz` 200; a real login still works; webhook path still 200s.

**Batch 2 -- App dependency security (HIGH/MED; venv pip; REQUIRES engine restart; Robinhood-closed window).**
- Stage in a scratch venv first; pin exact versions; keep `~/pip_pre.txt`.
- `venv/bin/pip install 'starlette>=1.0.1' 'fastapi>=0.163.2' 'pyjwt>=2.13.0' 'cryptography>=49.0.0' 'aiohttp>=3.14.4' 'urllib3>=2.6.4' 'pillow>=12.3.0'`.
- **web3 6->7 is EXCLUDED here** (breaking; separate dev task M3).
- Restart engine via canonical `restart_tc.ps1` (Jack). Verify: `/healthz` 200; boot log shows all divisions armed; `pip freeze` shows targets; smoke the PM/web paths.

**Batch 3 -- OS security debs, non-kernel (MED/LOW; root; NO reboot; run live, low impact).**
- `apt-get install --only-upgrade python3.12 libpython3.12 libpython3.12-stdlib python3.12-venv krb5* libglib2.0-* chrony snapd open-vm-tools netplan.io` (hold linux-image/libc6 for Batch 4).
- python3.12 patch only takes effect in the venv after the Batch 2 engine restart (order Batch 3 before/with Batch 2's restart).
- Verify: `apt list --upgradable` shrinks; services still green.

**Batch 4 -- Kernel + glibc + reboot (HIGH; root; full reboot; biggest window).**
- `apt-get install --only-upgrade linux-image-azure libc6` then `reboot` (or rely on already-installed 1073 + reboot).
- Bounces box + all divisions (~several min). Do in the Robinhood-closed window after confirming Bitunix/Kalshi exposure is flat/managed.
- Verify: `uname -r`=6.8.0-1073; `[ -f /var/run/reboot-required ]` gone; engine + Caddy + Authelia back; `/healthz` 200; division boot-reconcile clean.

**Batch 5 -- Hardening/hygiene (MED/LOW; root; low/no trading impact).**
- Bind app to 127.0.0.1 (M5; code/systemd + engine restart -- fold into Batch 2's restart).
- Disable/remove xrdp (M6): `systemctl disable --now xrdp xrdp-sesman` (no restart of engine).
- `journalctl --vacuum-size=1G`; prune `~/pm_*_backup_*` (M8).
- `pro attach` (M1) -- Jack decision; then `apt upgrade` picks up esm-apps incl. xrdp fix.
- De-dupe NSG SSH rule (L6) via `az`.

Suggested order by urgency-vs-disruption: **Batch 0 -> 1** (now, no trading impact) -> **3** (live) -> **2+5bind** (one Robinhood-closed restart) -> **4** (reboot, same or adjacent window) -> **5 rest** -> **M3 web3** (scheduled dev task).

---

## Questions for Jack (genuine blockers only)
1. **Is any other VM on `tc-prod-subnet` / the VNet?** (`rg-shared-prod` implies shared infra.) If yes, M5 (app `0.0.0.0:8000`) and M6 (xrdp 3389) are **live intra-VNet exposures**, not just defense-in-depth -> raise both to High and bind/disable promptly.
2. **Authelia auth backend -- file(argon2id) or LDAP?** CVE-2026-47203 (brute-force-regulation bypass) only applies to LDAP+BasicAuth; if file-backend, that one is N/A (the H2 ACL bypass still applies either way).
3. **Why is xrdp installed on a headless trading VM?** OK to disable/remove it (M6)?
4. **Ubuntu Pro (M1):** attach the free subscription? It unlocks esm-apps now (incl. the xrdp fix) and esm-infra to 2032.
5. **web3 6->7 (M3):** schedule the breaking upgrade as a dev task, or prefer the interim mitigation (disable CCIP-read / egress-block IMDS from the pm process)?
6. **Reboot window (Batch 4):** confirm a weekend daytime slot with Bitunix/Kalshi exposure pre-checked flat.

## Upkeep
Review only; **nothing on prod changed** -> no code/config/MOC edit required by the AGENTS.md upkeep rule. This note is the Phase-1 deliverable; Phase 2 batches are gated on Jack's per-batch approval. Runners live at `cc\secrev_box_probe.ps1` and `cc\secrev_az_probe.ps1`; evidence under `cc-secrev-2026-11-23-wt\analysis\`.
