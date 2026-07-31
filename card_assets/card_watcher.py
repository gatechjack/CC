"""card_watcher.py — poll for closed bitunix_sfp construct trades and deliver a card to Telegram.

ISOLATED box side-process. DISPLAY/NOTIFICATION ONLY:
  * READ-ONLY on the shared DB (opened mode=ro; only closed rows are SELECTed).
  * writes ONLY its own files: the cursor file + rendered PNGs under ~/card_assets/out/.
  * never imports/starts the trading engine, touches config, orders, or the git-tracked trade path.

Modes:
  (default)              poll loop, every POLL_SECONDS (20s). One card per newly-closed trade.
  --check                load_secrets -> print has_telegram bool only (deploy sanity). No DB, no send.
  --test-once <order_id> render+send that single row, print result. Does NOT touch/advance the cursor.

Idempotency: cursor = the result_ts of the last successfully-sent row. Persisted AFTER each send, so a
crash never re-sends earlier ones. On first start (no cursor file) the cursor is seeded to NOW (the max
current closed result_ts, or current UTC) so historical closes are NOT back-fired.

Resilience: each trade is wrapped in try/except -> log + CONTINUE. A failed card is a missed notification,
never a crash and never a block on the next trade.

Config (env, all optional — sane defaults):
  CARD_DB_PATH       default ~/trading_corp/data/trading_corp.db
  CARD_ASSETS_DIR    default ~/card_assets/assets   (also used by card_gen)
  CARD_OUT_DIR       default ~/card_assets/out
  CARD_CURSOR_PATH   default ~/card_assets/cursor.txt
  CARD_YAML_PATH     default ~/trading_corp/config/strategies.yaml
  CARD_POLL_SECONDS  default 20
"""
import logging
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import card_gen
import card_sender
from card_data import build_card_data, read_trend_mode_map

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("card_watcher")

DIVISION = "bitunix_sfp"


def _home() -> Path:
    return Path.home()


def db_path() -> Path:
    return Path(os.environ.get("CARD_DB_PATH", str(_home() / "trading_corp" / "data" / "trading_corp.db"))).expanduser()


def out_dir() -> Path:
    return Path(os.environ.get("CARD_OUT_DIR", str(_home() / "card_assets" / "out"))).expanduser()


def cursor_path() -> Path:
    return Path(os.environ.get("CARD_CURSOR_PATH", str(_home() / "card_assets" / "cursor.txt"))).expanduser()


def yaml_path() -> Path:
    return Path(os.environ.get("CARD_YAML_PATH", str(_home() / "trading_corp" / "config" / "strategies.yaml"))).expanduser()


def poll_seconds() -> int:
    try:
        return int(os.environ.get("CARD_POLL_SECONDS", "20"))
    except ValueError:
        return 20


def _connect_ro() -> sqlite3.Connection:
    """Open the shared DB READ-ONLY (uri mode=ro). Row access by column name."""
    uri = f"file:{db_path().as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


def read_cursor() -> str | None:
    p = cursor_path()
    try:
        if p.exists():
            v = p.read_text(encoding="utf-8").strip()
            return v or None
    except OSError as e:
        log.error("read_cursor failed: %s", e)
    return None


def write_cursor(value: str) -> None:
    """Persist the cursor atomically (write temp + replace) to its OWN file."""
    p = cursor_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(value, encoding="utf-8")
    tmp.replace(p)


def _max_closed_result_ts(conn: sqlite3.Connection) -> str | None:
    cur = conn.execute(
        "SELECT max(result_ts) FROM paper_trade_record "
        "WHERE division=? AND result IN ('win','loss')",
        (DIVISION,),
    )
    r = cur.fetchone()
    return r[0] if r and r[0] else None


def seed_cursor_if_missing(conn: sqlite3.Connection) -> str:
    """On first start (no cursor), seed to NOW so we don't back-fire historical closes."""
    existing = read_cursor()
    if existing:
        return existing
    seed = _max_closed_result_ts(conn) or datetime.now(timezone.utc).isoformat()
    write_cursor(seed)
    log.info("card_watcher: first start — seeded cursor to %s (no historical backfire)", seed)
    return seed


def _caption(cd: dict) -> str:
    """Short caption for the buzz message (the card itself carries the detail)."""
    parts = [
        "SFP Failed Swing — construct trade CLOSED",
        f"{cd.get('pair','')} {cd.get('side','')} {cd.get('leverage','')}".strip(),
        f"{cd.get('r_result','')}  {cd.get('roi_pct','')}  [{cd.get('outcome_badge','')}]".strip(),
    ]
    return "\n".join(p for p in parts if p)


def _render_and_send(row: dict, trend_map: dict) -> bool:
    """Build card_data -> render PNG -> send. Returns send success. Never raises."""
    cd = build_card_data(row, trend_map)
    out_dir().mkdir(parents=True, exist_ok=True)
    oid = row.get("order_id") or "unknown"
    png = out_dir() / f"sfp_card_{oid}.png"
    card_gen.render_card(cd, str(png))
    caption = _caption(cd)
    ok = card_sender.send_card(str(png), caption)
    return ok


def poll_once(conn: sqlite3.Connection, cursor: str, trend_map: dict) -> str:
    """Fetch closed rows after `cursor`, render+send each in order, advance+persist cursor per send.

    Returns the (possibly advanced) cursor. Each trade is independently guarded.
    """
    rows = conn.execute(
        "SELECT rowid, * FROM paper_trade_record "
        "WHERE division=? AND result IN ('win','loss') AND result_ts > ? "
        "ORDER BY result_ts ASC",
        (DIVISION, cursor),
    ).fetchall()

    for row in rows:
        d = _row_to_dict(row)
        rts = d.get("result_ts")
        oid = d.get("order_id")
        try:
            ok = _render_and_send(d, trend_map)
            if ok:
                # advance + PERSIST after each successful send (crash-safe, no re-send)
                cursor = rts
                write_cursor(cursor)
                log.info("card_watcher: sent card for %s (%s) — cursor -> %s", oid, rts, cursor)
            else:
                log.error("card_watcher: send FAILED for %s (%s) — cursor held at %s (will retry next poll)",
                          oid, rts, cursor)
                # Do NOT advance: a transient failure retries next poll. A persistent failure will keep
                # retrying this one row (a stuck notification), which is the safe direction (no silent skip).
                break
        except Exception as e:
            log.error("card_watcher: trade %s (%s) raised: %s — CONTINUING", oid, rts, e)
            continue

    return cursor


def run_loop() -> None:
    log.info("card_watcher: starting poll loop (db=%s, poll=%ss)", db_path(), poll_seconds())
    trend_map = read_trend_mode_map(yaml_path())
    log.info("card_watcher: trend_mode_map=%s", trend_map)
    # seed cursor on a short-lived RO connection
    with _connect_ro() as conn:
        cursor = seed_cursor_if_missing(conn)
    while True:
        try:
            # re-read trend map each cycle (HOT — it can change without restart)
            trend_map = read_trend_mode_map(yaml_path()) or trend_map
            with _connect_ro() as conn:
                cursor = poll_once(conn, cursor, trend_map)
        except Exception as e:
            log.error("card_watcher: poll cycle raised: %s — continuing", e)
        time.sleep(poll_seconds())


def test_once(order_id: str) -> int:
    """Render+send exactly one row by order_id. Does NOT touch the cursor. Returns process exit code."""
    trend_map = read_trend_mode_map(yaml_path())
    log.info("card_watcher --test-once: order_id=%s trend_map=%s", order_id, trend_map)
    with _connect_ro() as conn:
        row = conn.execute(
            "SELECT rowid, * FROM paper_trade_record WHERE order_id=?", (order_id,)
        ).fetchone()
    if row is None:
        log.error("card_watcher --test-once: no row for order_id=%s", order_id)
        print(f"NO ROW for order_id={order_id}")
        return 2
    d = _row_to_dict(row)
    cd = build_card_data(d, trend_map)
    print("card_data:", cd)
    try:
        ok = _render_and_send(d, trend_map)
    except Exception as e:
        log.error("card_watcher --test-once: render/send raised: %s", e)
        print(f"ERROR: {e}")
        return 3
    print(f"SEND {'OK' if ok else 'FAILED'} (cursor NOT touched)")
    return 0 if ok else 1


def do_check() -> int:
    ok = card_sender.check()
    print(f"has_telegram={ok}")
    return 0 if ok else 1


def main(argv) -> int:
    if len(argv) >= 2 and argv[1] == "--check":
        return do_check()
    if len(argv) >= 2 and argv[1] == "--test-once":
        if len(argv) < 3:
            print("usage: card_watcher.py --test-once <order_id>")
            return 2
        return test_once(argv[2])
    run_loop()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
