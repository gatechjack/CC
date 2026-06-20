# VERIFY — D4 concurrent-position guard (2026-06-20). Split per Board.

Pre-deploy prod **PID 3065623**. Run AFTER the operator's restart. Read-only; agent verifies.

## A. CONFIRMS-AT-RESTART  (flag OFF → guard WIRED but DORMANT; zero behavior change)
- [ ] **Engine up, NEW PID** — `systemctl show trading-corp -p MainPID -p ActiveState -p SubState -p NRestarts`:
      MainPID != 3065623, active/running, NRestarts=0.
- [ ] **Files at TARGET** — observer `e88a7abca643f2048facfcb19a6c559b`, main `97a4d67661361414e369d9e4355e7d3e`;
      strategies.yaml contains `concurrent_position_guard:` with `enabled: false`.
- [ ] **Config preserved (surgical yaml)** — `diff config/strategies.yaml{.bak-pre-d4-2026-06-20,}` shows ONLY the
      added D4 block; `execution_mode: live`, per_account DD-cap 0.99, B2 maker OFF, staleness gate ON all intact.
- [ ] **Clean boot** — startup log line `BitUnix concurrent-position guard (D4): enabled=False`; NO
      ImportError/TypeError/`concurrent_position_guard` binding error.
- [ ] **Reconciler clean / flat / no halt.**
- [ ] **DORMANT proof** — with the flag OFF the guard returns not-blocked immediately: NO
      `concurrent_position_guard_blocked` audit appears; a normal entry/score behaves exactly as pre-deploy.
- [ ] audit + Telegram notify path is REACHABLE but unexercised here — confirmed only when the flag is flipped ON (B).

## B. NEEDS-A-LIVE-TRADE  (only AFTER the operator flips `enabled: true` + restart)
- [ ] **BLOCK fires** — while the bot holds an open same-side position, a NEW same-side score attempt is blocked:
      a `concurrent_position_guard_blocked` audit (reason `bot_own_same_side_position_open`, source `venue+engine`)
      **+ a Telegram notify**; NO 2nd `live_order_placed`; the venue stays a single position (no netting).
- [ ] **PASS-THROUGH** — a normal single entry (flat → open) proceeds and places; the guard does not false-block.
- [ ] **POST-MANUAL-FLATTEN not wrongly blocked** — after the position is closed at the venue (e.g. operator
      SL-to-price), the NEXT legit same-side signal ENTERS: the venue-flat read is authoritative, the lagging
      engine `result IS NULL` row does NOT block (the exact wrong-block this design avoids).
- [ ] reversal / opposite-side + any reduce-only/flatten continue to flow (same-side-only scope).

**Only after B is observed clean on a live trade is the guard TRUSTED.** Until then it stays OFF (or, once ON,
under watch).

## Rollback if any A-check fails
Restore the 3 `*.bak-pre-d4-2026-06-20` over the live files (PLAN.md) + restart. Flag OFF ⇒ dormant regardless.
