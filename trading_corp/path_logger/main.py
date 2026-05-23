"""Entry point for the Kalshi BTC order-book path logger daemon.

Run as:
    python -m trading_corp.path_logger

Key constraints:
  - PID lock at data/path_logger.pid (SEPARATE from data/trading_corp.pid)
  - NTP MUST be synced at startup (sys.exit(1) if not)
  - Uses data/path_logger.db (NEVER data/trading_corp.db)
  - Never imports agents.data_exec, agents.risk, or trading_corp.web.*
  - Never places orders; this is a read-only observation sidecar
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
import stat
import sys
import tempfile
from pathlib import Path

from trading_corp.utils.secrets import load_secrets, RedactingFilter
from trading_corp.path_logger.logger import run_logger_tasks

# ── Configuration ─────────────────────────────────────────────────────────────

_PID_FILE = Path("data/path_logger.pid")
_DB_PATH = "data/path_logger.db"

log = logging.getLogger(__name__)

# ── PID lock (separate from trading_corp.main._acquire_lock) ──────────────────
# Copied verbatim from trading_corp/main.py _acquire_lock / _release_lock,
# with _PID_FILE pointing to data/path_logger.pid (different file).
# DO NOT import or share trading_corp.main's lock.


def _acquire_lock() -> bool:
    """Atomically claim data/path_logger.pid. Return False if another live instance owns it.

    Uses O_CREAT|O_EXCL so two processes starting simultaneously cannot both
    claim the lock. Stale PID files (process gone) are reaped and the caller
    retries the atomic claim exactly once.
    """
    _PID_FILE.parent.mkdir(parents=True, exist_ok=True)

    def _atomic_claim() -> bool:
        try:
            fd = os.open(str(_PID_FILE), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        try:
            os.write(fd, str(os.getpid()).encode("ascii"))
        finally:
            os.close(fd)
        return True

    if _atomic_claim():
        return True

    try:
        old_pid = int(_PID_FILE.read_text().strip())
    except (ValueError, OSError):
        old_pid = -1

    if old_pid == os.getpid():
        return True  # re-entrant — we already own it

    if old_pid > 0:
        try:
            os.kill(old_pid, 0)  # signal 0 = existence check
            return False          # other process is alive
        except OSError:
            pass                  # stale PID file — fall through to reap

    try:
        _PID_FILE.unlink()
    except OSError:
        return False
    return _atomic_claim()


def _release_lock() -> None:
    try:
        if _PID_FILE.exists() and int(_PID_FILE.read_text().strip()) == os.getpid():
            _PID_FILE.unlink()
    except Exception:
        pass


# ── NTP pre-flight ────────────────────────────────────────────────────────────

def _check_ntp_sync() -> bool:
    """Return True if timedatectl reports NTPSynchronized=yes.

    Runs synchronously at startup. Uses subprocess because asyncio event loop
    is not yet running at this point.
    """
    import subprocess
    try:
        result = subprocess.run(
            ["timedatectl", "show", "--property=NTPSynchronized,NTP"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        output = result.stdout
        # timedatectl show emits lines like: NTPSynchronized=yes
        if "NTPSynchronized=yes" in output:
            return True
        log.critical(
            "path_logger: NTP pre-flight FAILED. "
            "timedatectl output: %r. "
            "Cannot start with unsynchronised clock — exiting.",
            output.strip(),
        )
        return False
    except FileNotFoundError:
        # timedatectl not available (non-systemd host, e.g. developer machine)
        log.warning(
            "path_logger: timedatectl not found — skipping NTP pre-flight check. "
            "Ensure NTP is synchronised on the deployment host."
        )
        return True  # Non-fatal on dev environments; prod requires timedatectl
    except Exception as exc:
        log.critical("path_logger: NTP pre-flight check failed: %s", exc)
        return False


# ── Kalshi client factory ─────────────────────────────────────────────────────

def _build_kalshi_client(api_key_id: str, private_key_pem: str):  # type: ignore[return]
    """Materialise the PEM to a restricted tempfile and construct AsyncKalshiClient.

    Replicates the pattern in trading_corp/brokers/kalshi.py lines 96–116.
    The tempfile is in /tmp (systemd PrivateTmp=true provides a private /tmp).
    Returns (client, tmp_path) so the caller can clean up on exit.
    """
    from pykalshi import AsyncKalshiClient  # type: ignore[import]

    fd, key_path_str = tempfile.mkstemp(prefix="path_logger_kalshi_", suffix=".pem")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(private_key_pem)
        key_path = Path(key_path_str)
        try:
            os.chmod(key_path, stat.S_IRUSR | stat.S_IWUSR)  # 0600 — owner-only
        except (OSError, NotImplementedError):
            pass  # No-op on Windows dev environments

        client = AsyncKalshiClient(
            api_key_id=api_key_id,
            private_key_path=str(key_path),
        )
        return client, key_path
    except Exception:
        # Clean up tempfile if client construction fails
        try:
            Path(key_path_str).unlink(missing_ok=True)
        except Exception:
            pass
        raise


# ── Async main ────────────────────────────────────────────────────────────────

async def _async_main(client: object) -> None:
    """Top-level coroutine. Installs signal handlers then runs logger tasks."""
    loop = asyncio.get_running_loop()

    # Gather task handle so signal handlers can cancel it
    main_task = asyncio.current_task()

    def _handle_signal(sig_name: str) -> None:
        log.info("path_logger: received %s — initiating graceful shutdown", sig_name)
        if main_task is not None:
            main_task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, lambda s=sig.name: _handle_signal(s))
        except NotImplementedError:
            # Windows does not support add_signal_handler — fall back to
            # SIGINT only via KeyboardInterrupt which asyncio propagates.
            pass

    try:
        await run_logger_tasks(client, _DB_PATH)
    except asyncio.CancelledError:
        log.info("path_logger: event loop cancelled — exiting cleanly")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    """Configure logging, acquire lock, run NTP pre-flight, start event loop."""
    # Logging: write to stderr; systemd captures to journal via StandardError=journal
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    # Install secret redaction on root logger
    root_logger = logging.getLogger()
    root_logger.addFilter(RedactingFilter())

    # ── PID lock ──
    if not _acquire_lock():
        log.critical(
            "path_logger: another instance is already running (PID file: %s) — exiting",
            _PID_FILE,
        )
        sys.exit(1)

    try:
        # ── NTP pre-flight ──
        if not _check_ntp_sync():
            sys.exit(1)
        log.info("path_logger: NTP sync confirmed")

        # ── Load credentials from Key Vault / .env ──
        secrets = load_secrets()

        if not secrets.kalshi_api_key_id or not secrets.kalshi_private_key_pem:
            log.critical(
                "path_logger: KALSHI_API_KEY_ID and/or KALSHI_PRIVATE_KEY_PEM not set — exiting"
            )
            sys.exit(1)

        # Materialise PEM to tempfile for AsyncKalshiClient
        client, key_path = _build_kalshi_client(
            secrets.kalshi_api_key_id,
            secrets.kalshi_private_key_pem,
        )
        log.info("path_logger: Kalshi client constructed (key_path=%s)", key_path)

        # Coinbase ccxt.pro — no auth needed for public watch_ticker('BTC/USD').
        # If future Kalshi API tightening requires auth, set COINBASE_API_KEY /
        # COINBASE_API_SECRET in the KV and the ccxt exchange below.
        # (secrets.coinbase_api_key and secrets.coinbase_api_secret available if needed)

        # ── Run event loop ──
        try:
            asyncio.run(_async_main(client))
        finally:
            # Clean up PEM tempfile on any exit path
            try:
                key_path.unlink(missing_ok=True)
                log.info("path_logger: PEM tempfile removed")
            except Exception as exc:
                log.warning("path_logger: could not remove PEM tempfile %s: %s", key_path, exc)

    finally:
        _release_lock()
        log.info("path_logger: PID lock released — exited cleanly")


if __name__ == "__main__":
    main()
