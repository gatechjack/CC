# Batch 2c -- scoping the 3 HELD libraries (cryptography, aiohttp, fastapi)

**READ-ONLY scoping. No deploy, no restart, no installs on the box.** prod-live `110d735c`; box drift = 0.
Evidence files: `28_`-`31_` (this `analysis/`), dry-run reports `29_`/`30_*.json`, box probe `28_box_probe.txt`.
Staged (not run) deploy artifacts: branch `secrev-batch2c-2026-11-25` `analysis/secrev_batch2c*.sh` + `_PINS.md`.

Bottom line up front: **all three CVEs are LOW / not-reachable in this deployment.** None is urgent. The work is
defense-in-depth. The one that carries real *deploy* risk is cryptography (it drags msal, which sits on the Key
Vault auth path and has zero PC test coverage) -- so it is isolated into its own batch and verified live.

---

## Item 1 -- Auth wording ("0 auth failures"): what the filter excluded, pre vs post deploy

The 2b verify (`27_`) filtered journal lines (unit=`trading-corp`, since the 2b restart) with
pattern `auth.*fail|unauthoriz|\b401\b|\b403\b|invalid.*(key|signature|token)|session.*expir|attestation...`
minus a benign filter `polymarket|data-api|/positions|incomplete chunked|429`. It reported **`count=4`** --
NOT literally zero. "0 REAL auth failures" / "0 auth errors" was a *classification* of those 4. This audit
re-ran the same filter over the post-deploy window **[00:26:07..01:41:31Z, 4524 s]** and an **equal-length
pre-deploy window [23:10:43..00:26:07Z]**. Post had 6 non-benign matches (window grew); every one is benign:

> **FLAG (per instruction -- post-deploy lines absent pre-deploy):** the Azure `Response status: 401` line and
> the 3 `BitUnix ... wiring/gate/guard` lines do **not** appear in the pre-window. Reason: both are **boot-time**
> lines emitted once at engine startup; the pre-window is the *previous* (already-running) instance, which emits
> no fresh boot lines. They are inherent to the restart, not new failures.

| # | excluded/benign line (unit=trading-corp) | why it matched | why NOT a real failure | in PRE window? |
|---|---|---|---|---|
| 1 | `azure.core...http_logging_policy: Response status: 401` (00:26:08) | real `401` in content | DefaultAzureCredential **credential-chain probe** at boot; immediately followed by **42x `Response status: 200`** (Key Vault working). The whole post-window status set = 42x200 / 1x400 / 1x401 / 4x404 -- the 400/401/404 are the benign chain-walk. | **No** (boot-only) -> FLAGGED |
| 2 | `trading_corp.main: BitUnix observer wiring: ...execution_mode=live` (00:26:12) | millisecond field `,401` of the timestamp | INFO **startup wiring** line; no auth content | **No** (boot-only) -> FLAGGED |
| 3 | `trading_corp.main: BitUnix staleness-reject gate (C): ... margin_s=120` | `,401` ms | INFO startup config line | **No** (boot-only) -> FLAGGED |
| 4 | `trading_corp.main: BitUnix concurrent-position guard (D4): enabled=True` | `,401` ms | INFO startup config line | **No** (boot-only) -> FLAGGED |
| 5 | `prediction_markets.live_driver: 1 confirmed whale-EXIT(s) ...` (01:40:20) | `,401` ms | normal PM operational line | **Yes** (4x pre) -- not post-only |
| 6 | `shard_snapshot_task: shard-snapshot kalshi_marc: total=$44.29 ...` (01:41:51) | `,401` ms | normal PM operational line | Yes (periodic) -- not post-only |
| -- | **69** `polymarket-data-api positions[...]` lines (benign-excluded bucket) | number/ms coincidences | explicit benign filter (`data-api`/`positions`); routine PM data reads | Yes (93 in pre) |

**Verdict:** the "0 auth failures" claim is substantively **correct** -- zero real authentication failures post-deploy.
The nuance the raw filter hides: 1 line is a benign Azure credential-chain `401` (by design, followed by 200s),
and 5 are false-positives from the literal `,401` **millisecond** component of the syslog timestamp. The automated
`AUTH ERRORS` counter is noisy on INFO logs and must be eyeballed; it should additionally strip the `HH:MM:SS,NNN`
prefix before matching (recommended tweak).

---

## Item 2 -- fastapi: unauthenticated routes + param-DoS reachability

From the box Caddyfile (`28_box_probe.txt` SECTION_C, redaction applied -- no secrets were present). Three vhosts;
the engine is `localhost:8000`, pm_web is `localhost:8081`. Routes reachable **without** Authelia `forward_auth`:

| route (vhost -> upstream) | handler (file:line) | parses query/form/body via FastAPI params? | DoS-param surface? |
|---|---|---|---|
| `POST /webhook/tradingview/lord-otter` (trading -> :8000) | `webhooks.py:112` `lord_otter_webhook(request: Request, background_tasks)` | **No** -- raw `Request`; `await request.body()` capped at **4 KB** (`_MAX_BODY_BYTES`, webhooks.py:55/169) then `json.loads`; never touches `request.query_params`/`.form()` | No |
| `POST /webhook/tradingview/market-cypher` (-> :8000) | `webhooks.py:308` same signature | **No** -- identical raw-body path | No |
| `GET /healthz` (-> :8000) | `app.py:191` `healthz()` | **No** -- zero params, returns `{"status":"ok"}` | No |
| `GET /sw.js`,`/manifest.webmanifest`,`/offline.html` (-> :8000) | `routes.py:240/259/269` | **No** -- `FileResponse` only | No |
| `GET /static/*` (-> :8000) | `app.py:184` `StaticFiles` mount | **No** -- serves by path | No |
| `GET /healthz` (predictions -> :8081) | `prediction_markets/web/app.py:224` | **No** -- zero params | No |

Everything else (dashboard, `home` with `stage1:int`, all `request.query_params`/`await request.form()` sites in
`mace_view.py`/`routes.py`) is behind Authelia `forward_auth` (argon2id + TOTP). There is **no** `add_middleware`/
`BaseHTTPMiddleware`/`app.middleware` anywhere in `trading_corp`, so nothing parses params for every request.

**Verdict: DoS reachable unauthenticated = NO.** The quadratic-param parser is only invoked on authenticated
dashboard routes; the only unauthenticated routes take a raw `Request` (4 KB cap + `json.loads`) or no params.
Residual: an authenticated user (Jack) could trigger it -- not a meaningful threat -- so the fix is defense-in-depth.

---

## Item 3 -- aiohttp: every import/use, division, host; websocket verdict

**Zero** direct `aiohttp` imports anywhere in the repo (whole-worktree grep: only hit is `runbooks/deploy_log.md`,
a doc). aiohttp is **purely transitive**. Reverse-deps (mirror venv): `aiohttp` required-by **ccxt, web3, anthropic,
azure-core**; `yarl` required-by **aiohttp, ccxt**.

| consumer | how aiohttp is used | division | host | file:line |
|---|---|---|---|---|
| ccxt (`ccxt.async_support`) | REST `ClientSession` | Coinbase broker / data / replay | api.exchange.coinbase.com (trusted, TLS) | `main.py:3517`, `web/data.py:3220`, `brokers/coinbase.py:135`, `agents/paper_trade_replay.py:1173` |
| ccxt (`ccxt.pro`) | **WebSocket** `watch_ticker('BTC/USD')` | path_logger sidecar (market-context-recorder) | Coinbase WS (public, no auth, trusted) | `path_logger/logger.py:495-506` |
| web3 | async HTTP provider | -- (Polymarket CLOB, M3) | -- | **not imported by app** (`from web3`=0 hits); dormant |
| anthropic / azure-core | only under extras (`[aiohttp]`/`[aio]`) | not active | -- | extras not installed |
| (for contrast) Bitunix WS | **`websockets` lib, NOT aiohttp** | engine | `wss://fapi.bitunix.com/public/` | `data/bitunix_ws_feed.py:108` |

**Verdict: are websockets used (ws-overflow CVE path)? YES, but narrowly.** aiohttp's WS transport is used only by
`ccxt.pro` for the **Coinbase public ticker** feed in the path_logger sidecar. The ws-overflow (CVE-2026-128128)
needs the *server* to send an oversized frame beyond `max_msg_size`; the peer is Coinbase over TLS (trusted). The
client CVEs (69244 OOB-read on malformed chunked response, 128124) likewise need a malformed response from a
trusted exchange over TLS. **Low practical reachability.** Clean 2-package bump; no aiohttp *server* anywhere.

---

## Item 4 -- cryptography: X.509-verification use + TLS path; name-constraint verdict

Grep for the vulnerable API across the repo: **zero** `cryptography.x509.verification`, `PolicyBuilder`,
`Verifier`, or bare `import cryptography` (only `Store(` = MACE's own `RungStore`). Reverse-deps (mirror venv):
`cryptography` required-by **PyJWT, azure-identity, ccxt, curl_cffi, msal, pykalshi, python-telegram-bot, robin-stocks**.

| consumer | what it uses cryptography FOR | uses x509 name-constraint verification? |
|---|---|---|
| msal / azure-identity | JWT client-assertion signing, token crypto (Key Vault auth) | No |
| pykalshi | Kalshi request **signing** (RSA) | No |
| robin-stocks / ccrt / PyJWT / python-telegram-bot | signing / JWT / primitives | No |
| curl_cffi | `<47` pin only under `[dev]`/`[test]` extras (**inactive** in prod) | No |

Client TLS: **no** custom `SSLContext`, **no** `certifi` handling, **no `verify=False`** anywhere in `trading_corp`.
requests/httpx/aiohttp/ccxt all use **stdlib `ssl` + OpenSSL** with default trust. Nothing opts into
`cryptography.x509.verification`.

**Verdict: name-constraint CVE (CVE-2026-69248) reachable = NO.** Cert-chain verification (incl. name constraints)
runs in OpenSSL via stdlib `ssl`, not in the `cryptography` package; the vulnerable path-validation API is never
called. Patch as defense-in-depth only.

---

## Item 5 -- Minimum compatible sets (PC mirror venv, pip resolution, hand-verified)

Full prod-mirror venv (python 3.12, 158 pkgs, `pip check` clean). pip `--dry-run` under-reports (it tolerates
installed-but-unnamed conflicts), so every proposed version was re-checked against **every** installed consumer's
specifier (the real `pip check` equivalent). Reports: `29_`/`30_*.json`.

| set | minimum consistent change | companion forced by | verified unchanged |
|---|---|---|---|
| **(a) cryptography>=49** | cryptography 47.0.0 -> **50.0.2** + **msal 1.36.0 -> 1.39.0** | msal 1.36.0 pins `cryptography<49` (the ONLY real blocker; curl_cffi `<47` is dev/test-extra-gated = inactive) | azure-identity 1.25.3, azure-core 1.39.0, azure-keyvault-secrets 4.11.0 (need only `cryptography>=2.5`, accept msal 1.39.0) |
| **(b) aiohttp>=3.14.4** | aiohttp 3.13.5 -> **3.14.5** + yarl 1.23.0 -> **1.25.1** | aiohttp needs `yarl>=1.17,<2.0` | ccxt/web3/anthropic/azure-core all satisfied -- **no other change** |
| **(c) fastapi>=0.163.2** | fastapi 0.136.1 -> **0.165.0** + starlette 1.0.1 -> **1.10.0** + **opentelemetry-api 1.45.2 (NEW)** | fastapi 0.165 needs `starlette>=1.6` + pulls opentelemetry-api | streamlit 1.57 accepts starlette 1.10.0 (`>=0.40`); **starlette 1.10.0 >= 1.0.1 => BadHost KEV stays fixed** |

The vault's feared "azure-identity/azure-core bump" for cryptography is **not needed** -- it is cryptography + msal only.

---

## Item 6 -- Tests (full suite per set vs baseline + path-specific)

Method = same scratch-venv full-suite as 2b: `pytest tests -p no:pytest_ethereum --continue-on-collection-errors
--timeout=90 -q`, run on the mirror venv at baseline, then per set (install -> suite -> revert -> assert baseline).

Baseline = **176 FAILED/ERROR** node-ids (162 FAILED + 14 ERROR) -- all pre-existing (box-resource / collection
errors on a PC mirror, same class as the 2b run; the DELTA is what matters). `31_test_*.{txt,ids}`.

| run | versions active (only these changed) | pip check | FAILED/ERROR ids | **new vs baseline** | fixed |
|---|---|---|---|---|---|
| baseline | current prod (crypto47/msal1.36/aiohttp3.13.5/yarl1.23/fastapi0.136.1/starlette1.0.1) | clean | **176** | -- | -- |
| **set a** | cryptography **50.0.2** + msal **1.39.0** | clean | **176** | **0** | 0 |
| **set b** | aiohttp **3.14.5** + yarl **1.25.1** | clean | **176** | **0** | 0 |
| **set c** | fastapi **0.165.0** + starlette **1.10.0** + opentelemetry-api **1.45.2** | clean | **176** | **0** | 0 |

**Every set is byte-identical to the baseline failure set -- 0 new failures, 0 regressions, pip check clean.**
(The suite reverted to baseline + re-asserted between sets.) Matches the 2b finding method exactly.

**Path-specific coverage (item 6B):**

| touched path | test files | exercised on PC? |
|---|---|---|
| FastAPI routes / webhooks (set c) | `test_webhooks_return_fast.py`, `test_webhook_audit_trail.py`, `test_signal_replay.py`, `test_approvals_routes.py`, many `prediction_markets/*` via `fastapi.testclient.TestClient` | **Yes -- strong.** Route layer end-to-end |
| aiohttp / ccxt clients (set b) | `test_coinbase_btc_donchian_agent.py`, `test_paper_trade_replay.py`; `test_bitunix_ws_feed.py` covers the *websockets*-lib feed | **Weak/indirect** -- ccxt HTTP boundary is mocked (`httpx.AsyncClient` stubs); live aiohttp path not exercised. Import/boot smoke only |
| Key Vault / msal / managed identity (set a) | -- | **NONE** -- zero `DefaultAzureCredential`/`SecretClient`/`msal` refs in tests (needs IMDS). **Live-only** verification (box: 2b saw ~277 KV successes) |

---

## Item 7 -- Recommendation

All three CVEs are low/not-reachable, so sequencing is driven by **deploy risk**, not urgency.

| priority | batch | libraries | reachability (items 2-4) | deploy risk | test coverage | restarts | rollback | post-deploy verification |
|---|---|---|---|---|---|---|---|---|
| 1 | **2c-1** (combine b+c) | aiohttp+yarl, fastapi+starlette (+opentelemetry-api) | aiohttp CVEs need malformed/oversized data from a trusted exchange (low); fastapi DoS not reachable unauth | **Low** -- no scope-widen (b) / strong coverage (c); starlette stays KEV-safe | b weak (mocked), **c strong** | 1 cycle (all 4 services, shared venv) | auto-restore on gate fail; else `secrev_batch2c_rollback.sh <bk>` | `/healthz` 200 (:8000 & :8081), dashboard 302, webhook GET->405, 0 tracebacks, ccxt marks + Coinbase WS reconnect |
| 2 | **2c-2** (isolate a) | cryptography + msal | x509 name-constraint not reachable (TLS via OpenSSL) | **Higher** -- msal is on the Key Vault auth path; **no PC coverage** | **none** (live-only) | 1 cycle (all 4) | same | **Key Vault fetch burst** (azure `Response status: 200` x~hundreds; 1 benign chain-401 OK) + RH/Bitunix/Kalshi broker auth + 0 msal errors |

- **Combine vs separate:** combine the two low-risk sets (2c-1); **isolate cryptography+msal (2c-2)** so a Key
  Vault regression is cleanly attributable and independently reversible. Run 2c-2 only after 2c-1 is stable.
  (Alternative if minimizing restarts dominates: one combined batch, 1 restart -- but loses attribution.)
- **Restarts:** all 4 app services share `/home/azureuser/trading_corp/venv`, so each batch bounces all 4 once to
  avoid mixed in-memory/on-disk lib state. 2 cycles total for the 2-batch plan.
- **Rollback:** each deploy script snapshots `pip freeze` pre-install and auto-restores on any gate/verify failure;
  manual post-deploy rollback = `secrev_batch2c_rollback.sh <backup_dir>` (reinstall exact pre-freeze + restart).
  OS-disk snapshot `tc-prod-os-20261124c` remains as the coarse fallback.
- **Timing:** market-closed, Jack sets it; FF to prod-live reserved for Jack. Staged, not run.
