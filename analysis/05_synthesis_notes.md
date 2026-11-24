# Synthesis notes (severity reconciliation after Azure NSG data)

## Perimeter (az probe 2026-11-24) — DECISIVE
NSG tc-prod-nsg (subnet-level; no NIC-level NSG) inbound custom rules:
- [110] + [1000] SSH dport=22 Allow src=98.231.16.63/32 ONLY (duplicate rules)
- [1010] HTTP 80 Allow src=Internet
- [1020] HTTPS 443 Allow src=Internet
- NO rule for 3389, NO rule for 8000 -> Azure default DenyAllInbound blocks both from internet.
Outbound: Azure defaults (all allowed).
=> Internet-reachable services = Caddy (80/443) only. SSH = home /32 only. App:8000 and xrdp:3389 = intra-VNet only.

## Arch note
VM = Standard_D2s_v3 (x86-64 Intel). Therefore kernel CVE-2025-10263 (ARM TLB LPE) and CVE-2023-20585
(AMD SEV-SNP) are NOT applicable to this VM. Kernel reboot still warranted for the other hundreds of CVEs
+ pending libc6, but do NOT cite those two as the driver.

## Patch orchestration
patchMode=ImageDefault, assessmentMode=ImageDefault => Azure Update Manager NOT managing patches.
Box relies on unattended-upgrades (enabled, 20auto-upgrades =1/1). Config has NO Automatic-Reboot=true,
so it installs security debs but NEVER reboots -> 88d uptime, 5 kernels + libc6 staged, reboot-required set.
vmAgent runtime 2.16.0.2 Ready (auto-updated OOB; dpkg walinuxagent 2.15.0.1 is just the base deb) -> agent healthy, not a finding.

## Severity reconciliation
- app 0.0.0.0:8000 -> MEDIUM (was going to be Critical if NSG exposed it; it does not). DiD + intra-VNet.
- xrdp *:3389 -> MEDIUM (not internet-exposed; needless surface + ESM patch pending + CVE-2024-39917 brute-force).
- Internet-facing HIGHs = Caddy, Authelia, Starlette(KEV via webhook bypass), reboot(kernel+glibc).
- web3 6.11.0 CCIP-read SSRF: elevate because outbound is unrestricted + VM has managed identity ->
  SSRF to IMDS 169.254.169.254 could mint a managed-identity token -> Key Vault. CONDITIONAL on the pm
  code doing eth_call to attacker-influenced addresses with CCIP-read default-on. Rate MEDIUM-HIGH, caveat reachability.

## Residual unknowns -> Questions for Jack
1. Are there OTHER VMs on tc-prod-subnet / the VNet? If yes, 8000/3389 intra-VNet exposure is real (co-tenant reachable).
2. Authelia auth backend = file(argon2id) or LDAP? CVE-2026-47203 (brute-force bypass) only applies to LDAP+BasicAuth.
3. Does any pm/web3 path eth_call untrusted contract addresses with CCIP-read enabled? (web3 SSRF reachability)

## Provenance caveat (carry into report)
Advisory IDs/dates are from web-research agents on 2026-11-24. Box versions are directly observed.
Authoritative citations: ubuntu.com/security/notices, github.com/<proj>/security/advisories, python.org, nvd, osv.
Flagged-for-verification (third-party mirror URLs): aiohttp CVE-2026-128124/128128, fastapi GHSA-f56c,
pillow CVE-2026-54058, urllib3 fixed-version. Re-verify each HIGH advisory at source before Phase-2 action.
