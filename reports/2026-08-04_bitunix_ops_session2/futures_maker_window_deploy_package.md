# Futures maker-entry window — deploy-ready package (APPROVED 2026-08-05)

Operator executes the flip at the next deploy window. **Config-only hot-reload flag flip** — the B2
maker code (`ef6fa5f`) is already on prod (PROPOSALS 2026-08-02 confirmed "code live, flag OFF"), so
there is no code deploy, no restart, no pytest/md5-code-sweep gate. Deploy channel = the operator's
prod-write mechanism (Azure `az run-command RunShellScript` root, per the 2026-08-04 PMCC deploy; SSH
is classifier-blocked). Agent did NOT touch prod. **Clock (≥30 entries / 3-week cap) starts at flip.**

## 1. Exact config diff

File `config/strategies.yaml`, under `bitunix_futures: → fees:` (verified block below, worktree
line 1372; confirm the same block on prod before editing — see §2). **Single-line change:**

```diff
     # B2 maker (POST_ONLY) entry execution — DEFAULT OFF (flip deliberately).
-    maker_entry_enabled: false
+    maker_entry_enabled: true
     maker_entry_rest_timeout_s: 2.0
     maker_entry_offset_pct: 0.0
     maker_entry_fallback_mode: cross_to_taker
```

Leave `rest_timeout_s: 2.0`, `offset_pct: 0.0` (join-the-touch), `fallback_mode: cross_to_taker`
unchanged. **Only the `bitunix_futures` block's `maker_entry_enabled` flips.** Do NOT add any
`maker_entry_*` key to the `bitunix_sfp` block (SFP stays taker — that is the isolation guarantee).

## 2. Pre-flip verification (on prod, read-only)

1. **Block parity** — confirm the prod block matches before editing:
   `sed -n '/^bitunix_futures:/,/^[^ ]/p' /home/azureuser/trading_corp/config/strategies.yaml | grep -n maker_entry`
   expect: `maker_entry_enabled: false`, `rest_timeout_s: 2.0`, `offset_pct: 0.0`,
   `fallback_mode: cross_to_taker`.
2. **Maker code present on prod** (so the flip is not a no-op):
   `grep -c "_place_maker_entry\|maker_entry" /home/azureuser/trading_corp/trading_corp/brokers/bitunix.py`
   expect ≥1.
3. **SFP has no maker path** (isolation): `grep -c maker .../agents/divisions/bitunix_sfp_observer.py`
   expect 0.
4. **md5 pre:** `md5sum /home/azureuser/trading_corp/config/strategies.yaml` — record it.
5. Prefer to flip while the **futures account is flat** (no open position) to get a clean first-entry read.

## 3. Apply (hot-reload — NO restart)

`strategies.yaml` is mtime-checked; the futures observer reads `fees.maker_entry_*` per signal, so the
flag applies on the **next futures entry** without a restart. Edit the one line (root, via the deploy
channel), preserving 4-space indent. Then:

- **md5 post:** `md5sum .../config/strategies.yaml` — must differ from §2.4.
- **Confirm value:** `grep -A1 "B2 maker" .../config/strategies.yaml` → `maker_entry_enabled: true`.
- **No restart, no engine bounce** — verify the PID is unchanged and 0 tracebacks after the edit.

## 4. What gets measured (audit-driven, read-only SELECT)

Per futures entry over the window (actor = `bitunix_futures`; adjust kind names to the live audit
vocabulary — confirm with a first `SELECT DISTINCT kind` probe):

- **Fill rate / fallback:** count maker-filled entries vs `BitunixMakerEntryUnfilled` / taker-fallback
  events. `fallback_rate = fallbacks / entries`.
- **Slippage vs taker:** maker fill px vs the signal-ref/touch → realized saving/side (≈0.0004→0.00014).
- **Late/missed entries:** rest-timeout expirations + entry-timing delta vs the taker baseline.
- **Net-R:** maker fee saving − non-fill/late-entry cost, in R.

## 5. Abort criteria (pre-registered — ANY trips → flip back to `false`, hot)

| # | trip | monitor |
|---|---|---|
| 1 | **taker-fallback rate > 40%** (offset 0.0 crosses too often) | fallback events / entries over the window |
| 2 | **any maker-path fault** — unhandled `BitunixMakerEntryUnfilled`, cancel-FAILED double-fill guard, reconciler orphan-stop / `_halt_new_orders` | `journalctl -u trading-corp | grep -iE "maker|orphan|halt_new"` + audit |
| 3 | **net-R below the taker baseline** by more than measured noise after ≥30 entries | the §4 net-R rollup |
| 4 | **any drawdown-cap / halt** on the futures account | `flatten_account` / `*_drawdown_breach_block` audits |

Monitoring cadence: check after the first 3–5 maker entries (catch faults early), then daily.

## 6. Rollback

Flip `maker_entry_enabled: true → false` (same one-line hot-reload). Immediate on any §5 trip; no
restart. The B1 catastrophic stop is taker/market throughout and is never affected.

## 7. How it feeds the n≥30 review

The window yields a measured **fill-rate + slippage + net-R** table on the live futures account
without perturbing the SFP OOS sample (SFP stays taker). A clean positive net-R with acceptable fill
rate de-risks a later SFP maker flip (after SFP's own n≥30 OOS review); a high-fallback / negative
result keeps the SFP flip parked. Record the window's outcome in `runbooks/deploy_log.md` +
`BACKLOG` when the clock closes.
