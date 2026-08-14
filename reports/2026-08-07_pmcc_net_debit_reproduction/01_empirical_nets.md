# PMCC "net debit — blocked" — LIVE empirical reproduction (2026-08-07)

READ-ONLY. Verbatim-prod functions on live Robinhood quotes. Nothing placed;
auto_execute untouched. Account 461391328 (PMCC book).

## Provenance (verbatim-prod confirmed)
- `pmcc_robinhood.py` worktree LF-md5 = `2a390124` == deployed credit-fix (prod truth).
- `_pmcc_combo.py` worktree LF-md5 = `cf5a8f1c` == deployed (untouched).
- Worktree branch `claude-2026-08-07b` off `prod-live ee04747`.

## The DEPLOYED gate (pmcc_robinhood.py `_propose_roll_short`, L3932-3966)
```
close_q          = _fresh_leg_quote(old short expiry/strike)   # fresh, same timestamp
close_mark_fresh = fresh mark (else scan mark)
close_ask        = fresh ask (else close_mark_fresh)
give_up          = 0.02   (config robinhood_pmcc.combo.give_up_dollars)
conservative_net = new.bid - close_mark_fresh          # _short_roll_credit
mid_net          = new.mark - close_mark_fresh         # operator fills here
natural          = new.bid - close_ask                 # dispatch's actual bar
dispatch_net     = natural - give_up                   # <-- GATE BLOCKS if < 0
if dispatch_net < 0 and override != net_debit_justified: ABORT "net_debit_roll"
combo tags: direction = credit if dispatch_net>=0 else debit; net_limit = |dispatch_net|
```
Picker `_select_weekly_strike`: default δ target = `_short_target_delta` = 0.30;
new-weekly expiry = earliest in [7,21] DTE that rolls out (`_days_to > old DTE`).
Liquidity gate: Liveness (OI>=100 OR vol>=500) AND two-sided (bid>0, ask>0, non-inverted).
No spread-width reject (that was the 2026-08-06 fix).

## Spot (14:03 UTC ~10:03 ET, market open)
- TSLA 325.04 (bid 325.06 / ask 325.12)
- SMR   9.67 (bid 9.66 / ask 9.67)

## Current shorts (live, 14:03:53 UTC)
| name | short | bid | ask | mark | δ | intrinsic |
|---|---|---|---|---|---|---|
| TSLA | $322.50C 2026-08-07 (0-DTE) | 3.50 | 3.65 | 3.575 | 0.676 | $2.54 ITM |
| SMR  | $10.00C 2026-08-14 (7-DTE)  | 0.32 | 0.37 | 0.345 | 0.420 | $0 (OTM) |
LEAPs: TSLA $310C 2027-01-15 mark 49.975 (=$4,997.50/ct; cost basis $164.21 => $16,421/ct).
       SMR  $10.00C 2028-01-21 mark 4.55.

## SMR roll: buy $10.00C 08-14 (ask 0.37, mark 0.345) -> sell new weekly 08-21
| new strike | δ | bid | ask | mark | natural(bid-0.37) | dispatch(-0.02) | mid(mark-0.345) | class |
|---|---|---|---|---|---|---|---|---|
| 9.5  | 0.560 | 0.70 | 0.79 | 0.745 | +0.33 | +0.31 | +0.400 | credit (clears) |
| 10.0 | 0.446 | 0.50 | 0.53 | 0.515 | +0.13 | +0.11 | +0.170 | credit (clears) |
| **10.5 (tile)** | 0.346 | 0.34 | 0.39 | 0.365 | **-0.03** | **-0.05** | **+0.020** | **MID-CREDIT-WRONGLY-BLOCKED** |
| 11.0 (δ0.30 pick) | 0.258 | 0.24 | 0.26 | 0.250 | -0.13 | -0.15 | -0.095 | GENUINE-DEBIT |
| 11.5 | 0.201 | 0.16 | 0.21 | 0.185 | -0.21 | -0.23 | -0.160 | GENUINE-DEBIT |

SMR classification (tile-selected $10.50C): **MID-CREDIT-WRONGLY-BLOCKED**
(mid +$0.02/sh = +$2/ct fills; gate blocks on dispatch -$0.05). Tile's earlier "+$0.14"
= same structure at an earlier snapshot (intraday drift; still a mid credit).
NOTE: δ0.30 default picks $11.00C which is a genuine debit even at mid -> strike-sensitive;
the wrongly-blocked case is specifically the higher-δ ($10.50C) strike the LLM/band selected.

## TSLA roll: buy $322.50C 08-07 0-DTE ITM (ask 3.65, mark 3.575) -> sell new weekly 08-14
| new strike | δ | bid | ask | mark | natural(bid-3.65) | dispatch(-0.02) | mid(mark-3.575) | class |
|---|---|---|---|---|---|---|---|---|
| 322.5 | 0.560 | 8.40 | 8.55 | 8.475 | +4.75 | +4.73 | +4.900 | credit (same strike; keeps ITM) |
| 325.0 | 0.506 | 7.15 | 7.30 | 7.225 | +3.50 | +3.48 | +3.650 | credit |
| 330.0 | 0.401 | 5.10 | 5.20 | 5.150 | +1.45 | +1.43 | +1.575 | credit (best up-and-out credit) |
| **335.0 (δ0.30 pick)** | 0.307 | 3.55 | 3.65 | 3.600 | -0.10 | **-0.12** | **+0.025** | **MID-CREDIT-WRONGLY-BLOCKED (marginal)** |
| 340.0 | 0.228 | 2.45 | 2.49 | 2.470 | -1.20 | -1.22 | -1.105 | GENUINE-DEBIT (bounded) |
| 345.0 | 0.167 | 1.68 | 1.71 | 1.695 | -1.97 | -1.99 | -1.880 | GENUINE-DEBIT |
| 350.0 | 0.121 | 1.16 | 1.18 | 1.170 | -2.49 | -2.51 | -2.405 | GENUINE-DEBIT |

TSLA classification (picker δ0.30 = $335C): **MID-CREDIT-WRONGLY-BLOCKED at the margin**
(mid +$0.025/sh = +$2.5/ct; gate blocks on dispatch -$0.12). Does NOT reproduce as a
clean GENUINE-DEBIT at the δ target — contra hypothesis.
- Credit strike EXISTS: $330C (δ0.40) rolls for +$1.43 dispatch credit (clears the gate),
  same/near strike (322.5/325) +$3.5..4.7. All up-and-out credit lives at δ>=0.40.
- Genuine (bounded) debit only at deeper-OTM assignment-escape strikes ($340+, δ<=0.23):
  -$1.2 .. -$2.5, i.e. paying to move the strike far above spot to dodge 0-DTE assignment.

## TSLA ITM / 0-DTE assignment specifics
- Short $322.50C, spot 325.04 => intrinsic +$2.54 (ITM). Expires TODAY (0-DTE) =>
  assignment-likely if left ITM at close. The intrinsic is embedded in the buyback ask
  (3.65 = 2.54 intrinsic + 1.11 time), which is what makes an up-and-out roll pay <3.65
  and thus print a debit once you move the strike above ~$330.
- 8%-of-LEAP debit tolerance (skill L255): base to be confirmed from code (see 02_*).
```
