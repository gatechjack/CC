# A / Phase 1b — live-freeze capture verdict (H1 vs H2 → NEITHER; it's H3)

§4 read-only (82fda13). Board-authorized agent execution of the read-only capture.
Branch `bitunix-deblock-eventloop-2026-06-16`. **Checkpoint — no fix written.**

## Method (and why it's `/proc`, not py-spy)

py-spy was installed isolated (`/tmp/pyspy_pkg/bin/py-spy` 0.4.2, prod venv untouched)
but **cannot run**: `ptrace_scope=1` requires sudo, and the NOPASSWD sudoers set is only
`systemctl/journalctl/sqlite3 trading-corp` — py-spy needs a password we don't have. So
I used the **no-sudo `/proc/<pid>/task/*/{stat,wchan}` capture** (same-user reads, allowed
under ptrace_scope=1), driven by a journal-silence watcher (`/tmp/pw3.sh`).

**Wrong-PID correction:** the target is the real Python engine **PID 2797301** (child of
the `xvfb-run` wrapper 2797287, which is 1 idle thread in `do_wait`). Earlier work pointed
at 2797287 — that would have dumped the wrapper, not the engine.

## Live capture — FREEZE#1, 2026-06-16 16:04:54→16:11+ (still frozen at last read, ~6 min)

3 `/proc` snapshots at silence=94/114/134 s, plus a utime delta — all consistent:

| signal | observed | implication |
|---|---|---|
| main thread (tid 2797301) | `state=R`, `wchan=0` ×3 | on-CPU, RUNNING, not in any syscall |
| utime/stime over 4 s | utime +269 ticks (~67%), stime +133 (~33%) | **burning ~a full core** (CPU-bound, not I/O wait) |
| other 11 threads | `S` / `futex_wait_queue` | idle, GIL-starved (waiting for the GIL the main thread holds) |
| network wchans (`tcp_recvmsg`/`sk_wait_data`) | **none** | NOT H1 (no to_thread pool saturation by hung network calls) |
| main-thread DB/disk/lock wchan (`io_schedule`/`fdatasync`/futex) | **none** (main is R, not waiting) | NOT H2 (no sync-DB-on-loop blocked on a lock) |

## VERDICT: H1 and H2 are BOTH REFUTED → it's **H3**

**The freeze is a CPU-bound / GIL-holding synchronous operation on the asyncio
event-loop (main) thread.** It executes Python (or a GIL-holding C call) continuously
for minutes; the loop never iterates, uvicorn (on that loop) can't serve, the other
threads can't get the GIL, and the async network timeouts only flush on thaw (victims —
which is why earlier thaw-clusters of BitUnix/Coinbase/kalshi ConnectTimeouts looked like
the cause but aren't).

This **invalidates both pre-registered fix plans**:
- H1's fix (timeouts on `to_thread` network calls) — wrong; the pool is idle.
- H2's fix (move sync DB off the loop) — wrong; the main thread isn't in a DB/disk wait.
- **H3's fix class:** the CPU-bound op must be (a) offloaded to a **ProcessPool** (a
  ThreadPool won't help — it's the GIL), or (b) **chunked with periodic `await asyncio.sleep(0)`**
  yields, or (c) algorithmically capped/optimized, or (d) removed if spurious. Which one
  depends on the exact op.

## What I deliberately did NOT do: name the culprit by guessing

The journal's last lines before the freeze were `yfinance: $BTCUSDT possibly delisted`
errors — BUT the yfinance wrapper (`data/yfinance_provider.py:51,119,138`) is already
`asyncio.to_thread`-offloaded, so those are most likely **off-loop noise, not the freezer**.
Other run-up activity (`polymarket_arbitrage/polymarket_scan_cycle`, `kalshi_market_map:
collected 312 tradeable markets`) are candidates for a heavy CPU op, but `/proc` is
kernel-level and **cannot name the Python frame**. Picking one would be exactly the
"fix blind" failure Phase 1 was created to prevent.

## To NAME the exact frame (required before Phase 2)

**py-spy during a freeze** — it prints the Python stack of the running main thread and
names the CPU-bound call (file:line) directly. The freeze recurs ~every 74 min and the
watcher (`/tmp/pw3.sh`) attempts py-spy on every capture (currently logging the sudo-deny).
To enable, the operator (who holds sudo) does ONE of:
- add a NOPASSWD sudoers drop-in for the py-spy dump (then the next freeze's watcher
  attempt succeeds — no password in transcript, scoped to py-spy), or
- run `sudo /tmp/pyspy_pkg/bin/py-spy dump --pid 2797301` during a freeze themselves.

Then Phase 2 targets the proven CPU-bound frame with the correct H3 fix.

## Artifacts
- Watcher `/tmp/pw3.sh` (armed, MAX_EVENTS=2; FREEZE#1 captured, awaiting #2).
- Captures: `/tmp/pyspy_dumps/proc_20260616T160628Z_{1,2,3}.txt` (+ py-spy sudo-deny logs).
- py-spy isolated at `/tmp/pyspy_pkg/bin/py-spy` (prod venv untouched).
