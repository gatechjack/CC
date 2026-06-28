# Prod egress IP swap — Step-1 assessment + runbook preview (2026-06-27)

Goal: give prod a NEW, clean egress IP so Bitunix/Cloudflare stops bot-challenging it, WITHOUT losing
inbound (SSH/DNS/web) or disrupting the shared LB. Root cause recap: Cloudflare **managed challenge**
(`cf-mitigated: challenge`, "Just a moment…") on our specific IP `20.51.145.253` — not cloud-wide
(16 global datacenter nodes pass), most likely IP-reputation from the engine's relentless polling.

## STEP 1 — READ-ONLY FINDINGS

### Confirmed from the VM (IMDS + DNS)
- `20.51.145.253` is **ONE IP doing three jobs**: LB **frontend** (IMDS loadbalancer), **inbound DNS**
  (`trading.jacksumner.com → 20.51.145.253`, confirmed), and **outbound SNAT** (egress). They are the
  same address.
- VM `tc-prod-vm`, region **eastus**, `rg-shared-prod`, NIC `eth0` private **10.0.0.4/24** (subnet
  10.0.0.0/24), **no public IP on the NIC** — public IP lives on the LB.
- ⟹ **Swapping the frontend IP would break inbound** (DNS + SSH + web) until DNS is repointed. Risky.

### Could NOT determine (ARM Reader denied to the VM identity → you must enumerate)
The VM's managed identity is **scoped to Key Vault only** — ARM calls return `403 AuthorizationFailed`.
So I cannot see the LB's other backends, what else uses the public IP, or the subnet's members.
**★ BLAST RADIUS IS UNCONFIRMED** — `rg-shared-prod` + "shared" LB naming means other resources may
depend on this LB / IP / subnet. **Enumerate before touching anything.** Read-only commands for you:

```
az network public-ip list -g rg-shared-prod -o table
az network lb list -g rg-shared-prod -o table
az network lb show -g rg-shared-prod -n <LB> --query "{fe:frontendIPConfigurations[].name,be:backendAddressPools[].name,out:outboundRules}"
az network nic show --ids <NIC-id> --query "ipConfigurations[].subnet.id"
az network vnet subnet show --ids <SUBNET-id> --query "{natgw:natGateway.id, members:ipConfigurations[].id}"
az network nat gateway list -g rg-shared-prod -o table
```
The last two are the key ones: **how many NICs share subnet 10.0.0.0/24** (NAT-gw egress blast radius)
and **whether a NAT gateway already exists**.

### ★ The clean answer: NAT gateway for egress only (don't swap the frontend)
A **NAT gateway** on the VM's subnet takes precedence over LB outbound SNAT for egress, giving a NEW
egress IP **while leaving the LB frontend + inbound DNS/SSH on `20.51.145.253` untouched** — no DNS
change, no SSH disruption, no inbound risk. This is preferred over swapping the shared frontend.
- **Caveat:** a NAT gateway attaches to the **subnet**, so **every VM in 10.0.0.0/24 egresses via the
  new IP**. If the subnet is shared with VMs that must keep their egress, either (a) move tc-prod-vm to
  its own subnet first, or (b) accept the shared egress change. The subnet-members query above decides this.
- If NAT-gw isn't viable, fallback = swap the LB frontend public IP **and** repoint `trading.jacksumner.com`
  DNS + re-verify SSH host key — more disruptive; only if forced.

### Inbound dependency
- `trading.jacksumner.com` A-record → `20.51.145.253`. **NAT-gw approach needs NO DNS change** (inbound
  stays on .253). Frontend-swap approach WOULD need the A-record updated + SSH known-hosts re-accept.

## STEP 2 — RUNBOOK PREVIEW (NAT-gw egress-only; finalize after you enumerate)
Reversible, test-before-commit. Re-bind the key only after the egress tests clean.
1. **Enumerate** (above). Confirm subnet blast radius + no existing NAT gw. STOP if the subnet is shared
   with VMs that must not change egress (switch to per-subnet plan).
2. **Provision** a new Standard **static Public IP** in `rg-shared-prod` (touches nothing yet).
3. **Create a NAT gateway** with that IP (idle, attached to nothing yet).
4. **★ Pre-commit clean-IP test (gold standard):** attach the NAT gw to a *throwaway isolated subnet*
   with a test VM; from it `curl https://fapi.bitunix.com/api/v1/futures/market/kline?...` →
   **expect HTTP 200**. If still challenged → STOP (not pure IP-reputation; rethink). Tear down test.
   *(Lighter alternative: skip the test VM, do step 5 then test from prod, rollback by detaching.)*
5. **Attach NAT gw to the prod subnet** → egress flips to the new IP. Verify on prod:
   `curl ifconfig.me` = new IP; **public kline → 200** (clears the flag; no key needed). Live capture
   should resume on its own.
6. **Re-bind the Bitunix API key** to the **new egress IP** in the Bitunix UI. (Inbound .253 unchanged.)
   *Until this, authed calls will fail the key's IP-allowlist — but the public-kline 200 already
   proves the flag is cleared.*
7. **Verify authed**: snapshot 200, reconciler real (not the swallow-to-[] false-flat).
8. **ROLLBACK** (any step): detach the NAT gw from the subnet → egress reverts to `20.51.145.253`;
   re-bind key back. Inbound never moved, so rollback is clean.

## SEPARATE — BitUnix historical kline-call inventory (retire bulk; DO NOT change yet)
Only **two** code paths hit `/api/v1/futures/market/kline`:
| path | type | verdict |
|---|---|---|
| `live_bar_cache._refresh_bitunix` (live_bar_cache.py:96) | live recent-window poll (`limit=max_bars`, no startTime), on the cache cadence | **KEEP** (live trading needs it). Note: this high-cadence poll is the likeliest flag-trigger — consider gentler cadence later. |
| `paper_trade_replay._bitunix_kline_fetcher` (paper_trade_replay.py:1152) via `_default_router_fetcher` (:1253) | **paginated historical** (startTime/endTime); driven by `start_replay_loop` **every 15 min** + a **startup catch-up** (main.py:1550). Re-walks each pending bitunix *paper* trade's bar window from entry. | **RETIRE / REPOINT** — this re-fetches historical windows repeatedly. ★ Recommended: point it at the local `bitunix_bar_history` table (the one we're backfilling) instead of the Bitunix API → kills the repetitive historical pulls AND uses the data we're building. Live + small outage gap-fill stay on the API. |

`crypto_vol_provider._fetch_bars` is **Coinbase via ccxt** — NOT Bitunix (excluded). No other BitUnix
historical path exists. My throwaway backfill script runs from local, not the engine.

**Net:** after the egress swap, the only BitUnix API traffic that should remain is the live poll
(+ optional small gap-fills). Retiring/repointing the replay fetcher removes the repetitive historical
load that helps keep the new IP's reputation clean.
