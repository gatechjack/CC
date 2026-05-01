"""Fidelity broker via Playwright browser automation, multi-account.

Fidelity has no public API. This broker drives the web UI at fidelity.com:
  - Login: https://digital.fidelity.com/prgw/digital/login/full-page
  - Portfolio data: intercepted from Fidelity's internal JSON API calls
    (more reliable than DOM scraping — the API shape is stable across redesigns)
  - Trading: navigates the multi-leg options ticket

Multi-account model:
  Fidelity holds Joint, 401(k), Individual etc. under one login. Rather than
  open three Chromium instances, all FidelityBroker instances share a single
  Playwright session and a single portfolio capture. Each instance filters
  the shared capture down to its `account_filter` substring on snapshot().

  First connect() bootstraps the shared session (login + storage_state cache).
  Subsequent connects() are no-ops. The session refcount drives teardown.

Authentication: FIDELITY_USERNAME + FIDELITY_PASSWORD env vars.
First login opens a headed browser so you can complete 2FA if prompted.
The session cookie is cached in data/fidelity_session/ so subsequent runs
are fully headless.

Quotes always use yfinance (faster and more reliable than scraping).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from trading_corp.brokers.base import AccountSnapshot, Broker
from trading_corp.persistence.models import FillEvent, Position, ProposedOrder

log = logging.getLogger(__name__)

_SESSION_DIR = Path("data/fidelity_session")
_LOGIN_URL = "https://digital.fidelity.com/prgw/digital/login/full-page"
_PORTFOLIO_URL = "https://digital.fidelity.com/ftgw/digital/portfolio/positions"
_TRADE_URL = "https://digital.fidelity.com/ftgw/digital/trade-equity/index.html"

# Body-text fingerprints of Fidelity's "session rejected" error page. Loads
# with HTTP 200 + a generic "Sorry, we can't complete this action right now"
# message — usually triggered by bot detection, stale CSRF, rate-limit, or
# a partially-valid cached cookie that confuses their server. The fix is
# always: wipe data/fidelity_session/ and re-auth from scratch.
_REJECTION_BODY_MARKERS = (
    "can't complete this action",
    "cannot complete this action",
    "we're sorry, but we can't",
)


async def _is_rejection_page(page) -> bool:
    """Detect Fidelity's session-rejection error page.

    Checks body text rather than URL because Fidelity routes rejected
    sessions through several paths (/prgw/digital/signin/retail,
    /prgw/digital/error/*, etc.) but always renders the same copy.

    Whitespace is normalized before matching — Fidelity's actual page wraps
    "can't complete this\\n   action" across two lines, so a literal-space
    marker would miss it.
    """
    try:
        raw = (await page.inner_text("body"))[:2000]
    except Exception:
        return False
    # Collapse all whitespace runs (spaces, tabs, newlines) to single spaces.
    # ASCII-only lower() so matching is locale-stable. Also normalize
    # typographic apostrophe U+2019 → ASCII U+0027 since Fidelity sometimes
    # serves smart quotes that wouldn't match a plain "can't" marker.
    text = " ".join(raw.split()).lower().replace("’", "'")
    return any(m in text for m in _REJECTION_BODY_MARKERS)


def _wipe_session_cache(reason: str) -> None:
    """Remove the cached storage_state.json so the next startup starts clean.

    Called when we detect Fidelity rejected the session — the cookie cache
    is the most likely culprit, and leaving it in place would just bounce
    the next run to the same error page.
    """
    storage_path = _SESSION_DIR / "storage_state.json"
    try:
        if storage_path.exists():
            storage_path.unlink()
            log.warning(
                "FidelityBroker: wiped %s (%s). Next start will force "
                "headed login from scratch — solve any captcha/MFA prompt "
                "in the browser window that opens.",
                storage_path, reason,
            )
    except Exception as e:
        log.warning("FidelityBroker: failed to wipe %s: %s", storage_path, e)

# ── Shared session state ──────────────────────────────────────────────────
# All FidelityBroker instances share one Playwright browser/context to avoid
# logging in three separate times and consuming 3× the memory.
_SESSION_LOCK = asyncio.Lock()
_REFRESH_LOCK = asyncio.Lock()
_PORTFOLIO_TTL_SEC = 60          # cached portfolio refreshes at most every 60s

class _SharedSession:
    """Process-wide Fidelity Playwright session."""
    playwright: Any = None
    browser: Any = None
    context: Any = None
    page: Any = None
    connected: bool = False
    refcount: int = 0
    # List of (url, raw_response) pairs from the last capture. NOT merged —
    # merging top-level keys destroys structure when multiple responses share
    # them (e.g. every GraphQL response has top-level `data`). The parser
    # walks this list to find the response it cares about.
    portfolio_captures: list = []
    portfolio_ts: float = 0.0     # unix seconds at last successful refresh
    credentials: tuple[str, str] | None = None  # (username, password)
    bootstrap_failed: bool = False    # set True after first failed bootstrap;
                                      # subsequent connect() calls fail fast
                                      # so 3 divisions don't each spend
                                      # 90s+ retrying the same broken path
    bootstrap_error: str = ""

_session = _SharedSession()


async def _refresh_shared_portfolio() -> None:
    """Refresh the shared portfolio capture if stale (>=_PORTFOLIO_TTL_SEC old).

    Multiple FidelityBroker instances share one capture per TTL window, so
    we don't navigate Fidelity's portfolio page once per division per scan.

    Strategy: navigate to the positions page, dismiss any OneTrust/cookie
    banner that might block the portfolio API call, then wait long enough
    for deferred XHRs to flush. Capture every JSON response that looks
    like portfolio data (URL contains 'portfolio' or 'positions' or 'account').
    """
    if not _session.connected or _session.page is None:
        return

    now = time.time()
    if (now - _session.portfolio_ts) < _PORTFOLIO_TTL_SEC and _session.portfolio_captures:
        return  # cache fresh

    async with _REFRESH_LOCK:
        now = time.time()
        if (now - _session.portfolio_ts) < _PORTFOLIO_TTL_SEC and _session.portfolio_captures:
            return

        # Page liveness check — Fidelity's anti-bot can kill the page
        # mid-session, leaving a stale reference. Detect and recreate.
        page_dead = False
        try:
            if _session.page is None or _session.page.is_closed():
                page_dead = True
        except Exception:
            page_dead = True

        if page_dead:
            log.warning(
                "FidelityBroker: shared page is closed; recreating from context"
            )
            try:
                if _session.context is not None and not getattr(
                    _session.context, "_closed", False
                ):
                    _session.page = await _session.context.new_page()
                else:
                    log.error(
                        "FidelityBroker: shared context also closed — "
                        "session unrecoverable until restart"
                    )
                    return
            except Exception as e:
                log.error("FidelityBroker: failed to recreate page: %s", e)
                return

        captured: list[tuple[str, Any]] = []
        all_urls: list[str] = []

        async def _on_response(response) -> None:
            url = response.url
            if any(url.endswith(ext) for ext in (".js", ".css", ".png", ".svg", ".woff2", ".ico", ".jpg", ".gif")):
                return
            all_urls.append(url)
            try:
                data = await response.json()
                captured.append((url, data))
            except Exception:
                pass

        page = _session.page
        page.on("response", _on_response)
        try:
            try:
                await page.goto(_PORTFOLIO_URL, wait_until="load", timeout=60_000)
            except Exception as e:
                log.warning(
                    "FidelityBroker: portfolio goto raised %s; "
                    "continuing with whatever XHRs were captured", e,
                )

            # Try to dismiss any consent banner first
            await _dismiss_overlays(page)

            # Wait for ANY likely portfolio element to render. Many bank SPAs
            # fire the portfolio API only after the positions table starts
            # rendering — not on `load`. We try several known selector shapes.
            # If none match in 15s, we proceed with whatever we have.
            portfolio_selectors = [
                "[data-testid*='positions' i]",
                "[data-testid*='portfolio' i]",
                "[class*='positions-table' i]",
                "[class*='portfolio-summary' i]",
                "[class*='account-list' i]",
                "[class*='account-balance' i]",
                "table[class*='positions' i]",
                "[id*='portfolio' i]",
            ]
            try:
                await page.wait_for_selector(
                    ", ".join(portfolio_selectors),
                    timeout=15_000,
                    state="attached",
                )
                log.debug("FidelityBroker: portfolio element rendered")
            except Exception:
                # Not actionable — the GraphQL data still gets captured via
                # the network listener regardless of which selectors render.
                log.debug(
                    "FidelityBroker: no portfolio element matched within 15s; "
                    "relying on captured XHRs (this is normal)"
                )

            # Generous additional window for any deferred XHRs
            await asyncio.sleep(10.0)
        finally:
            page.remove_listener("response", _on_response)

        # Log final landed URL + a content snippet so we know if the page
        # actually rendered the portfolio or got bounced elsewhere.
        try:
            final_url = page.url
            body_text = (await page.inner_text("body"))[:200].replace("\n", " | ")
        except Exception:
            final_url = "?"
            body_text = "?"

        log.info(
            "FidelityBroker shared refresh: final_url=%s | %d total responses | "
            "%d JSON | body_snippet=%r",
            final_url, len(all_urls), len(captured), body_text,
        )
        # Always log every captured URL — these tell us whether the
        # portfolio API was actually hit.
        log.info("FidelityBroker captured URLs:")
        for url in all_urls[:50]:
            log.info("  - %s", url)

        if captured:
            # Store the raw (url, data) pairs WITHOUT merging. The parser
            # walks this list to find the right response — merging top-level
            # keys would destroy GraphQL responses (every one has top-level
            # `data`; whichever was last would win and clobber the others).
            _session.portfolio_captures = list(captured)
            _session.portfolio_ts = time.time()

            # Always dump every captured response to disk so the parser can
            # be tuned offline — invaluable when Fidelity's API shape changes
            # and we don't want to re-trigger logins (rate-limit risk).
            # Dumps each response as its own file, named by URL slug, plus a
            # combined index file. No-op on dump errors.
            try:
                import json as _json
                dump_dir = _SESSION_DIR / "last_capture"
                dump_dir.mkdir(parents=True, exist_ok=True)
                # Wipe previous capture (small enough to be cheap)
                for old in dump_dir.glob("*.json"):
                    try:
                        old.unlink()
                    except Exception:
                        pass

                index: list[dict] = []
                for i, (url, data) in enumerate(captured):
                    slug = (
                        url.split("?")[0]
                        .rstrip("/").split("/")[-1]
                        .replace(".", "_")[:60]
                        or f"item_{i}"
                    )
                    fname = f"{i:03d}_{slug}.json"
                    try:
                        (dump_dir / fname).write_text(
                            _json.dumps(data, indent=2)[:1_000_000],  # cap at 1MB per file
                            encoding="utf-8",
                        )
                        index.append({"url": url, "file": fname,
                                      "is_dict": isinstance(data, dict),
                                      "top_keys": list(data.keys())[:30] if isinstance(data, dict) else None,
                                      "list_len": len(data) if isinstance(data, list) else None})
                    except Exception as e:
                        log.debug("FidelityBroker dump skip %s: %s", url, e)

                (dump_dir / "_index.json").write_text(
                    _json.dumps({
                        "captured_at": datetime.now(timezone.utc).isoformat(),
                        "final_url": (final_url if 'final_url' in dir() else "?"),
                        "total_responses": len(all_urls),
                        "json_responses": len(captured),
                        "responses": index,
                    }, indent=2),
                    encoding="utf-8",
                )
                log.info(
                    "FidelityBroker: captured payload dumped to %s (%d files) "
                    "— inspect to tune _parse_portfolio without re-logging",
                    dump_dir, len(captured),
                )
            except Exception as e:
                log.debug("FidelityBroker capture dump failed: %s", e)
        else:
            log.warning(
                "FidelityBroker: shared refresh captured 0 JSON responses; "
                "session may be expired — delete data/fidelity_session/storage_state.json "
                "and restart for a fresh login."
            )


async def _dismiss_overlays(page) -> None:
    """Click through common Fidelity overlays that block portfolio data.

    Best-effort — silently ignores anything that doesn't match.
    """
    overlay_selectors = [
        "#onetrust-accept-btn-handler",                  # OneTrust cookie banner
        "button:has-text('Accept All Cookies')",
        "button:has-text('Accept Cookies')",
        "button:has-text('I Accept')",
        "button:has-text('Continue')",
        "button[aria-label*='accept' i]",
        "button[aria-label*='close' i]",
        "div[role='dialog'] button:has-text('OK')",
    ]
    for sel in overlay_selectors:
        try:
            await page.click(sel, timeout=1_500)
            log.info("FidelityBroker: dismissed overlay via selector %r", sel)
        except Exception:
            pass


class FidelityBroker(Broker):
    name = "fidelity"
    paper = False

    def __init__(
        self,
        username: str,
        password: str,
        target_account: str | None = None,
    ) -> None:
        self._username = username
        self._password = password
        # Case-insensitive substring to match the account name/type, e.g. "Joint".
        # None = use all accounts (sum everything).
        self._target_account = target_account.strip() if target_account else None
        self._connected = False
        # Stash credentials on the shared session so the first instance to
        # connect drives the (one-time) login.
        if _session.credentials is None:
            _session.credentials = (username, password)

    # ------------------------------------------------------------------
    # Lifecycle (shared across all FidelityBroker instances)
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        async with _SESSION_LOCK:
            # Fail fast on subsequent attempts after a bootstrap failure.
            # Without this, three divisions each spend ~90s retrying the
            # same broken path (hostile network, Fidelity blocking, etc.),
            # holding up the dashboard boot for 4-5 minutes.
            if _session.bootstrap_failed:
                raise RuntimeError(
                    f"Fidelity shared session bootstrap previously failed: "
                    f"{_session.bootstrap_error or '(unknown)'}. "
                    "Restart the process to retry."
                )

            _session.refcount += 1
            if not _session.connected:
                try:
                    await self._bootstrap_shared_session()
                except Exception as e:
                    # Mark the session as permanently failed for this process
                    # lifetime so the other Fidelity brokers fail fast.
                    _session.refcount = max(0, _session.refcount - 1)
                    _session.bootstrap_failed = True
                    _session.bootstrap_error = str(e)[:300]
                    raise
        self._connected = True
        log.info(
            "FidelityBroker bound: target_account=%r (refcount=%d, shared_connected=%s)",
            self._target_account, _session.refcount, _session.connected,
        )

    async def _bootstrap_shared_session(self) -> None:
        """First-time setup: launch playwright, restore session cookie or login.

        Uses ONE browser process at a time — between mode switches we fully
        close and relaunch so the user only ever sees a single Firefox window.
        Previous version reused `_session.browser` across mode switches, which
        caused the dreaded "2 logon screens" symptom (a phantom headless
        instance lingering while the headed login one was visible).

        Phases:
          A. (if cookie exists) Try headless reuse. No window expected.
             → success: done. → failure: close browser entirely, fall through.
          B. Fresh headed login. ONE visible window. Save cookie. Close browser.
          C. Relaunch headless with new cookie. No window.
        """
        from playwright.async_api import async_playwright  # type: ignore

        _SESSION_DIR.mkdir(parents=True, exist_ok=True)
        storage_path = _SESSION_DIR / "storage_state.json"

        _session.playwright = await async_playwright().start()

        # ── Phase A: try headless reuse of cached session ──
        if storage_path.exists():
            try:
                ctx = await self._make_context(
                    headless=True, storage=str(storage_path), force_relaunch=True,
                )
                page = await ctx.new_page()
                await page.goto(_PORTFOLIO_URL, wait_until="commit", timeout=60_000)
                # Detect Fidelity's "Sorry, we can't complete this action"
                # rejection page BEFORE the URL/body checks below — the
                # rejection page can land on URLs that look auth-ish.
                if await _is_rejection_page(page):
                    log.warning(
                        "FidelityBroker: cached session rejected by Fidelity "
                        "(generic 'can't complete this action' page). "
                        "Wiping cache and forcing fresh headed login."
                    )
                    _wipe_session_cache("Phase A rejection detected")
                    # Cleanup happens at the unified close below; just
                    # fall through to Phase B with cache wiped.
                else:
                    page_text = (await page.content()).lower()
                    session_ok = (
                        "/ftgw/" in page.url
                        and "login" not in page.url.lower()
                        and "signin" not in page.url.lower()
                        and "sign in" not in page_text[:2000]
                    )
                    if session_ok:
                        await page.wait_for_load_state("domcontentloaded", timeout=60_000)
                        _session.context = ctx
                        _session.page = page
                        _session.connected = True
                        log.info("FidelityBroker: reused cached session (headless)")
                        return
                    # Bad/expired cookie. Close everything before headed launch.
                    log.info(
                        "FidelityBroker: cached session expired (url=%s); "
                        "tearing down headless and re-logging in", page.url,
                    )
                await ctx.close()
                if _session.browser is not None:
                    await _session.browser.close()
                    _session.browser = None
            except Exception as e:
                log.warning(
                    "FidelityBroker: headless session check failed (%s); re-logging in.", e,
                )
                # Tear down whatever's lingering before we open the headed window
                try:
                    if _session.browser is not None:
                        await _session.browser.close()
                except Exception:
                    pass
                _session.browser = None

        # ── Phase B: fresh headed login ──
        ctx = await self._make_context(headless=False, force_relaunch=True)
        page = await ctx.new_page()
        try:
            await self._do_login(page)
        except Exception as e:
            log.error("FidelityBroker: fresh login failed (%s)", e)
            try:
                await ctx.close()
                if _session.browser is not None:
                    await _session.browser.close()
                _session.browser = None
            except Exception:
                pass
            raise
        await ctx.storage_state(path=str(storage_path))
        log.info("FidelityBroker: session saved to %s", storage_path)

        # Close the headed browser fully before relaunching headless,
        # so the user only sees ONE window at a time.
        await ctx.close()
        if _session.browser is not None:
            await _session.browser.close()
            _session.browser = None

        # ── Phase C: relaunch headless with the freshly saved cookie ──
        ctx = await self._make_context(
            headless=True, storage=str(storage_path), force_relaunch=True,
        )
        page = await ctx.new_page()

        _session.context = ctx
        _session.page = page
        _session.connected = True
        log.info("FidelityBroker shared session online (user=%s)", self._username)

    async def disconnect(self) -> None:
        if not self._connected:
            return
        self._connected = False
        async with _SESSION_LOCK:
            _session.refcount = max(0, _session.refcount - 1)
            if _session.refcount > 0:
                # Other instances still using the shared session
                return
            # Last one out tears down
            try:
                if _session.context:
                    await _session.context.close()
                if _session.browser:
                    await _session.browser.close()
                if _session.playwright:
                    await _session.playwright.stop()
            except Exception as e:
                log.warning("FidelityBroker: shared session teardown error: %s", e)
            finally:
                _session.context = None
                _session.browser = None
                _session.playwright = None
                _session.page = None
                _session.connected = False
                _session.portfolio_captures = []
                _session.portfolio_ts = 0.0
                log.info("FidelityBroker: shared session torn down (last instance)")

    # ------------------------------------------------------------------
    # Account data
    # ------------------------------------------------------------------

    async def snapshot(self) -> AccountSnapshot:
        """Return the AccountSnapshot for THIS instance's target_account.

        Reuses a shared portfolio capture (refreshed at most every
        `_PORTFOLIO_TTL_SEC` seconds) so multi-account dashboards don't
        navigate Fidelity's portfolio page three times in a row.
        """
        self._require_connected()
        await _refresh_shared_portfolio()
        return self._parse_portfolio(_session.portfolio_captures)

    def _parse_portfolio(self, captures: list) -> AccountSnapshot:
        """Parse Fidelity's GraphQL portfolio response into an AccountSnapshot.

        Fidelity's portfolio page fires a GraphQL query at
            /ftgw/digital/portfolio/api/graphql?ref_at=portsum
        whose response shape (verified 2026-04-28) is:

            {"data": {"getContext": {"person": {
                "balances": {"balanceDetail": {"gainLossBalanceDetail": {
                    "totalMarketVal": <total across all accounts>,
                    "todaysGainLoss": ..., "todaysGainLossPct": ...
                }}},
                "assets": [
                    {
                        "acctNum": "Z34518932",
                        "acctType": "Brokerage",
                        "preferenceDetail": {"name": "Joint WROS", "acctGroupId": "IA"},
                        "acctRelAttrDetail": {"relRoleTypeCode": "JOINTWROS"},
                        "gainLossBalanceDetail": {"totalMarketVal": 17012.80, ...},
                        ...
                    },
                    ...
                ],
            }}}}

        With `target_account` set: returns the sum of matching `assets[]`
        entries (name substring match against `preferenceDetail.name`,
        `acctSubTypeDesc`, or `acctNum`).

        With `target_account` unset (None): returns the rolled-up
        `totalMarketVal` across all accounts.
        """
        equity = 0.0
        buying_power = 0.0
        positions: list[Position] = []
        target = self._target_account.lower() if self._target_account else None

        # Find the GraphQL response containing person.assets data. Several
        # graphql responses come back per page load; we want the one with
        # `getContext.person.assets` populated.
        person: dict | None = None
        try:
            for url, body in captures:
                if not isinstance(body, dict):
                    continue
                data = body.get("data")
                if not isinstance(data, dict):
                    continue
                ctx = data.get("getContext")
                if not isinstance(ctx, dict):
                    continue
                p = ctx.get("person")
                if isinstance(p, dict) and isinstance(p.get("assets"), list) and p["assets"]:
                    person = p
                    break
        except Exception as e:
            log.warning("FidelityBroker: error walking captures: %s", e)

        if person is None:
            # No usable response — could be an expired session, a bad
            # capture, or Fidelity changed their API. Delete the cookie
            # and re-login if this persists.
            log.warning(
                "FidelityBroker: no `getContext.person.assets` response found "
                "in %d captures. If this happens repeatedly, delete "
                "data/fidelity_session/storage_state.json and re-login.",
                len(captures),
            )
            return AccountSnapshot(
                account=self._username, equity=0.0, buying_power=0.0,
                cash=0.0, positions=[],
            )

        # Walk assets, optionally filtering by target_account
        matched_names: list[tuple[str, float]] = []
        for acct in person.get("assets", []):
            if not isinstance(acct, dict):
                continue
            pref = acct.get("preferenceDetail") or {}
            name = (
                (pref.get("name") or "")
                or (acct.get("acctSubTypeDesc") or "")
                or (acct.get("acctType") or "")
            )
            name_lower = name.lower()
            acct_num = str(acct.get("acctNum") or "")
            rel_type = (
                (acct.get("acctRelAttrDetail") or {}).get("relRoleTypeCode") or ""
            ).lower()

            # Multi-field substring match
            if target and not (
                target in name_lower
                or target in acct_num.lower()
                or target in rel_type
            ):
                continue

            gl = acct.get("gainLossBalanceDetail") or {}
            acct_equity = float(gl.get("totalMarketVal") or 0)
            equity += acct_equity
            matched_names.append((name or acct_num, acct_equity))

        if target is not None:
            log.info(
                "FidelityBroker: target_account=%r matched %d account(s): %s = $%s total",
                self._target_account, len(matched_names),
                [n for n, _ in matched_names], f"{equity:,.2f}",
            )
        else:
            # No filter: use the rolled-up balance across all accounts as a
            # cross-check. (Should equal sum of per-account equity.)
            top = person.get("balances", {}).get("balanceDetail", {}) \
                                            .get("gainLossBalanceDetail", {})
            rolled = float(top.get("totalMarketVal") or 0)
            if rolled and not equity:
                # Sum-from-assets failed but rolled total is present — use it.
                equity = rolled

        return AccountSnapshot(
            account=self._target_account or self._username,
            equity=equity,
            buying_power=buying_power,
            cash=buying_power,
            positions=positions,
        )

    async def quote(self, symbol: str) -> float:
        """Last trade price via yfinance (no Playwright round-trip needed)."""
        if " " in symbol or "#" in symbol:
            return 0.0
        try:
            import yfinance as yf  # type: ignore
            def _get() -> float:
                info = yf.Ticker(symbol).fast_info
                price = (
                    getattr(info, "last_price", None)
                    or getattr(info, "previous_close", None)
                )
                return float(price) if price else 0.0
            return await asyncio.to_thread(_get)
        except Exception as e:
            log.warning("FidelityBroker: quote failed for %s: %s", symbol, e)
            return 0.0

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    async def place_order(self, order: ProposedOrder) -> FillEvent:
        self._require_connected()
        extra = order.extra or {}
        if extra.get("is_spread"):
            return await self._place_spread_order(order)
        return await self._place_single_order(order)

    async def _place_spread_order(self, order: ProposedOrder) -> FillEvent:
        """Route a multi-leg spread through Fidelity's web multi-leg ticket.

        Full Playwright selector automation is written as a skeleton here.
        The specific CSS selectors must be confirmed against the live Fidelity
        UI before enabling live trading.  Run with headless=False and
        paper_mode=True on first use to observe and validate the form flow.

        Fidelity multi-leg URL pattern:
          /ftgw/digital/trade-equity/index.html#multi-leg;symbol=SPY
        """
        extra = order.extra or {}
        underlying = extra.get("underlying", order.symbol)
        legs = extra.get("legs", [])
        strategy = extra.get("strategy_variant", "spread")
        limit = order.limit_price or 0.0

        log.info(
            "FidelityBroker: %s %s (%d legs) @ $%.2f net — "
            "Playwright UI automation not yet validated on live site; "
            "logged as simulated fill. "
            "To place manually: fidelity.com → Trade → Multi-Leg Options.",
            strategy, underlying, len(legs), limit,
        )

        # Navigate to the trade ticket (opens the page; leg entry is TODO)
        await _session.page.goto(
            f"{_TRADE_URL}#multi-leg;symbol={underlying}",
            wait_until="domcontentloaded",
            timeout=30_000,
        )

        return FillEvent(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            qty=float(order.qty),
            price=limit,
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            venue="fidelity-simulated",
        )

    async def _place_single_order(self, order: ProposedOrder) -> FillEvent:
        log.warning(
            "FidelityBroker: single-leg option order not yet automated for %s; "
            "logging as simulated fill.",
            order.symbol,
        )
        return FillEvent(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            qty=float(order.qty),
            price=float(order.limit_price or 0),
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            venue="fidelity-simulated",
        )

    async def cancel_order(self, order_id: str) -> bool:
        log.warning("FidelityBroker: cancel_order not yet automated for %s", order_id)
        return False

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _make_context(
        self,
        headless: bool,
        storage: str | None = None,
        force_relaunch: bool = False,
    ):
        """Build a Playwright browser context bound to the shared session.

        IMPORTANT: uses Firefox, NOT Chromium. Fidelity's Akamai-fronted HTTP/2
        servers reject Chromium-class browsers (both bundled Playwright Chromium
        and real Edge fail with `ERR_HTTP2_PROTOCOL_ERROR`). Firefox has a
        completely separate HTTP/2 implementation that Fidelity accepts.

        First-time setup: `playwright install firefox` (~110MB) is required.

        `force_relaunch=True` closes any existing browser before launching a
        new one with the requested `headless` mode. Use this when switching
        between headless/headed phases — Playwright can't change a browser's
        headless mode after launch.
        """
        kwargs: dict[str, Any] = {
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) "
                "Gecko/20100101 Firefox/131.0"
            ),
            "viewport": {"width": 1280, "height": 900},
        }
        if storage:
            kwargs["storage_state"] = storage

        if force_relaunch and _session.browser is not None:
            try:
                await _session.browser.close()
            except Exception:
                pass
            _session.browser = None

        if _session.browser is None:
            _session.browser = await _session.playwright.firefox.launch(
                headless=headless,
            )
        ctx = await _session.browser.new_context(
            **kwargs,
            java_script_enabled=True,
            extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
        )
        await ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        return ctx

    async def _do_login(self, page) -> None:
        """Fill credentials and wait for post-login redirect.

        Tries multiple known selector patterns because Fidelity periodically
        redesigns their login page. Falls back to waiting for you to complete
        the login manually in the headed browser window (5-minute window).
        """
        await page.goto(_LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
        # NOTE: do NOT wait for "networkidle" here — Fidelity's login page has
        # long-polling analytics that never let the network settle. The selector
        # waits below already handle "is the form ready?" with 4s budgets each.

        _USER_SELECTORS = [
            "#userId-input",
            "input[name='username']",
            "input[autocomplete='username']",
            "input[id*='user' i]",
            "input[placeholder*='user' i]",
            "input[placeholder*='login' i]",
            "input[type='text']:visible",
        ]
        _PASS_SELECTORS = [
            "#pin-input",
            "input[type='password']",
            "input[name='password']",
            "input[id*='pin' i]",
            "input[id*='pass' i]",
            "input[autocomplete='current-password']",
        ]
        _SUBMIT_SELECTORS = [
            "button[type='submit']",
            "input[type='submit']",
            "button:has-text('Log In')",
            "button:has-text('Sign In')",
            "button:has-text('Continue')",
        ]

        # Pace the form fill so Fidelity's anti-bot doesn't reject the
        # submission as inhuman. Bot-detection layers measure key timings;
        # a sub-second username→password→submit triggers their heuristics.
        # Use type() with delay (per-character keystroke pause) instead of
        # fill() (instant), and ~1.5-2s pauses between fields.
        filled = False
        for sel in _USER_SELECTORS:
            try:
                await page.wait_for_selector(sel, timeout=4_000)
                # Click first to focus, then type with per-key delay
                await page.click(sel)
                await page.fill(sel, "")  # clear any prefill
                await page.type(sel, self._username, delay=80)
                log.info("FidelityBroker: filled username using selector '%s'", sel)
                filled = True
                break
            except Exception:
                continue

        if filled:
            await asyncio.sleep(1.5)  # human-paced pause between fields

            for sel in _PASS_SELECTORS:
                try:
                    await page.wait_for_selector(sel, timeout=4_000)
                    await page.click(sel)
                    await page.fill(sel, "")
                    await page.type(sel, self._password, delay=80)
                    log.info("FidelityBroker: filled password using selector '%s'", sel)
                    break
                except Exception:
                    continue

            await asyncio.sleep(2.0)  # human-paced pause before submit

            for sel in _SUBMIT_SELECTORS:
                try:
                    await page.click(sel, timeout=4_000)
                    log.info("FidelityBroker: clicked submit using selector '%s'", sel)
                    break
                except Exception:
                    continue
        else:
            log.warning(
                "FidelityBroker: could not auto-fill login form (selectors changed?). "
                "Please complete the login manually in the browser window."
            )

        # Wait for post-login redirect.
        #
        # CRITICAL: must wait for a *post-auth* URL, not just "URL changed".
        # Fidelity's URL hierarchy:
        #   /prgw/digital/login/full-page    pre-auth login form
        #   /prgw/digital/signin/retail      pre-auth signin landing (form rejected → here)
        #   /prgw/digital/2fa/...            pre-auth 2FA prompt
        #   /ftgw/digital/...                POST-AUTH (actual account pages)
        #
        # The previous check `!url.includes('login')` returned True the
        # moment Fidelity bounced us from /login/full-page to /signin/retail
        # (which doesn't literally contain "login") — even though we were
        # still completely unauthenticated. We then cached a useless cookie.
        #
        # Now: require the URL to land in /ftgw/ which only happens after
        # successful credential validation + any 2FA challenges.
        log.info(
            "FidelityBroker: waiting for STABLE post-login URL on /ftgw/* "
            "(complete 2FA in the browser if prompted — up to 5 min)..."
        )

        # Stability-based wait. Fidelity's auth redirect chain may briefly
        # touch /ftgw/ during a token-setting hop before bouncing back to
        # /prgw/digital/signin/retail (rejected creds) or /prgw/digital/2fa
        # (challenge). A simple "URL contains /ftgw/" check resolves on that
        # transit hop in milliseconds. We instead poll every 1s and require
        # the URL to remain on /ftgw/ for `_STABLE_SAMPLES` consecutive
        # samples before declaring success.
        deadline = time.monotonic() + 300        # 5 min total budget
        _STABLE_SAMPLES = 3                       # 3 consecutive 1s polls = 3s stable
        # Check for the rejection page periodically — every Nth poll, since
        # inner_text() is more expensive than reading page.url. The rejection
        # page renders in seconds; checking every ~3s catches it fast without
        # spamming page.inner_text() at 1Hz.
        _REJECTION_POLL_INTERVAL = 3
        stable = 0
        poll_count = 0
        while time.monotonic() < deadline:
            try:
                cur_url = page.url or ""
            except Exception:
                cur_url = ""
            cur_lower = cur_url.lower()
            on_post_auth = (
                "/ftgw/" in cur_url
                and "signin" not in cur_lower
                and "login" not in cur_lower
                and "/2fa" not in cur_lower
                and "verify" not in cur_lower
            )
            if on_post_auth:
                stable += 1
                if stable >= _STABLE_SAMPLES:
                    break
            else:
                stable = 0
                # Fast-fail on Fidelity's "Sorry, we can't complete this
                # action" rejection page — no human action will fix this in
                # the 5-min window. Wipe the cache so the NEXT startup
                # forces a clean re-auth, then raise immediately.
                if poll_count % _REJECTION_POLL_INTERVAL == 0:
                    if await _is_rejection_page(page):
                        try:
                            stuck_url = page.url
                        except Exception:
                            stuck_url = "?"
                        log.error(
                            "FidelityBroker: Fidelity rejected the login session "
                            "with their generic error page ('Sorry, we can't "
                            "complete this action right now'). URL=%s. "
                            "This is usually bot detection, rate-limit, or stale "
                            "CSRF. Wiping cached session — restart trading_corp "
                            "in 5-10 minutes for a fresh headed login.",
                            stuck_url,
                        )
                        _wipe_session_cache("Phase B rejection page detected")
                        raise RuntimeError(
                            "FidelityBroker: session rejected by Fidelity "
                            "(generic 'can't complete this action' page). "
                            "Cache wiped — wait 5-10 min and restart."
                        )
            poll_count += 1
            await asyncio.sleep(1.0)
        else:
            # 5-minute deadline expired without 3 stable samples
            try:
                stuck_url = page.url
                stuck_text = (await page.inner_text("body"))[:300]
            except Exception:
                stuck_url, stuck_text = "?", "?"
            log.error(
                "FidelityBroker: login did not stably reach /ftgw/* within 5min. "
                "Last URL=%s. Body snippet: %r. "
                "If you see 'Username/Password' in the snippet, credentials "
                "were rejected (wrong, CAPTCHA, or rate-limit). If you see "
                "'Verify' / 'Code' / 'security question', a 2FA challenge "
                "was waiting — complete it manually in the Firefox window.",
                stuck_url, stuck_text,
            )
            raise TimeoutError(
                f"FidelityBroker login timeout — last URL was {stuck_url}"
            )

        log.info("FidelityBroker: login complete (stable post-auth URL=%s)", page.url)

    def _require_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("FidelityBroker not connected — call connect() first")
