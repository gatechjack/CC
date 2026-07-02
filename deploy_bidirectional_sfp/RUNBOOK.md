# RUNBOOK — Regime-Aware Bidirectional SFP deploy (2026-07-01)

**What ships:** `bitunix_sfp` goes from long-only fixed-side to **regime-aware
bidirectional** on all 4 coins (BTC/ETH/SOL/XRP): 15m EMA-200+slope regime picks the
side (long UP/RANGE, short DOWN/RANGE, never counter-trend), short SFPs via M2=0
reflection of the **byte-identical** detector, learn-account sizing (0.05/0.10, lev 10),
an **isolated research-log** catalog, a **regime-flip watch** + cockpit chip, and a **HOT
`config.side` kill-switch** (regime|long|short, no restart). Detector `bitunix_sfp.py`
**unchanged** (`91fd7672`) — all logic is in the observer/reconciler/config.

The operator runs EVERY prod write/restart; each `.ps1` is self-gating and aborts on a
red gate. Base = **a9fb8c6b** (prod==main==`79cbbef`; Kalshi-drift-aware — config drift
is Kalshi-only, the bitunix_sfp block was byte-unchanged pre-deploy `9be416eb`).

## Touched files → installed target md5
Prod DIVERGES from the branch base, so `main.py` + `strategies.yaml` are **targeted-hunk
CRLF hybrids** (prod's exact bytes with ONLY the bitunix_sfp hunk swapped in; built by
`build_hybrids.py`, verified by `verify_hybrids.py`, installed byte-exact — NO `tr`). The
other 5 are prod==base, installed from the worktree CR-stripped to LF. **md5s below are the
INSTALLED-file md5s** (raw for the two hybrids, LF for the five).

| file | install | target md5 |
|---|---|---|
| `trading_corp/main.py` | raw hybrid (prod + cache hunk; +Kalshi leg_priced preserved) | `d0d382cbffcb6ebfbf50372fcc9175dd` |
| `trading_corp/agents/divisions/bitunix_sfp_observer.py` | LF | `1eb85d572674e6a05a6b0fd1ff93ab1f` |
| `trading_corp/agents/divisions/bitunix_position_reconciler.py` | LF | `f54665e8335bb76fd28171c94e3a6dc1` |
| `trading_corp/agents/divisions/bitunix_sfp_research_log.py` **(NEW)** | LF | `b6b1b4469d11e35d5d2d6a42379b878d` |
| `trading_corp/web/sfp_cockpit_view.py` | LF | `143773b74ad60818311e9511aa9cecc9` |
| `trading_corp/web/templates/sfp_cockpit/_state_board.html` | LF | `1cce2d72c38902db3f6b9543b2cd95be` |
| `config/strategies.yaml` | raw hybrid (prod + bitunix_sfp block; +polymarket lines & CRLF preserved) | `12fd6c3f67fe2ec48a59009c7d855679` |
| `trading_corp/agents/strategies/bitunix_sfp.py` **(UNCHANGED — asserted)** | — | `91fd76726364331c8083aaaa68fce199` |

`divisions.yaml` **NOT touched** (SOL/XRP arming is via strategies.yaml symbols+symbol_modes).

> **Hybrids are prod-derived + regenerable** (`build_hybrids.py` fetches prod's live
> main.py/strategies.yaml, applies the hunk, writes `hybrids/`). They are git-ignored (the
> recipe is committed, the blobs are not — rebuild if prod moves; the drift-gate then guards
> that prod still matches what they were built from). Base bug (2026-07-01 first apply): the
> package originally file-copied all 7 → clobbered prod's Kalshi leg_priced fix + polymarket
> lines + CRLF. Caught read-only at the config-diff gate, no restart, restored clean.

## Deploy sequence (operator paste, one line each — ≤100 chars)
```
powershell -ep bypass -f .\preflight.ps1
powershell -ep bypass -f .\apply.ps1
powershell -ep bypass -f .\restart.ps1
powershell -ep bypass -f .\bootsmoke.ps1
```
Rollback (only if needed): `powershell -ep bypass -f .\rollback.ps1`

### 1. preflight.ps1 — read-only gates + drift-gate snapshot
- git `main == origin/main`; **snapshots prod md5s** of the touched files → `preflight_prod_snapshot.txt` (the drift-gate base for apply).
- Detector md5 == `91fd7672`.
- **RH pickle age < 20h** — ELSE run `rh_pickle_refresh.ps1` FIRST (a stale pickle hangs the WHOLE engine on the RH 2FA challenge — caused a ~20min outage 2026-07-01).
- **SFP flat**: `paper_trade_record` open live rows == 0 AND the reconciler line = clean/match_count==0 (NOT a `position WHERE qty!=0` check). `bitunix_futures` may hold its own position — **isolated, does NOT block** an SFP-scoped deploy.

### 2. apply.ps1 — drift-gate → LF-blob install → config/detector gate
- **Drift-gate**: re-md5 prod; abort if it differs from `preflight_prod_snapshot.txt` (prod moved since staging).
- **Install** each file: `scp` byte-copy → `tr -d '\r'` (LF) → **md5 must equal the target**; backs up the prod file to `*.bak-pre-bidir-2026-07-01` first. Aborts (→ rollback) on any md5 mismatch.
- **Config-diff gate 1**: only the `bitunix_sfp` block changed (strategies.yaml lines 1..1928 == the .bak, all other divisions byte-identical) + detector still `91fd7672`.

### 3. restart.ps1 — ONE flat-guarded engine restart
- Re-checks **SFP flat + pickle fresh** inside the remote bash; aborts (no restart) if not flat or pickle stale.
- `sudo -n systemctl restart trading-corp` (engine-level → reconciles BOTH divisions post-boot).

### 4. bootsmoke.ps1 — gate 3 (post-restart health)
Confirms: PID/NRestarts/active; **0 tracebacks** since boot; **both** divisions reconcile clean at startup; **regime seed depth ≥800 per coin** (log `regime seed <COIN>USDT: N ...`); the 3 new tables exist (`bitunix_sfp_research_log`, `bitunix_sfp_regime_flip`, `bitunix_sfp_regime_state`); regime-state mirror has 4 coins (post-warmup); `side: regime`; 4 symbol_modes armed. (NB: with today's seed depth ~788 < 800, regime is `None` for ~3h post-boot → no trades until the buffer fills — fail-safe; then it converges to ~100% parity as the append-only capture deepens.)

### 5. rollback.ps1 — restore pre-deploy blobs + one restart (last resort)

## FIRST-LIVE A/B (post-deploy gate — the maiden-short verification)
On the **first real fill per side**, confirm: TP rests at the venue with a real `/tpsl/`
id; on resolution the correct leg fires and **OCO auto-cancels the other (no orphan)**;
auto-book books at 2R; the research-log row has the correct regime stamp.
- **Watch the MAIDEN SHORT hardest** — the ONE venue-unexercised behavior is a short's B1
  `slPrice` sitting **ABOVE** entry and **triggering on price RISING** (venue-inferred from
  the short position side; the division has only ever placed longs). Confirm it triggers
  correctly and closes the short with no orphan.
- **Watch a RANGE both-sides case** — confirm the one-position-per-coin guard: a long and a
  short can NEVER be open on the same coin at once (opposite entry blocked; the one-way
  venue would net anyway).
- **If the maiden short misbehaves → HOT rollback**: edit `config/strategies.yaml`
  `bitunix_sfp: side: regime` → `side: long`. The next SFP signal (sparse) reads it —
  **shorts stop, longs keep running/collecting. No restart, no redeploy.** (Fail-safe: any
  YAML read error also defaults to `long`.)

## Test-suite status
- **All SFP + new-behavior tests GREEN.** `tests/test_bitunix_sfp_observer.py` reconciled
  (seed the regime buffer in `_mk` → real regime-gated fires) = 12 pass; new
  `tests/test_bitunix_sfp_bidirectional.py` = 18 pass (regime parity-lock, warmup gate,
  seed-from-history, short geometry/reflection, side-gate, kill-switch + fail-safe,
  research-log round-trip + `paper_trade_record` isolation, flip-watch label→label,
  regime-state mirror). Detector/mode-b/tp/watch/archiver SFP tests unchanged-green.
- **0 regressions** — see the base-vs-branch failure diff in the deploy report.
- The 230d empirical offline gates (regime parity, short parity ts/level, side-gate routing,
  side-switch) are the standalone `deploy_bidirectional_sfp/*_test.py` scripts (already run,
  all-pass, committed) — they need the local `cc/data/*_scalping.db`, so they run outside
  pytest.
