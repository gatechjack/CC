"""Whale attribution on /live + the per-whale LIVE-COPY record (2026-09-01). Pure (subdivision.py reads only
sqlite -- no fastapi). ADVERSARIAL on ATTRIBUTION CORRECTNESS, the thing that breaks silently:
  - a close is credited by the WALLET ON ITS OWN ROW, never a close->entry join -- proven with a settlement whose
    condition_id/outcome_index are NULL (the real id=8 first-Cubs close), which a join would DROP;
  - SAME-SIDE STACKING: two whales on one ticker/leg -> one held row EACH, summing back to the per-ticker net;
  - OPPOSED closes (realized_pnl NULL, won NULL) are counted separately, NEVER folded into realized or W/L;
  - an attached-but-uncopied whale shows 0s (attached), a detached-but-copied whale still shows its record.
"""
from trading_corp.prediction_markets import db, subdivision

_ORDER_COLS = ("account_id", "category", "wallet", "condition_id", "outcome_index", "signal_id", "client_order_id",
               "ticker", "order_side", "outcome_leg", "is_exit", "submitted_count", "submitted_price",
               "time_in_force", "outcome_status", "fill_count", "fill_price", "fee", "dry_run", "submitted_ts",
               "response_ts", "close_source", "realized_pnl", "won")


def _ins(conn, **kw):
    base = dict(account_id="kalshi_jack", category="mlb", wallet="0xaaa", condition_id="0xc", outcome_index=1,
               signal_id="s", client_order_id="c", ticker="T", order_side="bid", outcome_leg="yes", is_exit=0,
               submitted_count=5, submitted_price=0.5, time_in_force="ioc", outcome_status="filled", fill_count=5.0,
               fill_price=0.5, fee=0.01, dry_run=0, submitted_ts=1788200000, response_ts=1788200000,
               close_source=None, realized_pnl=None, won=None)
    base.update(kw)
    _ins.n = getattr(_ins, "n", 0) + 1
    base["client_order_id"] = "c%d" % _ins.n
    cols = [c for c in _ORDER_COLS]
    conn.execute("INSERT INTO pm_subdivision_order (%s) VALUES (%s)" % (",".join(cols), ",".join(["?"] * len(cols))),
                 tuple(base[c] for c in cols))


def _db(tmp_path, monkeypatch):
    p = str(tmp_path / "pm.db"); monkeypatch.setenv("PM_DB_PATH", p); db.init_db(p)
    with db.connect(p) as c:
        c.execute("INSERT INTO pm_account(account_id,venue,secret_ref,label,active,created_ts) VALUES('kalshi_jack','kalshi','K','Jack',1,1)")
        c.execute("INSERT INTO pm_subdivision(account_id,category,label,sizing_mode,fixed_stake_usd,active,created_ts) VALUES('kalshi_jack','mlb','J','contracts',0.01,1,1)")
        for wal, name in (("0xaaa", "Alice"), ("0xbbb", "Bob"), ("0xccc", "Charlie"), ("0xddd", "Dave")):
            c.execute("INSERT INTO pm_whale(wallet,user_name) VALUES(?,?)", (wal, name))
        # attach Alice, Bob, Charlie (Dave is DETACHED but has copied)
        for wal in ("0xaaa", "0xbbb", "0xccc"):
            c.execute("INSERT INTO pm_subdivision_attachment(account_id,category,wallet,active,source,added_ts) VALUES('kalshi_jack','mlb',?,1,'seed',1)", (wal,))
        # ── Alice: T1 open (same-side stack w/ Bob) + T2 settled WIN + T3 settled LOSS with NULL cid (the id=8 case)
        _ins(c, wallet="0xaaa", ticker="T1", outcome_leg="yes")                                             # open entry
        _ins(c, wallet="0xaaa", ticker="T2", outcome_leg="yes")                                             # entry
        _ins(c, wallet="0xaaa", ticker="T2", outcome_leg="yes", is_exit=1, close_source="settlement", realized_pnl=2.0, won=1)
        _ins(c, wallet="0xaaa", ticker="T3", outcome_leg="yes")                                             # entry
        _ins(c, wallet="0xaaa", ticker="T3", outcome_leg="yes", is_exit=1, close_source="settlement",       # ★ NULL cid/oidx
             condition_id=None, outcome_index=None, realized_pnl=-0.6, won=0)
        # ── Bob: T1 open (SAME ticker/leg as Alice -> stacking) + T4 OPPOSED close (realized/won NULL)
        _ins(c, wallet="0xbbb", ticker="T1", outcome_leg="yes")                                             # open entry (stack)
        _ins(c, wallet="0xbbb", ticker="T4", outcome_leg="yes")                                             # entry
        _ins(c, wallet="0xbbb", ticker="T4", outcome_leg="yes", is_exit=1, close_source="opposed", realized_pnl=None, won=None)
        # ── Dave (DETACHED): T5 settled win
        _ins(c, wallet="0xddd", ticker="T5", outcome_leg="yes")
        _ins(c, wallet="0xddd", ticker="T5", outcome_leg="yes", is_exit=1, close_source="settlement", realized_pnl=1.5, won=1)
    return p


def test_live_orders_carry_the_whale(tmp_path, monkeypatch):
    p = _db(tmp_path, monkeypatch)
    with db.connect(p) as c:
        rows = subdivision.live_orders(c, "kalshi_jack", "mlb")
    assert rows and all("wallet" in r and r["wallet"] for r in rows)          # every row has a wallet
    names = {r["wallet"]: r["user_name"] for r in rows}
    assert names["0xaaa"] == "Alice" and names["0xbbb"] == "Bob"              # display name joined


def test_same_side_stacking_is_one_row_per_whale(tmp_path, monkeypatch):
    p = _db(tmp_path, monkeypatch)
    with db.connect(p) as c:
        held = subdivision.live_positions_by_whale(c, "kalshi_jack", "mlb")
        ticker_level = subdivision.live_positions(c, "kalshi_jack", "mlb")
    t1 = [h for h in held if h["ticker"] == "T1"]
    assert len(t1) == 2 and {h["wallet"] for h in t1} == {"0xaaa", "0xbbb"}   # ★ two whales, two rows on one ticker
    assert all(h["contracts"] == 5.0 for h in t1)
    # per-whale rows sum back to the per-ticker net (10 on T1)
    t1_ticker = [t for t in ticker_level if t["ticker"] == "T1"][0]
    assert t1_ticker["contracts"] == 10.0 == sum(h["contracts"] for h in t1)


def test_per_whale_record_attribution(tmp_path, monkeypatch):
    p = _db(tmp_path, monkeypatch)
    with db.connect(p) as c:
        rec = {w["wallet"]: w for w in subdivision.live_copies_by_whale(c, "kalshi_jack", "mlb")}
    a = rec["0xaaa"]
    # ★ the NULL-cid settlement is credited to Alice by her wallet (a close->entry join on cid would DROP it):
    assert a["n_settled"] == 2 and a["settled_w"] == 1 and a["settled_l"] == 1
    assert abs(a["realized_pnl"] - 1.4) < 1e-9                                # +2.0 (T2) + -0.6 (T3, NULL cid)
    assert a["copies"] == 3 and a["n_open"] == 1 and a["open_cost_usd"] > 0   # T1 still open
    assert a["thin_sample"] is True
    b = rec["0xbbb"]
    # ★ OPPOSED close is counted separately, NEVER as realized or W/L:
    assert b["opposed_closed"] == 1 and b["n_settled"] == 0
    assert b["settled_w"] == 0 and b["settled_l"] == 0 and b["realized_pnl"] == 0.0
    assert b["copies"] == 2 and b["n_open"] == 1                              # T1 (stack) still open
    # Charlie: attached, never copied -> 0s, attached True
    assert rec["0xccc"]["attached"] is True and rec["0xccc"]["copies"] == 0 and rec["0xccc"]["n_closed"] == 0
    # Dave: DETACHED but did copy -> present, attached False, its settled win recorded
    assert rec["0xddd"]["attached"] is False and rec["0xddd"]["settled_w"] == 1 and abs(rec["0xddd"]["realized_pnl"] - 1.5) < 1e-9


def test_record_is_live_only_not_paper(tmp_path, monkeypatch):
    # a dry_run=1 row (paper/logged-not-placed) must NEVER enter the live-copy record.
    p = _db(tmp_path, monkeypatch)
    with db.connect(p) as c:
        _ins(c, wallet="0xaaa", ticker="TX", outcome_leg="yes", dry_run=1)   # a dry-run copy
        rec = {w["wallet"]: w for w in subdivision.live_copies_by_whale(c, "kalshi_jack", "mlb")}
    assert rec["0xaaa"]["copies"] == 3                                        # unchanged -- dry-run excluded


if __name__ == "__main__":
    print("run under pytest (uses tmp_path/monkeypatch fixtures)")
