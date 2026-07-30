#!/usr/bin/env python3
"""R4 (REFERENCE-ONLY ARTIFACT — NOT RUN, NOT PENDING) — one-time latch clear for MaggieTheEagle.

STATUS 2026-07-30: **NEVER RUN and NO LONGER NEEDED.** R1 self-healed the Maggie latch
live on deploy (verified: snapshot advanced 3->1 = {KXFEDDECISION-26SEP-H0}, 3 scan cycles,
0 anomalies/0 alarms). This file exists only as a documented fallback; it is NOT a pending
task. Do not run it unless a future latch somehow fails to self-heal AND you explicitly decide to.

Context: the R1 fix SELF-HEALS the current MaggieTheEagle mass_disappearance latch
on the first post-deploy scan (both KXFEDDECISION-26JUL markets classify as
`resolved` -> snapshot advances to {KXFEDDECISION-26SEP-H0}, no alarm). So this
script is NOT needed for correctness — it only clears the latch at the DEPLOY
INSTANT instead of within one <=10-min poll. Decision (2026-07-30): SKIP by default.

This file is committed as a reviewable artifact. It is a DRY RUN: the actual
`set_agent_state` write is COMMENTED OUT. It prints the current snapshot (BACKUP)
and the intended post-write snapshot (WOULD WRITE) so the change can be reviewed
before any mutation. To actually run it (only on explicit go): uncomment the write
line, run on the prod venv, and keep the printed BACKUP for rollback.

Run (read-only dry run):
  /home/azureuser/trading_corp/venv/bin/python r4_clear_maggie_snapshot.py
Rollback (if ever run for real): re-write the printed BACKUP JSON to the same key.
"""
import json

from trading_corp.persistence.db import load_agent_state  # , set_agent_state

DB_URL = "sqlite:////home/azureuser/trading_corp/data/trading_corp.db"
AGENT = "kalshi_copy_trader"
KEY = "positions:MaggieTheEagle"
# The single market that is still ACTIVE on Kalshi (verified 2026-07-30 via the
# public markets API: -26JUL-H0/-H25 finalized, -26SEP-H0 active). Keep only this.
KEEP = {"KXFEDDECISION-26SEP-H0"}


def main() -> None:
    rec = load_agent_state(AGENT, KEY, db_url=DB_URL)
    snap = rec[0] if rec else {}
    print("BACKUP (current snapshot):", json.dumps(snap, sort_keys=True))
    if not isinstance(snap, dict):
        print("ABORT: snapshot is not a dict; nothing to do.")
        return
    new = {k: v for k, v in snap.items() if k in KEEP}
    # Guard: only proceed if the markets we intend to keep are actually present.
    missing = KEEP - set(snap.keys())
    if missing:
        print(f"ABORT: expected active markets not in snapshot: {sorted(missing)}")
        return
    print("WOULD WRITE (post-clear snapshot):", json.dumps(new, sort_keys=True))
    print(f"WOULD DROP: {sorted(set(snap.keys()) - KEEP)}")
    # --- DRY RUN. Uncomment the next line ONLY on explicit operator go: ---
    # set_agent_state(AGENT, KEY, new, db_url=DB_URL); print("WRITE COMMITTED")
    print("DRY RUN complete — no write performed.")


if __name__ == "__main__":
    main()
