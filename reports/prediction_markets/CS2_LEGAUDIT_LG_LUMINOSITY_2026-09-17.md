# CS2 leg-audit `code_review:code_not_in_outcome:LG!<Luminosity` — venue confirmation (READ-ONLY)

**Date:** 2026-09-17 · **Branch:** `cs2-legaudit-lg-luminosity-2026-09-17` (off prod-live d9468361)
**Runner:** `cc/pm_cs2_legaudit_diag_ro.{ps1,sh}` (sqlite mode=ro + public Kalshi/Polymarket API + deployed-matcher canon)
**Verdict: FILL IS CORRECT.** The flag is the benign subsequence-check artifact, not an inversion. No disarm.

## The rows (both accounts, own books)

Ticker `KXCS2GAME-26SEP171100NIPLG-LG`, leg `yes` (order_side bid), is_exit 0, real (dry_run 0), both `filled` 3@0.48.

| field | jack (id 999) | karen (id 1000) |
|---|---|---|
| account_id | kalshi_jack | kalshi_karen |
| signal_outcome | Luminosity | Luminosity |
| signal_slug | cs2-lg6-nip-2026-09-17 | cs2-lg6-nip-2026-09-17 |
| condition_id | 0x8824…9341 | 0x8824…9341 |
| outcome_index | 0 | 0 |
| signal_id | 1fbfc00a…67c0 | 1fbfc00a…67c0 |
| submitted | 3 @ 0.49 | 3 @ 0.50 |
| fill | 3 @ 0.48 | 3 @ 0.48 |
| client_order_id | e87524ef-1995-5852-ae33-f96379d3525f | ec0702d7-5295-5b30-b7ed-8a03ff2f84f3 |
| broker_order_id | 01a0b072-a6f8-7958-9c58-c9c523641f27 | 01a0b072-a6f8-7972-99c7-d68851ec8fde |
| leg_audit | code_review:code_not_in_outcome:LG!<Luminosity | (same) |
| response_ts | 1789666686 | 1789666685 |

Distinct client + broker order IDs => each landed on its OWN Kalshi book, copying the same whale signal (signal_id 1fbfc00a…), same side. Not an assumed pair.

## Venue confirmation (verbatim)

- **Kalshi `…NIPLG-LG`:** title=`Luminosity wins`, yes_sub_title=`Luminosity`, event=`KXCS2GAME-26SEP171100NIPLG`, status=active, result=`''`, closes 2026-09-19.
- **Kalshi `…NIPLG-NIP` (opponent):** title=`NIP wins`, yes_sub_title=`NIP`.
- **Polymarket slug `cs2-lg6-nip-2026-09-17`:** question=`Counter-Strike: Luminosity vs NIP (BO1) - Logitech G Play Connect Group A`, outcomes=`["Luminosity","NIP"]`, conditionId `0x8824…9341` (== order condition_id). Whale took outcome_index 0 = **Luminosity**.
- **Teams:** NIP = Ninjas in Pyjamas, LG = Luminosity (Gaming). Match = Luminosity vs NIP.

## Deployed-matcher exact-normalized match

- canon(whale `Luminosity`) = `luminosity`; canon(Kalshi -LG `Luminosity`) = `luminosity` -> **MATCH True**.
- canon(-NIP `NIP`) = `nip`; canon(whale) == canon(-NIP) -> **False** (correct: we are NOT on the opponent).
- `labels_code_swapped(LG,'Luminosity', NIP,'NIP')` -> **False** (direct score 0+3=3 >= cross 0+0=0). Not refused; not a magic->FaZe class swap.
- Leg-audit subsequence: `subseq('LG','Luminosity')` -> False. `lg` is not an ordered subsequence of `luminosity` (no `g`), so the SOFT `code_review` flag fires. The bind was on the NAME (yes_sub_title exact canon-equality), never on the code; the code contributes 0 to the swap-guard here and the NIP side carries the direct assignment.

## Is this the first cs2 fill? NO.

First cs2 filled real entry: **id 377, KXCS2GAME-26SEP082300FAZEMGC-FAZE, 2026-09-09 05:32:42 UTC.** Census (real): jack 18 entry + 15 exit fills; karen 14 entry + 11 exit fills; 27 distinct cs2 tickers ordered since 2026-09-09.
It IS the first cs2 `code_review` flag ever (leg_audit distribution: `ok` 32, NULL 30 [pre-migration-021], `code_review:…LG!<Luminosity` 2) and the first Luminosity fill.

## Guard recommendation

The flag is a SOFT audit trail by design (live_driver.py:809-812 comment: "a legit non-subsequence code exists (Team Liquid->TL, McNally->MCC), so this is an audit trail, not an alarm"). It correctly did not block. But it will recur on every future Luminosity(LG) fill and any org whose Kalshi code drops a letter absent from the shortened yes_sub_title — the "trains me to ignore it" failure mode.

Recommendation (fork left to Jack): add a small, VENUE-VERIFIED code-alias table consulted only when the raw subsequence test fails; a hit emits a distinct `ok:code_alias` verdict (visible auto-clear, not silent `ok`), seeded `LG -> luminosity`. Preserves audit independence from the matcher (static curated abbreviation fact, not the matcher's runtime name-equality) while killing the recurring false positive. Do NOT make the audit compare yes_sub_title canon-equality (that would rubber-stamp the matcher and destroy independence). Adding any alias entry must require a venue confirmation like this one.
