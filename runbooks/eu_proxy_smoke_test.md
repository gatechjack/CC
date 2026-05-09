# EU egress smoke test for Polymarket — Phase 0.5 Stage 1

**Purpose.** Polymarket geo-blocks US IPs. Before standing up a persistent
EU proxy VM ($12/mo, ongoing operational surface), verify that an EU IP
is *sufficient* to reach the Polymarket endpoints we need. If Polymarket
fingerprints beyond IP (Cloudflare bot detection, wallet-address
heuristics, cookies), the proxy approach is dead and the rest of the
Polymarket scope must be rethought.

This runbook is **executable today** and produces a binary go/no-go for
Phase 0.5 Stage 2 (the persistent proxy).

**Time:** ~30 minutes. **Cost:** <$0.10 (one B1s VM hour).
**You execute on:** your laptop (Azure CLI) + the throwaway EU VM (SSH)
+ tc-prod-vm (SSH).

---

## What we're testing

Three Polymarket public-read hosts (the surface Phase 1 + Phase 2 will
hit):

| Host | What we'll need it for |
|---|---|
| `gamma-api.polymarket.com` | Market list, slug→token_id, category, volume, end_date |
| `clob.polymarket.com` | Order book reads, last trade price |
| `data-api.polymarket.com` | Wallet position queries (best-guess host; verify it exists) |

Plus one control:

| Host | What we'll confirm |
|---|---|
| Alchemy Polygon RPC (`POLYGON-RPC-URL` in KV) | Geo-anywhere — works from US too. Cash-side `snapshot()` reads land here, no proxy needed. |

**Success pattern** (what we expect to see):
- US (tc-prod-vm) → 403 / 451 / redirect-to-blocked-page on the three
  Polymarket hosts; 200 OK on Alchemy.
- EU (throwaway VM) → 200 OK on all four.

**Failure patterns** (what stops Stage 2):
- EU also gets blocked → likely Cloudflare bot detection or wallet-
  fingerprint heuristics; proxy doesn't fix either. Abandon EU egress.
- Alchemy blocks US → unexpected; reconsider RPC provider before Phase 1.
- Inconsistent results across multiple curls → flag-and-investigate
  before committing.

---

## Prerequisites

1. **Azure CLI logged in** (`az login` — uses your existing admin creds).
2. **Current tc-prod-vm reachable via SSH** (`ssh azureuser@trading.jacksumner.com`).
3. **Your current laptop public IP** (for SSH to the throwaway VM):
   ```bash
   curl -s https://api.ipify.org
   ```
   Save it as `LAPTOP_IP` for the commands below.

---

## Stage 1a — Spin up throwaway EU VM

One B1s VM in `northeurope` (Dublin), no public IP cost (uses dynamic),
deleted within an hour.

```bash
# --- copy-paste, edit LAPTOP_IP ---
LAPTOP_IP="<paste your IP from curl https://api.ipify.org>"
RG="rg-eu-smoke-test"
VM_NAME="eu-smoke"
LOCATION="northeurope"

# Resource group (will be deleted whole at teardown — clean blast radius)
az group create --name "$RG" --location "$LOCATION"

# VM with public IP + SSH locked to your laptop only
az vm create \
  --resource-group "$RG" \
  --name "$VM_NAME" \
  --image Ubuntu2404 \
  --size Standard_B1s \
  --admin-username azureuser \
  --generate-ssh-keys \
  --public-ip-sku Standard \
  --nsg-rule SSH \
  --location "$LOCATION"

# Tighten the auto-created NSG: SSH from your laptop ONLY
NSG_NAME=$(az network nsg list --resource-group "$RG" --query '[0].name' -o tsv)
az network nsg rule update \
  --resource-group "$RG" \
  --nsg-name "$NSG_NAME" \
  --name default-allow-ssh \
  --source-address-prefixes "$LAPTOP_IP/32"

# Get the VM's public IP — record it for the SSH step below
EU_VM_IP=$(az vm show -d --resource-group "$RG" --name "$VM_NAME" --query publicIps -o tsv)
echo "EU VM IP: $EU_VM_IP"
```

**Verify NSG is locked:** `az network nsg rule show --resource-group "$RG" --nsg-name "$NSG_NAME" --name default-allow-ssh --query sourceAddressPrefixes -o tsv` should print just your laptop's IP, not `*` or `Internet`.

---

## Stage 1b — Smoke test from the EU VM

SSH in:

```bash
ssh -o StrictHostKeyChecking=no azureuser@$EU_VM_IP
```

Inside the EU VM, run the smoke script. Save its output verbatim — you'll diff it against tc-prod-vm later.

```bash
# --- run inside EU VM ---
mkdir -p /tmp/smoke && cd /tmp/smoke

probe() {
  local label="$1"
  local url="$2"
  local extra="$3"   # optional curl flags (e.g. -X POST -d '...' -H '...')
  echo "=== $label ==="
  echo "URL: $url"
  # -s silent, -o body file, -w status+timing summary, -m 15s timeout
  eval curl -sS -o "body_${label}" -w '"status=%{http_code} ip=%{remote_ip} time=%{time_total}s\n"' -m 15 $extra "'$url'" 2>&1
  echo "--- first 500 chars of body ---"
  head -c 500 "body_${label}" 2>/dev/null
  echo
  echo
}

# Polymarket Gamma API — list one market (smallest possible probe)
probe gamma "https://gamma-api.polymarket.com/markets?limit=1"

# Polymarket CLOB API — root + a known-public read
probe clob_root "https://clob.polymarket.com/"
probe clob_markets "https://clob.polymarket.com/markets?limit=1"

# Polymarket Data API — verify host exists (no wallet yet, expect 4xx
# with a structured body, not a geo-block 451). Uses a known whale's
# public address (a Polymarket leaderboard wallet) just to land on a
# plausible path. If this host doesn't exist we'll see a DNS or 404.
probe data_api "https://data-api.polymarket.com/positions?user=0x0000000000000000000000000000000000000001"

# Alchemy Polygon RPC control (replace with the actual URL when running)
# We're testing the host responds, NOT testing Polymarket-the-app.
# Cleanest probe: eth_chainId — no auth header needed beyond the URL itself.
ALCHEMY_URL="<paste full POLYGON-RPC-URL here, including the /v2/<key> path>"
probe alchemy_chainid "$ALCHEMY_URL" "-X POST -H 'Content-Type: application/json' -d '{\"jsonrpc\":\"2.0\",\"method\":\"eth_chainId\",\"params\":[],\"id\":1}'"

echo "===== SUMMARY ====="
ls -la
```

**What you're recording:** the status line for each probe (e.g. `status=200 ip=18.232.x.x time=0.34s`) + the first 500 chars of the body. Copy the whole console output to a scratch file on your laptop.

**Stay logged in to EU VM** — you'll re-run the same script from tc-prod-vm in Stage 1c, then compare.

---

## Stage 1c — Same smoke test from tc-prod-vm

Open a second terminal:

```bash
ssh azureuser@trading.jacksumner.com
```

Re-run the **identical** `probe` script from Stage 1b on tc-prod-vm. (Paste the whole block, same Alchemy URL, same probes.) Capture the output.

---

## Stage 1d — Diff + decision

Build a comparison table from the two outputs. Expected:

| Probe | EU VM | tc-prod-vm (US) | Verdict |
|---|---|---|---|
| `gamma` | 200 + JSON market list | 451 / 403 / redirect to /blocked | Geo-block confirmed; needs proxy |
| `clob_root` | 200 / 301 / 308 | 451 / 403 | Geo-block confirmed; needs proxy |
| `clob_markets` | 200 + JSON | 451 / 403 | Geo-block confirmed; needs proxy |
| `data_api` | 200 (or 4xx with JSON body — host exists) | 451 / 403 / DNS fail | If host exists from EU: needs proxy. If DNS fail from both: host doesn't exist; we use on-chain CTF reads instead. |
| `alchemy_chainid` | 200 + `{"result":"0x89"}` (137 = Polygon mainnet) | 200 + same | Geo-anywhere confirmed; no proxy on this leg |

**Go criteria for Stage 2** (all four must hold):
1. All three Polymarket hosts return non-200 from US — confirms the geo-block is real (not a flaky network).
2. At least `gamma-api` AND `clob` return 200 from EU — confirms IP-based unblock works.
3. Alchemy returns 200 from BOTH US and EU — confirms direct-RPC path is fine.
4. No anomalies (Cloudflare challenge pages, suspiciously-fast 200s without bodies, set-cookie chains that look like fingerprinting).

**Edge case — `data_api`:**
- If it 200s from EU but DNS-fails from US: ideal, proxy fixes it.
- If it DNS-fails from both: the host doesn't exist; we'll use on-chain CTF position reads via Alchemy instead (more code in Phase 1, but no extra geo-block exposure). Note this in the report and proceed with Stage 2 — does not block.
- If it returns Cloudflare-challenge HTML from EU: investigate before Stage 2.

---

## Stage 1e — Tear down

**Critical** — don't leave the throwaway VM running. ~$0.01/hour adds up.

```bash
# Run from your laptop, not from the EU VM (you'd kill yourself mid-run)
az group delete --name "rg-eu-smoke-test" --yes --no-wait
```

Wait ~2 min, then verify:

```bash
az group show --name "rg-eu-smoke-test" 2>&1 | grep -i 'could not be found' && echo "OK — torn down"
```

---

## Reporting back

Paste the comparison table (Stage 1d) plus any anomalies into the
session. Include:
- Status code + body-first-500 for each probe, both sides
- Whether the four go-criteria all held
- Any Cloudflare / WAF / cookie patterns spotted
- Your verdict: GO (proceed to Stage 2) / NO-GO (abandon EU egress) /
  GO-WITH-CAVEAT (e.g. data_api host doesn't exist, fall back to CTF)

I'll then either start Stage 2 (proxy VM runbook + secrets/redaction
code) or, on NO-GO, pivot the architectural plan with you.
