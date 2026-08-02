"""Consistent backup + restore-verification for a SQLite corpus DB.

Follows the repo's backup idiom (online sqlite `.backup`, then PRAGMA
integrity check) and adds an off-machine copy with a restore-path proof: the
DEST copy is re-opened read-only and integrity-checked + row/table-matched
against the source, so we know the cloud copy is actually restorable.

Default subject is the Bitunix backtest corpus data/btc_scalping.db.

Usage:
  python scripts/backup_corpus_db.py --db <src.db> --dest <off-machine dir> [--keep 5]

Read-only w.r.t. the source (online backup takes a shared lock only). Writes
only the backup file, the dest copy, and an append-only backup_log.tsv.

RESTORE: a .bak is a complete standalone SQLite file (already integrity-checked
here). To restore, stop any writer, then copy a chosen .bak over the live DB:
  copy "<dest>\\btc_scalping.db.<stamp>.bak" "C:\\Users\\AA Incorporado\\CC\\data\\btc_scalping.db"
Re-verify with: python -c "import sqlite3;print(sqlite3.connect('data/btc_scalping.db').execute('PRAGMA integrity_check').fetchone())"
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import sqlite3
import sys
from datetime import datetime, timezone


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def table_summary(path: str) -> tuple[str, int, list[tuple[str, int]]]:
    """(integrity_check, table_count, [(table,row_count)...]) opened read-only."""
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        integ = c.execute("PRAGMA integrity_check").fetchone()[0]
        tabs = [r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        counts = []
        for t in tabs:
            try:
                n = c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            except sqlite3.DatabaseError:
                n = -1
            counts.append((t, n))
        return integ, len(tabs), counts
    finally:
        c.close()


def online_backup(src: str, dst: str) -> None:
    """Consistent online copy (safe even if a writer holds the DB)."""
    s = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    d = sqlite3.connect(dst)
    try:
        s.backup(d)          # atomic page-by-page copy
    finally:
        d.close()
        s.close()


def prune(dest_dir: str, prefix: str, keep: int) -> list[str]:
    baks = sorted(f for f in os.listdir(dest_dir)
                  if f.startswith(prefix) and f.endswith(".bak"))
    removed = []
    while len(baks) > keep:
        victim = baks.pop(0)
        os.remove(os.path.join(dest_dir, victim))
        removed.append(victim)
    return removed


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=r"C:\Users\AA Incorporado\CC\data\btc_scalping.db")
    ap.add_argument("--dest", required=True, help="off-machine / cloud-synced backup dir")
    ap.add_argument("--local", default=None,
                    help=r"local staging dir OUTSIDE the repo (default: LOCALAPPDATA\trading_corp\backups)")
    ap.add_argument("--keep", type=int, default=5, help="retain last N backups per dir")
    args = ap.parse_args()

    src = os.path.abspath(args.db)
    if not os.path.exists(src):
        print(f"FAIL: source db not found: {src}")
        return 2
    name = os.path.basename(src)
    # Local staging lives OUTSIDE any git worktree so backups never pollute a
    # checkout / risk being committed.
    default_local = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
                                 "trading_corp", "backups")
    local_dir = args.local or default_local
    os.makedirs(local_dir, exist_ok=True)
    os.makedirs(args.dest, exist_ok=True)

    stamp = utc_stamp()
    bak_name = f"{name}.{stamp}.bak"
    local_bak = os.path.join(local_dir, bak_name)

    src_size = os.path.getsize(src)
    print(f"source : {src}  ({src_size/1e6:.1f} MB)")
    print(f"backup : {bak_name}")

    # 1) online consistent backup -> local staging
    online_backup(src, local_bak)

    # 2) verify the local backup (repo idiom: integrity check) + compare to source
    s_integ, s_ntab, s_counts = table_summary(src)
    b_integ, b_ntab, b_counts = table_summary(local_bak)
    ok_integ = (b_integ == "ok")
    ok_shape = (s_counts == b_counts)
    print(f"verify : source integrity={s_integ} tables={s_ntab}")
    print(f"         backup integrity={b_integ} tables={b_ntab}  shape_match={ok_shape}")
    print(f"         backup md5={md5(local_bak)}  size={os.path.getsize(local_bak)/1e6:.1f} MB")
    if not (ok_integ and ok_shape):
        print("FAIL: backup did not verify against source — NOT copying to dest.")
        return 3

    # 3) copy verified backup off-machine (cloud channel)
    dest_bak = os.path.join(args.dest, bak_name)
    shutil.copy2(local_bak, dest_bak)

    # 4) RESTORE-PATH PROOF: re-verify the dest copy independently
    d_integ, d_ntab, d_counts = table_summary(dest_bak)
    ok_dest = (d_integ == "ok" and d_counts == s_counts)
    print(f"dest   : {dest_bak}")
    print(f"restore: dest integrity={d_integ} tables={d_ntab} shape_match={d_counts == s_counts}"
          f"  md5={md5(dest_bak)}")
    if not ok_dest:
        print("FAIL: dest copy did not verify — restore path NOT proven.")
        return 4

    # 5) retention prune (both dirs) + append-only log
    for d in {local_dir, args.dest}:
        rp = prune(d, name + ".", args.keep)
        if rp:
            print(f"pruned : {d} -> removed {len(rp)} old ({', '.join(rp)})")

    biggest = max(s_counts, key=lambda kv: kv[1]) if s_counts else ("-", 0)
    log_line = (f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\t{name}\t{bak_name}\t"
                f"OK\tsize={os.path.getsize(dest_bak)}\ttables={s_ntab}\t"
                f"largest={biggest[0]}:{biggest[1]}\tmd5={md5(dest_bak)}")
    for d in {local_dir, args.dest}:
        with open(os.path.join(d, "backup_log.tsv"), "a", encoding="utf-8") as f:
            f.write(log_line + "\n")

    print("RESULT : OK — backup verified, off-machine copy verified, restore path proven.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
