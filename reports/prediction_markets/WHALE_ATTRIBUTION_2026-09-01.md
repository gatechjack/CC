# Whale attribution on /live + per-whale LIVE-COPY record (2026-09-01) — design, adversarial review, deploy note

pm_web ONLY. No engine change, no schema change — `wallet` is already on every `pm_subdivision_order` row (confirmed:
41 entries, 23 settlement closes, 2 opposed closes, **0 wallet-NULL**). Rides the next pm_web restart. Built, tested,
box-scratched; HALT before deploy.

## What was built
- **Whale on every row** — `live_orders` now selects `wallet` + joins `pm_whale` for a display name; the "Live trades"
  ledger and the "Currently held" table each carry a Whale column, rendered `user_name or wallet[:10]…` (matching the
  existing "Copies these whales" list).
- **Currently held is now PER (ticker, whale)** — `live_positions_by_whale`: one row per whale per ticker.
- **The per-whale LIVE-COPY record** — `live_copies_by_whale`: for each whale, copies placed, settled W/L, realized,
  open-at-cost, and opposed-closed — the honesty discipline of the account P&L (realized / W/L / SAMPLE / open shown
  SEPARATELY, thin-sample travels with the number).

## ★ Recommendation (Jack: recommend, do not pick silently)
- **Row structure for same-side stacking: ONE ROW PER WHALE PER TICKER.** A ticker's contracts can come from several
  whales (ten whales one side = fifty contracts, by design). Jack's literal question is "which whale is this trade
  from" — per-whale rows answer it; a per-ticker-with-whales-listed row re-buries it. The per-ticker net is still
  available (the account page's `live_positions`), shown as context in the copy.

## ★ Attribution rules (Jack: say how each close is attributed, prove with real rows)
- **By the WALLET ON EACH ROW — never a close→entry join.** Every row (entry AND close) carries its wallet.
  - **Settlement close** → credits the wallet's realized_pnl + W/L (won 0/1). PROVEN on the box: the three whales'
    settlement realized sums to **−20.24** and **7W/16L** — exactly the account total (`subdivision_pnl`).
  - **★ The NULL-cid case** — the first Cubs settlement `id=8` has `condition_id=NULL, outcome_index=NULL` (R-d's early
    boot scan). A close→entry join on cid/oidx would **drop it**; its wallet (`0x16bb`) is present, so wallet-on-row
    credits it correctly. Tested (`test_per_whale_record_attribution`, Alice's T3) AND live (it is inside SDTrading's −17.33).
  - **Opposed close** (the guard's flatten) → carries the wallet but `realized_pnl NULL, won NULL`. Counted SEPARATELY
    as `opposed_closed`, **never** folded into realized or W/L. PROVEN: the 2 box opposed closes attribute to
    `0x16bb` (id 52) and `0x684baa57` (id 55), each shows `opposed_closed=1`, neither in realized/W/L.
  - **Whale-exit close** (Option D) — none on the box yet; handled generically (it carries a wallet + realized like a
    settlement, so it credits realized/W-L when it appears; if it should be distinguished from settlement, that is a
    one-line template change once real rows exist).
- **Same-side stacking PROVEN:** `KXMLBGAME-…SEABOS-SEA` is held via SDTrading (5ct) + 0x684baa57 (5ct) → two rows,
  summing to the per-ticker net of 10.

## ★ Naming (Jack: it must not be confused with the other two records)
The section is titled **"Live-copy record · per whale · real money"** with copy stating it is the one record that
says whether copying a whale is *actually working here*, and explicitly that it is **NOT** the paper-trade record
("would it have") and **NOT** the prospect screen ("did it historically"), which key on the same whales on a
different basis. A `dry_run=1` (paper/logged) row can never enter it (tested).

## Adversarial review — attribution correctness (the thing that breaks silently)
- ✅ wallet-on-row (not a join) — the NULL-cid `id=8` is caught (a join drops it). Tested + live.
- ✅ opposed counted separately, never in realized/W-L. Tested + live.
- ✅ same-side stacking → per-whale rows summing to the per-ticker net. Tested + live.
- ✅ record decomposes the account P&L exactly (−20.24 / 7W-16L). Live.
- ✅ dry-run rows excluded (live-only). Tested.
- ✅ attached-but-uncopied whale shows 0s (attached); detached-but-copied whale still shows its record. Tested.
- ✅ per-whale open sums to the account-page open (no opposing pairs held now → per-(ticker,wallet) == per-ticker net).
- ⚠️ **Honest limitation (flag, not a bug):** the opposed-close's actual money outcome is UNBOOKED — the engine does
  not compute realized_pnl on a guard flatten, so an opposed-closed entry's cost/result is in neither realized nor
  open. The page shows `opposed_closed=N` so the operator knows it happened; capturing that P&L is an ENGINE change
  (book realized on opposed flattens), out of scope for a pm_web-only change. There are only 2 such rows today.
- ⚠️ `0x684baa57` has no `pm_whale.user_name` → shows the wallet short-form (correct fallback; name it upstream if wanted).

## Tests + box-scratch
- `test_whale_attribution.py` (4, pure): whale on every ledger row; same-side stacking → per-whale rows; the full
  attribution (NULL-cid settlement, opposed-separate, attached/detached, thin-sample); live-only (dry-run excluded).
  Plus `test_live_r3` (the /live render) still green → the template changes render.
- Box-scratch `cc\pm_whale_attrib_boxscratch_ro` proved the record + stacking on the real 41-entry / 23-settlement /
  2-opposed dataset.

## Deploy (HALT — pm_web batch, next pm_web restart)
3 files: `subdivision.py` (live_orders + live_positions_by_whale + live_copies_by_whale) + `web/app.py`
(_load_live_subdivision passes the new data) + `web/templates/pm_live_subdivision.html` (Whale columns + the record
section). No new import (uses `search.DEFAULT_MIN_RESOLVED_FLOOR`, already imported). Reconcile file-by-file vs the box
(box-is-truth); the box's subdivision.py/app.py were just deployed at the pm_web-batch versions today, so this grafts
onto those. Rides the next pm_web restart.
