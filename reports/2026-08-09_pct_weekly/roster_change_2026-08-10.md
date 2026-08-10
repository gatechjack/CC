# PCT Roster Change — applied to prod 2026-08-10 ~03:43 UTC

**Authorized by operator. Written via the dashboard's own FastAPI endpoints (localhost:8000) + 2 `set_agent_state` edits. Engine reloads `selected_whales` each cycle → no restart.**

## Actions (8) — all verified PASS on independent re-read
| Whale | Action | selected | pinned | watch | Verify |
|---|---|---|---|---|---|
| rollobravado | promote+pin | +add | +pin | (stays) | PASS |
| Kosherlocks | promote+pin | +add | +pin | (stays) | PASS |
| GreatestTrader | promote+pin (+note) | +add | +pin (note) | (stays) | PASS |
| olddirtyfighter | promote+pin | +add | +pin | (stays) | PASS |
| llllllII | demote→watch | −remove | −unpin | +entry(note) | PASS |
| potatobrahh | remove | −remove | −unpin | — | PASS |
| ChadStarmer | remove | −remove | −unpin | — | PASS |
| Hakei / CVCM / ox1star84 / DegenKingBetter | KEEP unchanged | (in) | (in) | — | PASS |
| digitalnomad85 | untouched | (not in) | (in) | — | PASS |

**After-state counts:** selected=8, pinned=9, watch=107. Audit log: `polymarket_whale_promoted` ×5, `polymarket_whale_demoted` ×3.

## Two notes for the record
1. **Flatten counts (NOT zero):** demote flattened open paper positions — **llllllII 575, ChadStarmer 17, potatobrahh 5** (zero-PnL synthetic closes at entry price; correct demote behavior — clears the tracked book, resets `whale_state`). My pre-write "inert at 0 open" estimate queried the wrong table (`round_trips.resolved_ts IS NULL`); `force_close` reads `whale_state.our_positions`. No PnL distortion.
2. **First-run abort / duplicate promote log:** the first execute attempt died on a `/bin/sh` (dash) incompatibility (`${w:0:10}`) AFTER rollobravado's promote POST fired. Corrected to POSIX + re-ran; endpoints are idempotent so the roster is correct (rollobravado present once). The only residue is an extra audit-log row → 5 promote events for 4 promotes.

## Standing follow-up
- **llllllII hard-cut deadline 2026-08-17** — if its copyable signal hasn't resumed by then, remove the watch entry (hard-cut). Watch entry carries this note.
- GreatestTrader `note` (running hot; size conservatively) is advisory only — sizing is flat ~$1/order (conviction-scaling disabled); the note will be dropped on the next reseed.
