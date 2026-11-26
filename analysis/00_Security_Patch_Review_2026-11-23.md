---
type: current-state
domain: trading-corp
division: platform
source_path: "vm:/home/azureuser/trading_corp"
source_commit: f742c377cd62
last_verified: 2026-11-25
tags: [current-state, platform, security, patch-review, unverified]
---

# Security Patch Review -- 2026-11-23

> PHASE 1 = ASSESS ONLY. Nothing on the box was changed: no apt, no pip, no restart, no reboot, no config edit, no sudo.
> Box facts captured by two READ-ONLY runners on 2026-11-24 UTC (box clock is UTC; Jack-local was 2026-11-23 evening ET):
> `cc\secrev_box_probe.ps1` (ssh-STDIN, redacted) and `cc\secrev_az_probe.ps1` (read-only az control-plane).
> Evidence: `cc-secrev-2026-11-23-wt\analysis\{01_box_probe_redacted.txt, 02_requirements_box.txt, 03_azure_summary.txt, 04_advisory_research.md, 05_synthesis_notes.md}`.
> Citations: `(box:NN)` = line in 01_box_probe_redacted.txt; `(az)` = 03_azure_summary.txt; advisories by URL.

## STATUS -- BATCHES 0,1,3,5(non-Pro) EXECUTED (2026-11-24)
- **Batch 0 DONE:** snapshot `tc-prod-os-20261124` (Succeeded) + `az vm assess-patches` (29 crit+sec) + dpkg/pip baselines (`10_`/`11_`). (2nd snapshot `tc-prod-os-20261124b` Succeeded before the 2026-11-24 evening batches, `15_`.)
- **Batch 1 DONE (Caddy + Authelia remediated):** **Caddy 2.11.4 -> 2.11.9** + **Authelia 4.39.19 -> 4.39.28** (validate-before-install / sha256 + validate-config gate + `/api/health` 200). trading-corp NOT restarted. Login w/ 2FA confirmed by Jack. Evidence `13_`.
- **Batch 3 DONE (26 OS security debs; `16_`):** chrony/krb5/glib/snapd/open-vm-tools/netplan/etc. upgraded; **EXCLUDED** kernel(1074)/libc6/python3.12*/xrdp. `NEEDRESTART_MODE=l` -> **nothing auto-restarted**; all 6 protected services' PIDs unchanged (trading-corp 1060188, pm-web 1020846, sfp 656, mcr 645, caddy, authelia). Remaining upgradable=12 (the excluded set).
- **Batch 5 non-Pro DONE (`17_`):** **xrdp stop+disable+mask** (3389/3350 closed, not purged); **journald capped SystemMaxUse=1G** (3.9G->1008M), disk 84%->80%. Protected PIDs unchanged.
- **Disk-reclaim open question ANSWERED:** the earlier 89%->83% was **automatic** (unattended-upgrades autoremoving old azure kernels + journald rotation; NOT apt-cache autoclean, which is disabled), and my first probe already read 83% -> nothing I ran caused it. Evidence `14_recon.txt`.
- **Batch 2 DONE 2026-11-25 03:37Z (CODE deploy; M5 + Starlette-KEV + CPython remediated; `20_`/`21_`):** one engine restart (PID 1060188->1085473). `main.py:2799` dashboard bind **0.0.0.0 -> 127.0.0.1** (M5; `ss` now 127.0.0.1:8000 only); **starlette 1.0.0 -> 1.0.1** (CVE-2026-48710 BadHost KEV) pinned in `requirements.lock`+`.txt` (fastapi 0.136.1 unchanged); held **python3.12 deadsnakes 3.12.13 -> 3.12.16** applied same window. Verified GREEN (0 tracebacks/5min, all 6 divisions up incl. pm_live_driver, 302/405/400, authelia 200). **prod-live FF'd 50aea132 -> f742c377** (code 73b83e4a + deploy_log ledger f742c377); deploy_log recorded. Branch `secrev-batch2-bind-starlette-2026-11-24`.
- **Batch 4 DONE 2026-11-25 04:04Z (H4 remediated; `23_`):** snapshot `tc-prod-os-20261124c` (Succeeded) -> kernel **6.8.0-1074-azure** + libc6 activated + **reboot** (via systemd transient timer -- the first `nohup`-backgrounded attempt was killed by az run-command cleanup; `systemd-run --on-active` survives). After boot: `uname -r`=1074, **reboot-required CLEARED**, all 8 units active, engine 0 tracebacks, all 6 live divisions up (pm_live_driver cycling), bind 127.0.0.1:8000, 302/405/400, authelia 200, crontab(azureuser) intact. Box healthy ~5 min post-reboot.
- **Housekeeping DONE 2026-11-25 23:28Z (`24_`):** journald `SystemMaxUse` **1G->2G** (Jack 22:37 stopgap; active); **pm_* Tier A DELETED** -- all 12 Aug backups + `.db-shm/-wal` siblings, **753.5 MB freed**, abort-on-newer guard passed, the 3 Oct rollback points kept (disk 77%->76%, 16G free); `reset-failed xrdp-sesman` (0 failed xrdp units; both masked; 3389/3350 clear). No engine restart; 6 protected PIDs unchanged.
- **Batch 2b DONE 2026-11-26 00:26Z (CODE deploy, one engine restart PID 696->8931; `26_`/`27_`):** `pip install` + hash-pinned `requirements.lock`/`.txt`: **pyjwt 2.12.1->2.15.1** (M4; CVE-2026-48523/48526), **urllib3 2.6.3->2.8.0** (M7; CVE-2026-9375), **pillow 12.2.0->12.3.0** (CVE-2026-54058). **HELD (reported): cryptography** (msal caps `<49` vs CVE fix `>=49`), **aiohttp** (forces yarl 1.25.1), **fastapi** (forces starlette off 1.0.1 + opentelemetry). PC scratch-venv full-suite: identical to baseline (no new failures); `pip check` clean; starlette 1.0.1/fastapi 0.136.1 unchanged. Verified GREEN: ss 127.0.0.1:8000, 0 tracebacks, 6 divisions up, **broker auth OK** (RH equity+12pos / Bitunix reconciler / Kalshi all 4 accts, 0 auth errors, Key Vault 277 success), 302/405/400, authelia 200. Branch `secrev-batch2b-2026-11-25` @ `a2a2f68c` + deploy_log `110d735c`; **FF to prod-live pending Jack's push** (reserved).
- **Still deferred:** **Ubuntu Pro** when Jack has the token. **Snapshot prune** (delete `tc-prod-os-20261124{,b}`, keep `-20261124c`) after a few stable days -- NOT yet (1 day). pm_* **Tier B** KEEP + NSG redundant rule LEAVE (Jack 22:06). **14-day journal retention** still needs the PM-chat log-volume cut (then ~4G); 2G is the interim.

## PROVENANCE / CONFIDENCE (read first)
- **Box version strings + ports + NSG rules are directly observed** = high confidence.
- **CVE/advisory details were gathered by web-research agents on 2026-11-24** in a late-2026 timeline. Citations to `ubuntu.com/security/notices`, `github.com/<project>/security/advisories`, `python.org`, `nvd.nist.gov`, `osv.dev` are authoritative; a few package citations point to third-party mirror URLs and are **FLAGGED -> re-verify at source before any Phase-2 action** (04_advisory_research.md).
- Advisory IDs/dates are **leads for Phase 2**, not warrant to act blindly. Each Phase-2 batch re-verifies its advisories at source first.

---

## PHASE 2 -- CORRECTIONS & INVESTIGATION (2026-11-24)
> Jack's corrections (A) + read-only investigation answers (B), each cited. Evidence added this round:
> `06_investigation_redacted.txt` (box, ssh-STDIN, redacted), `07_az_vnet.txt` (az), and a re-verification agent
> re-checking every flagged advisory at NVD/GHSA/OSV/CISA-KEV. Citations below: `(inv:NN)` = line in
> `06_investigation_redacted.txt`; `(vnet)` = `07_az_vnet.txt`; `(reverify)` = the 2026-11-24 verification agent.

**A1 -- Authelia CVE-2026-48794 re-rated LOW.** NVD/GHSA score it CVSSv4 overall **1.3 LOW** (CVSSv3.1 3.1), edge-case domain canonicalization, ~9 conditions; fixed **4.39.20** (reverify: `osv.dev/vulnerability/CVE-2026-48794`, `github.com/authelia/authelia/security/advisories/GHSA-j748-h363-wqj8`). **Authelia finding moved HIGH -> MEDIUM.** It stays in tonight's Batch 1 anyway: cheap drop-in, it is the sole internet-facing auth gate, it is 9 releases behind, and the box is under active hostile recon (see A7).

**A2 -- "egress-block IMDS" REMOVED as a web3 mitigation.** Confirmed the app reads Key Vault through the VM's **managed identity**, which uses the **IMDS** endpoint: `trading_corp/scripts/pm_web.py:54-61` imports `azure.identity.DefaultAzureCredential` + `azure.keyvault.SecretClient` against `KEY_VAULT_URI` (inv:23-31); same pattern across the `utils/secrets.py` lineage (inv:32-62). `DefaultAzureCredential`/`ManagedIdentityCredential` on an Azure VM obtain tokens via IMDS 169.254.169.254, so egress-blocking IMDS would break secret loading and the engine. Interim mitigation for web3 is therefore **code-level only** (disable CCIP-read) -- not a network block.

**A3 -- OS release CONFIRMED Ubuntu 22.04, NOT 24.04. Support dates stand.** `/etc/os-release` = `PRETTY_NAME="Ubuntu 22.04.5 LTS"`, `VERSION_ID="22.04"`, `UBUNTU_CODENAME=jammy` (box:10-21). The 6.8 kernel is the **22.04.5 HWE (hardware-enablement) backport** (`linux-azure` 6.8 on jammy), not a 24.04 signal; and `python3.12` is from the **deadsnakes PPA** ("New Python Versions" repo, box:91) -- the Ubuntu base interpreter is still **python3 3.10.6** (box:209), which is jammy. Azure image confirms it: `publisher=Canonical offer=0001-com-ubuntu-server-jammy sku=22_04-lts-gen2` (az). **Standard support (Main/Restricted) ends Apr 2027; ESM Apr 2032** -- `ubuntu.com/about/release-cycle`.

**A4 -- Disk re-measured: CURRENT = 83% / 11G free** (`/dev/root 62G 52G 11G 83% /`, inv:2; inodes 3%, inv:3). The 2026-11-21 VM check read 89% / 7.2G. The ~4 GB reclaim is explained by the **apt archive cache now being 32K** (autocleaned by unattended-upgrades; inv:7) plus journald rotating at its cap. It remains a watch item: the real consumers are still **pm_* backups 4.3G** (inv:20) and **journald 4.1G** (inv:5). "83%" in this report is the current, authoritative figure.

**A5 -- Flagged advisories re-verified at source (reverify).** Corrections to the Phase-1 table:
- **urllib3 CVE-2026-9375** fixed in **2.7.0** (NOT ">2.6.3"; 2.6.3 is the last vulnerable build), CVSS 7.5 -- `osv.dev/vulnerability/CVE-2026-9375`.
- **web3 CVE-2026-40072** is **CVSS 1.7 LOW**, repo **ApeWorX/web3.py** (not ethereum/web3.py), affected <=7.14.1, fixed **7.15.0** -- `osv.dev/vulnerability/CVE-2026-40072`.
- **aiohttp**: CVE-2026-69244 fixed **3.14.3**; CVE-2026-128124 (CVSS 8.2) + CVE-2026-128128 (7.5) fixed **3.14.4** -- aio-libs advisories CONFIRMED.
- **fastapi GHSA-f56c-v883-vx8r** CONFIRMED CVSS 7.5, affects 0.123.2-0.163.1, fixed **0.163.2** (pub 2026-11-17).
- **pillow CVE-2026-54058** CONFIRMED, fixed **12.3.0** -- `nvd.nist.gov/vuln/detail/CVE-2026-54058`.
- **starlette CVE-2026-48710** CONFIRMED CVSS 6.5 and **in the CISA KEV catalog** (added 2026-09-02) -- `cisa.gov/known-exploited-vulnerabilities-catalog`.
- **Caddy**: 2.11.9 is in the box's own apt cache (box:53) = the configured Caddy repo offers it; GHSA-6365-7ppr-5r92 confirmed fixed in 2.11.5 (<=2.11.9). **NEW RISK for the reload**: 2.11.6-2.11.9 change behavior -- headers with `_`/`.` dropped by default, stricter config validation (2.11.8), 1-min idle timeout default-on. **Batch 1 must `caddy validate` the live config with the new binary BEFORE reload** and confirm the webhook-HMAC / forward_auth header path is unaffected.
- **Authelia**: 4.39.19 -> 4.39.28 has **no breaking config changes** (reverify: authelia releases; one cosmetic note -- 4.39.23 drops an obsolete `website` attribute, ignored if absent). Binary swap is safe for tonight.

**A6 -- Git provenance of commit d35aacf4.** Branch **`security-patch-review-2026-11-23`** (local worktree off prod-live). The commit contains **13 additive files, all under `analysis/`** (my review artifacts: probe outputs, az JSON, the two runners, the mirrored report) -- 1962 insertions, 0 deletions, **0 tracked prod-live files modified**. The commit is on **no remote** (`git branch -r --contains` empty) and is **not an ancestor of origin/prod-live** (`merge-base --is-ancestor` exit 1). **prod-live and main are untouched by me.** Creating the worktree branch was the sanctioned "new worktree branch" for this task. Anomaly to record: **origin/prod-live independently advanced cf035381 -> 03a83df1 during the session** (fetch 2026-11-23 19:52 local) -- two commits `9fc27f4e` (MACE holiday/early-close awareness) + `03a83df1` (deploy_log), dated 2026-11-24 00:43Z = **someone else's MACE deploy tonight**, not mine; likely restarted the engine. Not a blocker for Batch 0/1 (box binaries, not repo code). Jack decides whether to keep d35aacf4; not deleted.

**A7 -- NEW intel from Authelia logs (inv:78-83).** The box is under **active hostile reconnaissance**: a probe for **`/.git/HEAD`** from `85.11.167.160` was **correctly 401'd/redirected by Authelia** (inv:80) -- good (the repo is not exposed), but it confirms live scanning. A **second internet-facing subdomain, `predictions.jacksumner.com`** (the PM Streamlit web), is **also Authelia-gated** (inv:81,83) -- so Caddy+Authelia protect more than one app. This *raises* the value of the Batch-1 Caddy/Authelia patches.

**B1 -- VNet neighbors: NONE.** `tc-prod-vm` is the **only VM in the subscription**; it is **alone on `tc-prod-subnet`** (10.0.0.0/24, 1 NIC), VNet `tc-prod-vnet` (10.0.0.0/16) has **no peerings** (vnet). => **M5 (`0.0.0.0:8000`) and M6 (xrdp:3389) stay MEDIUM** (defense-in-depth, no co-tenant). Caveat: an empty **`AzureBastionSubnet`** (10.0.1.0/26, 0 hosts) exists; if a Bastion is ever deployed there it could reach 8000/3389 intra-VNet -- binding 127.0.0.1 + removing xrdp closes that regardless.

**B2 -- Authelia auth backend: PENDING.** Config `/etc/authelia/configuration.yml` is **root-only (unreadable as azureuser)** (inv:75-76). A read-only `az vm run-command` reader (emits only `HAS_LDAP`/`HAS_FILE`, never config contents) is prepared for Jack's authorization. Not a Batch-1 blocker: the 4.39.28 upgrade fixes both Authelia CVEs regardless of backend; the LDAP-only CVE-2026-47203 (Moderate) *applies only if* the backend is LDAP.

**B3 -- xrdp is DORMANT leftover.** Installed **2026-08-25 19:00:53** via `apt install -y xfce4 xfce4-goodies firefox xrdp` -- a full XFCE desktop + Firefox snap (inv:88,91). **Used once** that day (single azureuser RDP session 21:54, inv:100-102); logs frozen since 2026-08-27 (inv:98-99); **nothing depends on it** (`rdepends` empty, inv:94-96); **no active 3389 session now** (inv:103-104). The headless Playwright/Fidelity path uses **xvfb**, not this desktop. => M6 confirmed: disable/remove xrdp (and the unused XFCE/Firefox desktop is removable bloat + surface) in Batch 5.

**B4 -- web3 NOT reachable by any live division -> downgrade to LOW.** **No prod code imports web3** (`import web3`/`from web3` grep empty, inv:106) -- it is a transitive dep of the Polymarket client. The running `--live-divisions` are **bitunix_sfp, robinhood_pead, bitunix_futures, kalshi_copy_trading, robinhood_pmcc, robinhood_mace** (inv:110-115); **none touch Polymarket/web3**. The CCIP SSRF (already CVSS 1.7) is therefore **not live-exploitable**. **M3 moved MEDIUM -> LOW.** Interim fix (Batch 2, optional): disable CCIP-read in the Polymarket client, or drop web3 when the Polymarket path is next worked; no urgent action.

---

## Executive summary (5 lines)
1. **Perimeter is tight and that is the headline: the only Internet-reachable ports are 80/443 -> Caddy; SSH (22) is locked to one home /32; RDP (3389) and the app (8000) are NOT allowed inbound** (Azure default-deny) (az). No actively-internet-exposed, unauthenticated finding exists.
2. The real internet attack surface is therefore **Caddy 2.11.4 and Authelia 4.39.19** -- both are behind on security releases (Caddy forward_auth race; Authelia ACL-canonicalization bypass) and are the top priority (box:179-182).
3. The box is **88 days up with a reboot pending**: running kernel `6.8.0-1064-azure` is ~5 USNs behind the already-installed `6.8.0-1073`, and `libc6`/glibc is updated-but-not-active -- a reboot window is required (box:24,27-45).
4. The app's own stack carries a **CISA-KEV web vuln (Starlette 1.0.0 BadHost, CVE-2026-48710, CVSS 6.5)** reachable via the un-authed TradingView webhook path, plus JWT-forgery (PyJWT); the web3 SSRF is **re-rated LOW** (not reachable by any live division, see B4) (box:384,357,400).
5. Host hygiene: **not attached to Ubuntu Pro** (25 esm-apps security updates ungated; standard support ends Apr 2027), disk 83% with 4 GB of journald + stale `pm_*` backups to reclaim (box:109-127,425,429,434-442).

---

## Findings (prioritized)

### HIGH
| # | Item | Current | Fixed/Target | CVE / advisory | Exposure | Risk (incl. AI-assisted attacker) | Fix |
|---|---|---|---|---|---|---|---|
| H1 | **Caddy** reverse proxy (TLS term + forward_auth) **[REMEDIATED 2026-11-24 -> v2.11.9 active]** | v2.11.4 (box:179) | v2.11.9 (apt, box:53) | GHSA-6365-7ppr-5r92 (Moderate, fixed 2.11.5); GHSA-r766-rf9f-588h (High, fixed 2.11.8) -- `github.com/caddyserver/caddy/security/advisories` | **Internet-facing (80/443, src=Internet)** (az) | forward_auth+reverse_proxy race can route an auth check to the wrong upstream -> intermittent auth-bypass of the dashboard, which fronts live order-execution routes. An LLM-assisted attacker mass-scans for the exact Caddy+forward_auth fingerprint and replays the race automatically. | `apt upgrade caddy` to 2.11.9 + `systemctl reload caddy` (root; web-only blip). |
| H2 | **Authelia** (sole auth gateway) **[Phase-2 re-rate -> MEDIUM: CVE is Low 1.3 per A1] [REMEDIATED 2026-11-24 -> v4.39.28 active+healthy]** | v4.39.19 (box:180) | v4.39.28 | CVE-2026-48794 / GHSA-j748-h363-wqj8 (ACL domain canonicalization miss -> possible 1FA downgrade, fixed 4.39.20) -- `github.com/authelia/authelia/security/advisories` | **Internet-facing** (gates all of `trading.jacksumner.com`) (az) | Uppercase-subdomain request can fall through to a more-permissive access-control rule = auth-bypass class on the one component standing between the Internet and the trading UI. 9 releases behind. AI attacker enumerates ACL case-variants at machine speed. | Replace `/usr/local/bin/authelia` binary with 4.39.28 (verify checksum/signature) + `systemctl restart authelia` (root; auth blip, no trading impact). |
| H3 | **Starlette** (under FastAPI) **[REMEDIATED 2026-11-25 Batch 2 -> 1.0.1 in venv + pinned in requirements.lock/.txt]** | 1.0.0 (box:384) | 1.0.1 | CVE-2026-48710 "BadHost" / GHSA-86qp-5c8j-p5mr -- **in CISA KEV (actively exploited)** -- `github.com/advisories/GHSA-86qp-5c8j-p5mr` (verify KEV listing) | **Internet-reachable via the Caddy webhook bypass** (`/webhook/tradingview/*` skip Authelia) (Web&Auth note) | Host header with `/` or `?` makes path-based middleware see a different path than the router -> auth/validation bypass. Reachable pre-auth on the webhook paths. KEV = exploit code is public; automated scanners already weaponize it. | `venv/bin/pip install 'starlette>=1.0.1'` (+ fastapi, see H/M below); **engine restart required** -> Robinhood-closed window. |
| H4 | **Kernel + glibc reboot pending** **[REMEDIATED 2026-11-25 Batch 4: rebooted to 6.8.0-1074-azure + libc6 activated; reboot-required cleared]** | running `6.8.0-1064-azure`, 88d uptime; `libc6 2.35-0ubuntu3.18` loaded (box:24,199) | boot installed `6.8.0-1073` + activate new libc6 (box:29,43-45) | USN-8761-3/8817-1/8904-1/9026-1 (linux-azure) -- `ubuntu.com/security/notices` | Local (post-access) + availability | 5 kernel USNs' worth of fixes are installed-but-not-running; glibc patched-but-not-active. *Note:* the ARM-TLB LPE (CVE-2025-10263) and AMD-SEV (CVE-2023-20585) do **NOT** apply to this x86-64 Intel D2s_v3; driver is the aggregate + glibc + 88d uptime, not those two. Availability risk: an unplanned reboot (crash) on a 24/7 money-mover is worse than a planned one. | `reboot` in a planned window (root); bounces box + all divisions (~several min). |

### MEDIUM
| # | Item | Current | Fixed/Target | CVE / advisory | Exposure | Risk | Fix |
|---|---|---|---|---|---|---|---|
| M1 | **Not on Ubuntu Pro** | not attached; 25 esm-apps security updates pending; std support ends **Apr 2027** (box:120,126,118-119) | attach free Pro (esm-infra+esm-apps to 2032) | `ubuntu.com/about/release-cycle` | Host | Universe/Multiverse security fixes (incl. the **xrdp** fix, M6) are ungated only with Pro; after Apr 2027 Main/Restricted stops too. AI-assisted attacker favors known-unpatched esm-apps packages. | `pro attach <token>` (root); free for <=5 machines. Jack decision. |
| M2 | **CPython interpreter** (prod venv; from deadsnakes PPA = 3rd-party) **[REMEDIATED 2026-11-25 Batch 2 -> 3.12.16; engine restarted onto it]** | 3.12.13 (box:241) | 3.12.16 (apt, box:81) | CVE-2026-19445 (ssl UAF in SNI cb), CVE-2026-15806 (HTTPS creds over HTTP redirect), CVE-2026-115557 (ssl CA denylist), + XML/tarfile/zip-bomb -- `python.org/downloads/release/python-3121{4,5,6}` | Network-reachable (TLS/HTTP/XML paths) | UAF in the SSL SNI callback and credential-leak-on-redirect are reachable from the engine's normal outbound TLS/HTTP to brokers/data. | `apt upgrade python3.12*` (root); venv uses system libpython3.12 -> **engine restart** to load new stdlib. |
| M3->**LOW** | **web3 CCIP-read SSRF** **[Phase-2 re-rate -> LOW per B4: no prod code imports web3 + no live division touches Polymarket; CVE is 1.7 Low per A5]** | web3==6.11.0 (box:400) | 7.15.0 (MAJOR bump) | CVE-2026-40072 (CVSS 1.7, ApeWorX/web3.py) -- `osv.dev/vulnerability/CVE-2026-40072` | **Not live-reachable** (inv:106,110-115); outbound unrestricted (az) | Theoretical: CCIP-read default-on could fetch `169.254.169.254` (IMDS) -> managed-identity token -> Key Vault, BUT no live division reaches the Polymarket/web3 path so it is not exploitable today. **Do NOT egress-block IMDS** (breaks Key Vault, see A2). | Optional Batch-2 code change: disable CCIP-read or drop web3 when the Polymarket path is next worked. No urgent action. |
| M4 | **PyJWT** **[REMEDIATED 2026-11-26 Batch 2b -> 2.15.1]** | 2.12.1 (box:357) | 2.13.0 | CVE-2026-48526 (RSA-pub-as-HMAC forgery), CVE-2026-48523 (alg allowlist bypass) -- `github.com/advisories/GHSA-xgmm-8j9v-c9wx` | Reachability-dependent (any externally-sourced JWT verify) | Complete token forgery if a public key is accepted as an HMAC secret. Impact depends on whether the app verifies untrusted JWTs (vs only internal). | `venv/bin/pip install 'pyjwt>=2.13.0'`; engine restart. |
| M5 | **App binds `0.0.0.0:8000`** **[REMEDIATED 2026-11-25 Batch 2: main.py:2799 -> 127.0.0.1; ss confirms 127.0.0.1:8000 only]** | uvicorn on `0.0.0.0:8000` (box:157) | bind `127.0.0.1:8000` | config/defense-in-depth | **NOT internet-exposed** (NSG denies 8000) but **intra-VNet reachable** (az) | Any co-tenant VM on the VNet, or any future NSG slip, reaches the full trading dashboard -- incl. order-exec POSTs -- with **no auth** (Authelia is only enforced at Caddy). Zero-trust violation. | Set uvicorn host to 127.0.0.1 (code/systemd); engine restart. (Confirm no other VNet VM -- see Questions.) |
| M6 | **xrdp / RDP listening `*:3389`** on a headless trading box **[REMEDIATED 2026-11-24: stop+disable+mask; 3389/3350 closed; not purged]** | 0.9.17-2ubuntu3 (box:89,162) | 0.9.17-2ubuntu3.1 (ESM) + ideally remove | USN-8476-1: CVE-2024-39917 (MaxLoginRetry not enforced), CVE-2025-68670, CVE-2023-42822/40184 -- `ubuntu.com/security/notices/USN-8476-1` | **NOT internet-exposed** (NSG denies 3389); intra-VNet only | Needless attack surface + brute-forceable (login-retry cap unenforced) + patch is Pro-gated. No reason for RDP on this VM. | `systemctl disable --now xrdp xrdp-sesman` and/or `apt purge xrdp` (root). Decide why it exists first. |
| M7 | **cryptography / aiohttp** (broker/data clients) **[HELD 2026-11-26 Batch 2b: cryptography blocked by `msal<49`; aiohttp forces yarl 1.25.1 -- both need a scope-widening follow-up batch]** | cryptography 47.0.0 (box:273); aiohttp 3.13.5 (box:250) | cryptography 49.0.0; aiohttp 3.14.4 | CVE-2026-69248 (wildcard-SAN name-constraint bypass); CVE-2026-69244 (aiohttp C-parser OOB read) -- `osv.dev` / `github.com/aio-libs/aiohttp/security/advisories` | Network client paths | Cert name-constraint bypass weakens TLS verification; aiohttp OOB read crashes the client on a malicious response. Mostly DoS/verification-weakening, not RCE. | pip bump in venv; engine restart; test (cryptography 47->49 is a notable jump). |
| M8 | **Disk / log hygiene** **[REMEDIATED 2026-11-25: journald `SystemMaxUse=2G` (was 3.9G uncapped), pm_* Tier A deleted (753.5 MB), disk 84%->76%; 14-day retention pending PM-chat log cut]** | `/` 80% (13G free, 2026-11-24); journald **1008M capped**; `pm_*` backups 4.3G (`14_`) | <75%, journald capped (done), backups pruned (pending) | availability | Host | A 24/7 trader that fills `/` stops writing SQLite WAL/audit. journald now bounded by `SystemMaxUse=1G` drop-in; pm_* prune (~0.78-3.1G reclaim) awaits Jack's ruling. | journald DONE; pm_* prune per `19_pm_backups_proposal.md`. |

### LOW
| # | Item | Current | Note | Fix |
|---|---|---|---|---|
| L1 | OS security debs behind (non-kernel) **[REMEDIATED 2026-11-24 Batch 3: 26 debs upgraded]** | krb5 0.8->0.10, glib/chrony/snapd/open-vm-tools/netplan/misc (box:54-88) | Patched via Batch 3 (`16_`); nothing restarted. | done |
| L2 | **Trusted Launch off** | `securityType` empty on a gen2 image (az) | No vTPM/secure-boot/boot-integrity at the Azure layer. | Enable Trusted Launch (requires VM reconfig/redeploy; plan). |
| L3 | encryptionAtHost off | empty (az); OS disk Standard_LRS, platform SSE still on by default | Temp/cache disk not encrypted at host. | Enable encryption-at-host (redeploy). |
| L4 | urllib3 / pillow / fastapi **[urllib3->2.8.0 + pillow->12.3.0 REMEDIATED 2026-11-26 Batch 2b; fastapi HELD (forces starlette off 1.0.1 + opentelemetry)]** | urllib3 2.6.3->2.8.0, pillow 12.2.0->12.3.0, fastapi 0.136.1 (DoS) HELD | Client-side DoS class. | urllib3/pillow done; fastapi needs the starlette-1.6.0 + opentelemetry follow-up. |
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
| Caddy | **2.11.9 (DONE 2026-11-24)** | 2.11.9 |
| Authelia | **4.39.28 (DONE 2026-11-24)** | 4.39.28 |
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

**Batch 1 -- Internet-facing proxy/auth -- DONE 2026-11-24.** Caddy 2.11.4 -> 2.11.9 (validate-before-install + graceful reload) and Authelia 4.39.19 -> 4.39.28 (sha256 + narrow validate-config gate + /api/health 200 + 0 errors-since-start, auto-rollback armed). Ran via `az vm run-command` on staged+hash-verified `secrev_batch1.sh`; trading-corp NOT restarted. Verified: caddy 2.11.9 + authelia 4.39.28 active+healthy, dashboard 302, webhook 405, Host 400. Evidence `13_batch1_result.md`. Rollback: snapshot `tc-prod-os-20261124` + `secrev_batch1_rollback.sh` (pinned original).

**Batch 2 -- App dependency security + bind -- DONE 2026-11-25 03:37Z (CODE deploy, prod-live FF `50aea132->f742c377`; `20_`/`21_`).** starlette 1.0.0->1.0.1 (pinned requirements.lock+.txt) + fastapi 0.136.1 unchanged + python3.12 3.12.13->3.12.16 + main.py:2799 bind 127.0.0.1; one engine restart; verified GREEN (see STATUS). Minimal-KEV variant executed; Batch-2b (below) still deferred. Historical plan:
- **Minimal set first:** `starlette>=1.0.1` (CVE-2026-48710 BadHost, CISA KEV) **plus whatever FastAPI pin that Starlette requires** (test in a scratch venv ON THE PC first; do not blanket-bump fastapi). Keep `~/pip_pre.txt` baseline.
- **Bind uvicorn to 127.0.0.1** (M5) at the same engine restart (code/systemd one-liner) so the dashboard stops listening on 0.0.0.0:8000.
- **Deferred to a later Batch-2b (not the minimal KEV pass):** pyjwt>=2.13.0, cryptography>=49.0.0, aiohttp>=3.14.4, urllib3>=**2.7.0** (fix ver corrected per A5), pillow>=12.3.0, fastapi>=0.163.2 (DoS). web3 6->7 EXCLUDED (breaking; M3 dev task; note M3 is LOW/not-live-reachable per B4).
- Restart engine via canonical `restart_tc.ps1` (Jack). Verify: `/healthz` 200; boot log shows all divisions armed; `pip freeze` shows targets; app bound 127.0.0.1; smoke PM/web paths.

**Batch 3 -- OS security debs, non-kernel -- DONE 2026-11-24 (`16_`).** 26 debs upgraded (chrony/krb5/glib/snapd/open-vm-tools/netplan/iproute2/libaudit/open-iscsi/xfsprogs/tmux/nano/etc.) via `NEEDRESTART_MODE=l` (restarted nothing); all 6 protected PIDs unchanged. **python3.12\* was NOT included** (deadsnakes; moved to the Batch 2 weekend restart with the engine); kernel/libc6 held for Batch 4. Remaining upgradable=12 (= the excluded kernel+python3.12*+xrdp).

**Batch 4 -- Kernel + glibc + reboot -- DONE 2026-11-25 04:04Z (`23_`).** `apt --only-upgrade linux-azure` -> **6.8.0-1074-azure** installed; libc6 (already installed) activated by the reboot; rebooted via a **systemd transient timer** (`systemd-run --on-active=20 systemctl reboot`) -- the first `nohup &` approach was silently killed by az run-command's process cleanup (script never-ran log-absent on the first `!`, then reboot-didn't-fire until the systemd-timer fix). After boot: `uname -r`=1074, reboot-required CLEARED, all units active, engine 0 tracebacks, 6 divisions up, bind 127.0.0.1, crontab intact.
- Verify: `uname -r`=6.8.0-1073; `[ -f /var/run/reboot-required ]` gone; engine + Caddy + Authelia back; `/healthz` 200; division boot-reconcile clean.

**Batch 5 -- Hardening/hygiene (split; `17_`).**
- **DONE 2026-11-24:** xrdp stop+disable+**mask** (M6; 3389/3350 closed, not purged); journald **SystemMaxUse=1G** drop-in + vacuum (M8; 3.9G->1008M, disk 84%->80%).
- **Pending:** bind app to 127.0.0.1 (M5) -- fold into the Batch-2 weekend engine restart (draft `18_`: `main.py:2799`). pm_* backup prune (M8) -- proposal `19_` awaiting Jack's ruling. `pro attach` (M1) -- needs Jack's token. NSG SSH de-dupe (L6) -- the two rules are functional-but-NOT-exact duplicates (same match, different name+priority) so per Jack's rule they were reported, not changed. Optional: `systemctl reset-failed xrdp-sesman` to clear its cosmetic failed state.

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
