# Advisory research (web, gathered 2026-11-24 via 3 parallel Sonnet agents)

PROVENANCE NOTE: Box version strings are directly observed from the read-only probe (high confidence).
The advisory data below was gathered by web-research agents. Citations to ubuntu.com/security/notices,
github.com/<project>/security/advisories, python.org, nvd.nist.gov, and osv.dev are AUTHORITATIVE.
A few package citations point to third-party repo issue trackers (l3montree-dev/devguard,
MSKazemi/novafabric, Graphify-Labs/graphify) — these are NON-AUTHORITATIVE mirrors and are FLAGGED
for re-verification against the upstream GHSA/OSV before any Phase-2 action.

---

## Agent A — OS / kernel / OpenSSH / distro packages

### Ubuntu 22.04 LTS lifecycle
Standard security (Main/Restricted) ends **April 2027**; ESM (Ubuntu Pro) ends **April 2032**.
Source: https://ubuntu.com/about/release-cycle

### Kernel linux-azure USNs (running 1064 -> installed 1073)
| USN | Fixed kernel (22.04 azure) | Key CVEs | Priv-esc/RCE? | URL |
|---|---|---|---|---|
| USN-8793-2 | 6.8.0-1065 (CVM/fde) | CVE-2026-46315/46275/46170/46158, CVE-2025-71289 | not flagged | https://ubuntu.com/security/notices/USN-8793-2 |
| USN-8761-3 | 6.8.0-1067 | 95 CVEs incl CVE-2026-64518/64231/64225 | not flagged | https://ubuntu.com/security/notices/USN-8761-3 |
| USN-8817-1 | 6.8.0-1068 | CVE-2025-10263 (ARM TLB **LPE**), CVE-2026-64091, CVE-2026-53131 | **YES LPE** | https://ubuntu.com/security/notices/USN-8817-1 |
| USN-8904-1 | 6.8.0-1070 | 300+ CVEs | not flagged | https://ubuntu.com/security/notices/USN-8904-1 |
| USN-9026-1 | 6.8.0-1073 | 1000+ CVEs incl CVE-2023-20585 (AMD SEV-SNP) | guest-memory integrity | https://ubuntu.com/security/notices/USN-9026-1 |

Booting the already-installed 1073 closes all five. Highest flags: CVE-2025-10263 (confirmed LPE), CVE-2023-20585.

### OpenSSH 1:8.9p1-3ubuntu0.18
regreSSHion CVE-2024-6387 fixed at -3ubuntu0.10 (USN-6859-1). Installed 0.18 is CURRENT; chain of USNs
(7270-1 -> 0.11, 8090-1 -> 0.14, 8222-1 -> 0.15, 8533-1 -> 0.16, 8721-1 -> 0.17, 8928-1 -> 0.18) all
applied. No open SSH USN remains. Source: https://ubuntu.com/security/notices/USN-8928-1

### krb5 1.19.2-2ubuntu0.8 -> 0.10
0.8 already carried the last security fix (USN-8585-1, DoS-class). 0.9/0.10 are BUGFIX ONLY (no CVE).
Source: https://ubuntu.com/security/notices/USN-8585-1

### xrdp 0.9.17-2ubuntu3 -> 2ubuntu3.1
The .1 is an ESM/Pro security update (USN-8476-1): CVE-2025-68670, CVE-2024-39917 (MaxLoginRetry not
enforced -> brute force), CVE-2023-42822, CVE-2023-40184. Medium. Source: https://ubuntu.com/security/notices/USN-8476-1

### snapd 2.76.3 -> 2.78, chrony 4.2-2ubuntu2 -> .1
Both BUGFIX ONLY, no USN/CVE delta. (snapd prior LPEs already in 2.76.3.)

---

## Agent B — Caddy + Authelia (internet-facing)

### Caddy v2.11.4 (installed) -> v2.11.9 available
VERDICT: v2.11.4 IS vulnerable to issues fixed in 2.11.5-2.11.9.
| GHSA | CVE | Sev | Title | Fixed | Relevance |
|---|---|---|---|---|---|
| GHSA-6365-7ppr-5r92 | none | **Moderate** | wrong-upstream connection for forward_auth+reverse_proxy (race -> intermittent auth bypass) | 2.11.5 | **DIRECTLY hits this exact config (Caddy forward_auth -> Authelia)** |
| GHSA-8qx7-j2v2-x6q5 | CVE-2026-55458 | Low | hop-by-hop headers leak via WebSocket 101 upgrade | 2.11.5 | reverse-proxy header injection |
| GHSA-r766-rf9f-588h | none | High | {host}/:authority dot-segment path traversal | 2.11.8 | only if dynamic {host} root / on-demand TLS |
Already-patched-in-2.11.4 (not applicable): GHSA-f59h-q822-g45g (CVE-2026-52845), GHSA-j8px-rmrx-76h9 (CVE-2026-77281).
Sources: https://github.com/caddyserver/caddy/security/advisories , https://github.com/caddyserver/caddy/releases

### Authelia v4.39.19 (installed) -> v4.39.28 latest
VERDICT: v4.39.19 IS affected by:
| GHSA | CVE | Sev | Title | Affected | Fixed |
|---|---|---|---|---|---|
| GHSA-j748-h363-wqj8 | CVE-2026-48794 | Low (CVSS~2.3) | access-control domain miss via missing canonicalization (uppercase subdomain -> more-permissive rule; possible 1FA downgrade) | 4.36.0-4.39.19 | **4.39.20** |
| GHSA-hjj4-hfjm-fmrj | CVE-2026-47203 | Moderate (6.3) | brute-force regulation bypass via LDAP case-variant usernames | 4.38.0-4.39.19 | 4.39.20 (LDAP+BasicAuth only) |
Not applicable: GHSA-gmfg-3v4q-9qr4 (fixed 4.39.16), GHSA-68wm-pfjf-wqp6 (nginx-only).
Sources: https://github.com/authelia/authelia/security/advisories , https://github.com/authelia/authelia/releases

---

## Agent C — CPython + pip deps

### CPython 3.12.13 -> 3.12.16 (prod venv interpreter; from deadsnakes PPA)
Network-reachable highlights (full table in report):
- 3.12.15 CVE-2026-19445 SSL use-after-free in SNI callback (TLS handshake reachable)
- 3.12.15 CVE-2026-15806 urllib HTTPS creds leaked over HTTP redirect
- 3.12.16 CVE-2026-115557 ssl denylists compromised eMudhra intermediate CA (MITM)
- 3.12.14-16 multiple libexpat/XML, tarfile, zipfile-bomb, http.cookies injection CVEs
Sources: https://www.python.org/downloads/release/python-31214/ , -31215/ , -31216/

### pip packages (authoritative unless flagged)
| package==version | Verdict | GHSA/CVE | Fixed in | Source / FLAG |
|---|---|---|---|---|
| web3==6.11.0 | VULNERABLE | GHSA-5hr4-253g-cpx2 / CVE-2026-40072 SSRF via CCIP Read default-on | 7.15.0 | https://github.com/ApeWorX/web3.py/security/advisories/GHSA-5hr4-253g-cpx2 (NOTE: ApeWorX repo path looks wrong for web3.py -> VERIFY) ; NVD https://nvd.nist.gov/vuln/detail/CVE-2026-40072 |
| aiohttp==3.13.5 | VULNERABLE | GHSA-cq5v-8q36-5273 / CVE-2026-69244 OOB heap read in C parser (<=3.14.2) | 3.14.3 | https://github.com/aio-libs/aiohttp/security/advisories/GHSA-cq5v-8q36-5273 |
| aiohttp==3.13.5 | VULNERABLE | GHSA-jm3w-gw2p-xjfj / CVE-2026-128124 ReDoS add_domain | 3.14.4 | FLAG third-party mirror -> VERIFY |
| aiohttp==3.13.5 | VULNERABLE | GHSA-3rp8-rxj8-mvjw / CVE-2026-128128 ws max_msg_size int overflow | 3.14.4 | FLAG third-party mirror -> VERIFY |
| cryptography==47.0.0 | VULNERABLE | GHSA-m2h6-j472-rp4c / CVE-2026-69248 wildcard DNS SAN escapes name constraints (45.0.0-48.0.1) | 49.0.0 | https://osv.dev/vulnerability/GHSA-m2h6-j472-rp4c |
| requests==2.33.1 | PATCHED | CVE-2026-25645 fixed 2.33.0 | - | https://github.com/advisories/GHSA-gc5v-m9x4-r6x2 |
| urllib3==2.6.3 | VULNERABLE | GHSA-MWQ6-FG78-P6CQ / CVE-2026-9375 Brotli decompression-bomb bypass; fixed ver not stated | >2.6.3 | https://github.com/advisories/GHSA-mwq6-fg78-p6cq -> VERIFY fixed ver |
| starlette==1.0.0 | **VULNERABLE (KEV)** | GHSA-86qp-5c8j-p5mr / CVE-2026-48710 BadHost Host-header path bypass; **CISA KEV actively exploited** (0.8.3-1.0.0) | 1.0.1 | https://github.com/advisories/GHSA-86qp-5c8j-p5mr |
| fastapi==0.136.1 | VULNERABLE (via starlette) | CVE-2026-48710 | starlette 1.0.1 | https://github.com/fastapi/fastapi (VERIFY) |
| fastapi==0.136.1 | VULNERABLE | GHSA-f56c-v883-vx8r quadratic param DoS (>=0.123.2,<0.163.2) | 0.163.2 | FLAG third-party mirror -> VERIFY |
| Jinja2==3.1.6 | PATCHED | CVE-2025-27516 etc fixed in 3.1.6 | - | https://ubuntu.com/security/CVE-2025-27516 |
| pillow==12.2.0 | VULNERABLE | GHSA-62p4-gmf7-7g93 / CVE-2026-54058 OOB read McIdas AREA (<12.3.0) | 12.3.0 | FLAG third-party mirror -> VERIFY |
| PyJWT==2.12.1 | VULNERABLE | GHSA-jq35-7prp-9v3f / CVE-2026-48523 alg allowlist bypass; GHSA-xgmm-8j9v-c9wx / CVE-2026-48526 RSA-pub-as-HMAC forgery (<2.13.0) | 2.13.0 | https://github.com/advisories/GHSA-jq35-7prp-9v3f |
| py-ecc==8.0.0 | none located | - | - | - |
| eth-account==0.13.1 | none (old ReDoS fixed 0.5.9) | - | - | https://github.com/advisories/GHSA-v65g-f3cj-fjp4 |

Priority call-outs (Agent C): starlette BadHost (KEV) #1; PyJWT forgery #2; cryptography name-constraint #3;
CPython ssl/urllib CVEs #4; urllib3 Brotli bomb #5; web3 SSRF #6.
