"""Piece-4 research-log ROUND-TRIP + ISOLATION proof (read-only; temp DB).

Exercises the REAL fail-soft write path (bitunix_sfp_research_log.ensure_schema /
log_entry / log_exit) on a throwaway sqlite DB: entry insert -> exit update by
order_id -> read back correct, AND assert paper_trade_record is byte-identical
before/after (the live records table must NEVER be touched by the catalog).
"""
import hashlib, os, sqlite3, sys, tempfile

DEPLOY = r"C:\Users\AA Incorporado\cc-sfp-deploy-wt"
sys.path.insert(0, DEPLOY)
from trading_corp.agents.divisions import bitunix_sfp_research_log as rl


def snap(path):
    con = sqlite3.connect(path)
    rows = con.execute("SELECT * FROM paper_trade_record ORDER BY rowid").fetchall()
    con.close()
    return hashlib.sha256(repr(rows).encode()).hexdigest()


def main():
    path = os.path.join(tempfile.mkdtemp(), "rt.db")
    url = "sqlite:///" + path.replace("\\", "/")
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE paper_trade_record (order_id TEXT, result TEXT, extra_json TEXT)")
    con.execute("INSERT INTO paper_trade_record VALUES ('LIVE-1', NULL, '{\"x\":1}')")
    con.commit(); con.close()
    before = snap(path)

    rl.ensure_schema(url)
    oid = "sfp-order-abc"
    entry = {
        "order_id": oid, "division": "bitunix_sfp", "coin": "BTC/USDT.P", "side": "short",
        "regime_label": "down", "regime_engine": "15m_ema200_slope", "rr_target": 2.0,
        "sfp_mode": "REAL", "bos_tf": "3m", "entry_ts": "2026-07-01 14:00:00",
        "entry_px": 60000.0, "stop_px": 60600.0, "target_px": 58800.0,
        "sfp_sweep_px": 60500.0, "bos_confirm_px": 59900.0,
        "htf_1h_ema200": 60100.0, "htf_1h_slope": -0.002, "htf_1h_strength": 0.01,
        "htf_4h_ema200": None, "htf_4h_slope": None, "htf_4h_strength": None,
        "htf_1d_ema200": None, "htf_1d_slope": None, "htf_1d_strength": None,
        "broker_order_id": "BRK-9", "extra_json": '{"leverage":10}',
    }
    ok_e = rl.log_entry(url, entry)
    ok_x = rl.log_exit(url, oid, exit_ts="2026-07-01 15:30:00", exit_px=58800.0,
                       realized_r=2.0, closing_leg="tp")
    after = snap(path)

    con = sqlite3.connect(path); con.row_factory = sqlite3.Row
    row = con.execute("SELECT * FROM bitunix_sfp_research_log WHERE order_id=?", (oid,)).fetchone()
    con.close()

    checks = {
        "entry_insert_ok": ok_e, "exit_update_ok": ok_x,
        "division=bitunix_sfp": row["division"] == "bitunix_sfp",
        "side=short": row["side"] == "short", "regime=down": row["regime_label"] == "down",
        "rr_target=2.0": row["rr_target"] == 2.0,
        "entry_px": row["entry_px"] == 60000.0, "stop_px(above)": row["stop_px"] == 60600.0,
        "target_px(below)": row["target_px"] == 58800.0, "sweep_px": row["sfp_sweep_px"] == 60500.0,
        "htf_1h_ema200": row["htf_1h_ema200"] == 60100.0,
        "htf_4h_NULL": row["htf_4h_ema200"] is None, "htf_1d_NULL": row["htf_1d_ema200"] is None,
        "exit_px": row["exit_px"] == 58800.0, "realized_r=2.0": row["realized_r"] == 2.0,
        "closing_leg=tp": row["closing_leg"] == "tp",
        "duration_sec=5400": row["duration_sec"] == 5400,   # 90 min
        "PAPER_TRADE_RECORD_UNTOUCHED": before == after,
    }
    allok = all(checks.values())
    for k, v in checks.items():
        print(f"  {'OK  ' if v else 'FAIL'} {k}")
    print(f"\n  paper_trade_record sha256 before==after: {before == after}  ({before[:16]})")
    print(f"RESEARCH-LOG ROUND-TRIP + ISOLATION: {'ALL PASS' if allok else '*** FAIL ***'}")


if __name__ == "__main__":
    main()
