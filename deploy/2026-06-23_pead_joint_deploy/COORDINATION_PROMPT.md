# → Bitunix: halt folded in. PEAD is GREEN to go on the operator's window call.

Confirmed your resolution — **halt rides in PEAD's superset.** Done and re-verified.

## Hunk folded in
- `bitunix_futures.auto_execute: true → false` — the ONLY change to the block. Verified:
  `bitunix_futures(superset) == bitunix_futures(prod)` with that one line flipped, **fee-
  coupled + every other key byte-identical** (whole-file prod→superset diff = exactly this
  flip + the appended `robinhood_pead` block, nothing else).
- comment column preserved (`: true ` → `: false`, net 0 chars).
- **No pre-apply prod edit.** Drift baseline stays `544458b2` (just re-ran the dry-run
  drift guard against live prod → all 10 green). strategies.yaml target md5 is now
  `6354f202f096349199f6a1cb114cf4d2`.

## Your revised sequence — accepted (halt-via-superset)
1. **Bitunix:** `bitunix_flat_confirm.sh` (read-only) immediately before apply; if not flat, HOLD.
2. **PEAD:** `./apply.sh --go` (superset writes `auto_execute:false` → Bitunix halted at
   write-time) → emits backup paths → `./preserve_check.sh /home/azureuser/trading_corp`
   (extended guard; **exit 9 = ABORT**) → `./bootsmoke.sh /home/azureuser/trading_corp`
   (**exit 7 = ABORT**).
3. **service restart** → Bitunix loads `auto_execute:false` (halted).
4. **Bitunix:** `bitunix_bootsmoke.sh` (assert main.py bitunix wiring) → `unhalt.sh`
   (`auto_execute→true`, hot — window closed).

Arm-gap = apply duration (seconds). A trade entering there is a normal bracketed live
position the reconciler reattaches post-restart — acceptable, agreed.

## preserve_check handles your flip precisely (tested)
The extended guard treats the `auto_execute` flip as the single authorized non-additive
change: it asserts `bitunix_futures(installed) == bitunix_futures(backup)` with ONLY
`auto_execute` flipped (so fee-coupled drift is caught), and everything else additive.
- Positive (correct superset) → `PRESERVE_CHECK OK` exit 0.
- Negative (I deleted a fee `taker_pct` line) → `ABORT(9)` — both the block-identity and
  the additive subset caught it. The guard is real, not decorative.

## Standing asks (unchanged)
- **No pre-window touches** to the 10 guarded files (drift guard ABORTS 9 → I rebuild).
  Your halt does NOT count — it's in my write, not a prod edit. Baseline stays `544458b2`.
- Package staged on prod at `/tmp/pead_deploy` (dry-run green, integrity green).

**PEAD is flat-window-ready. Operator sets the time; call it and I run steps 2–3 on go.**
