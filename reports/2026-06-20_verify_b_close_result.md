# VERIFY-B close-side result — 2026-06-20 (NETTED 2-trade close)

Engine PID 3065623 (P2-combined, 22:13:00 UTC deploy). Both trades closed 02:19-02:21 UTC,
auto-booked 02:21:06. Read-only. **This was NOT a clean single-trade close** — two stacked shorts
(125b6f9e + 81f5427a) netted into one venue position, so most close-side reads are netting-corrupted.

## Venue truth (BitUnix Trade History)
| t (UTC) | tag | side | avg px | vol | realized PnL | role |
|---|---|---|---|---|---|---|
| 02:18:57 | TP | Buy | 63,356.8 | 0.0004 | +0.02966285 | Taker |
| 02:19:47 | SL | Buy | 63,391.6 | 0.0024 | +0.09445714 | Taker |

Venue gross realized = **+0.12412**. Total fees (2 entry + 2 close) = 0.07102. **Real net ≈ +0.053 USDT.**
A TP leg genuinely filled (first live managed-TP exit ever); remaining closed via SL trailed BELOW entry → profit.

## Engine booked (both records, result_ts 02:21:06, result_source=auto_booked_from_real_fill, pnl_basis=real_fill)
| record | result | gross pnl | net_realized | qty booked | vwap | exit_kind | exit_role | maker_taker_mix |
|---|---|---|---|---|---|---|---|---|
| 125b6f9e | **win** | 0.22028 | 0.160686 | **0.0028** | 63386.63 | unknown | maker | maker 1.0 |
| 81f5427a | **win** | 0.21720 | 0.170279 | **0.0028** | 63386.63 | unknown | maker | maker 1.0 |

Engine booked net total = **+0.330965**. Venue real net ≈ +0.053. **Over-booked ~6× (net) / 3.5× (gross).**

## TIER 1
1. **result via classify_result NET — PASS (headline).** Both `result=win`, derived from real net (+0.16/+0.17>0).
   First live win that books `win` — the old hard-coded `loss` bug is FIXED. Core P2 objective validated.
2. **exit_kind — guardrail HELD, positive match FAILED.** Both `exit_kind=unknown` (mirrored to autobook_level_type).
   It did **NOT** default to 'stop' (the anti-mislabel rule held — no false loss, no false stop). BUT the order-id
   match did not positively resolve tp/stop → fell to 'unknown' (netted 2-trade close fills didn't tie to either
   record's tracked tp_order_ids/sl_order_id). So: safe, but not a clean positive classification.
3. **exit_role/maker_taker_mix — RECORDED, no TypeError, but VALUE WRONG.** exit_role=maker, mix=100% maker.
   Venue = Taker on all 4 fills. Same role mis-recording defect as entry (roleType field unreliable). Mechanically
   threaded (no crash), but the recorded role is false.

## TIER 2 (TP partial DID fill → path exercised, but NOT validated)
4. **SL-trail (#5) — path reached, NO-OP.** `position_sl_update` (id 1248607/1248608, 02:20:05): source=bracket_sl_move,
   reason "TP1+TP2 filled → SL to TP1", new_sl 63351.06/63349.96, prev 63610.53/63591.23, **moved=FALSE** (current_qty=0.0).
   The new post-404 path RAN with zero 404s (regression gone ✅) but was a no-op — the whole position closed in ~50s
   (TP 02:18:57 → SL 02:19:47), faster than the 60s reconciler tick at 02:20:05 → nothing left to trail. The real
   SL-trail-to-breakeven did NOT happen in-engine. NOT validated.
5. **SL auto-reduce (#6) — did not occur** (no-op move, position already flat). NOT validated.
   PENDING a slower single trade where the reconciler tick catches the TP fill before full close.

## DEFECTS (flag hard)
- **D1 — PnL double-booked from netting.** Each record booked the FULL netted close (qty 0.0028, same 2 fills, same
  vwap) → each claimed the entire close → realized PnL inflated ~6×. Material accounting corruption at scale.
- **D2 — filled_legs=[] despite a real TP fill.** TP-leg fill never registered into filled_legs (orphan/managed-exit
  fill-registration gap) → engine's "TP1+TP2 filled" inference is guesswork, not tracked legs.
- **D3 — role mis-recording (entry + exit).** Venue Taker, engine maker (both sides). roleType field wrong/unreliable.
- **D4 — no concurrent-position guard** (root cause): the 02:06 second entry stacked onto the open 01:10 short. THIS
  is what produced the netting that corrupts D1/exit_kind/Tier-2.

## Healthy (the P2 self-heal worked)
divergence_detected (02:20:05, missing=2) → both auto_book_server_side_close (02:21:06) → reconciled clean (02:21:07)
→ halt_released "two_consecutive_clean_ticks" (02:22:07) → self-resume, **NO RESTART**. Engine flat/healthy after.

## Verdict
P2 win-labeling + anti-false-'stop' = **VALIDATED**. Everything else this close (exit_kind positive match, role,
Tier-2 SL-trail/auto-reduce, PnL accuracy) was **corrupted or no-op'd by the netting** → a clean VERIFY-B still needs
a SINGLE un-netted trade. The concurrent-position guard (D4) is now the priority fix.
Agent SSH this session = READ-ONLY throughout.
