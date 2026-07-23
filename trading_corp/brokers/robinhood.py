"""Robinhood broker — Phase 3 implementation via robin_stocks, multi-account.

All robin_stocks calls are synchronous; every one is wrapped with
asyncio.to_thread so the async graph never stalls.

Multi-account model:
  robin_stocks logs in once per process (module-level session). Multiple
  RobinhoodBroker instances can share that login — each instance binds to
  one account via the `account_filter` parameter (passed to robin_stocks'
  account_number argument on every per-account call).

  account_filter values (case-insensitive):
    "individual"  → first non-IRA, non-joint account
    "ira_roth"    → IRA Roth account (matches brokerage_account_type)
    "ira"         → first IRA-type account (Roth or Traditional)
    "joint"       → joint account
    <number>      → exact match on account_number (or substring)
    "" / None     → default (first) account

Authentication: username + password from env vars ROBINHOOD_USERNAME /
ROBINHOOD_PASSWORD. If you have a TOTP MFA app (Authenticator), also set
ROBINHOOD_MFA_SECRET (base32 secret) — the code is generated via pyotp.
Without it, robin_stocks falls back to SMS on first login and caches the
session token (store_session=True) so subsequent runs skip the prompt.
"""
from __future__ import annotations

import asyncio
import logging
import re
import os  # ITEM3-RH-AUTH
import sys
import time
from datetime import date, datetime, timezone
from threading import Lock

from trading_corp.brokers.base import AccountSnapshot, Broker, validate_combo_cohesion
from trading_corp.persistence.models import FillEvent, Position, ProposedOrder

log = logging.getLogger(__name__)

# Module-level shared login state. robin_stocks itself is a process-wide
# singleton, so we just need to ensure rs.login() runs at most once across
# all RobinhoodBroker instances.
_LOGIN_LOCK = Lock()
_LOGIN_DONE = False
_ACCOUNT_LIST: list[dict] = []   # cached account list from /accounts/ endpoint

# ITEM3-RH-AUTH: process-wide RH auth-health state (shared across all RH divisions)
_AUTH_LOCK = Lock()
_auth_down = False
_auth_down_since = None
_auth_last_good = None
_reauth_last_ts = 0.0
_REAUTH_MIN_INTERVAL_S = 300   # GUARD 1: >= a real outage cools before we hit /login again (429 guard)
_REAUTH_TIMEOUT_S = 15         # GUARD 2: fresh-file validate <1s; a stale-file challenge exceeds this
_auth_alert_hook = None        # set by main.py wiring -> data_exec._on_rh_auth_change


class RobinhoodAuthError(Exception):
    """Marker only; snapshot() self-heals and does NOT raise."""

# Robinhood's `instrument` URL on a position can point to non-equity
# instruments — most commonly crypto holdings under
# `/currencies/c-{NNNN}-{HEX}/`. The equity resolver
# `rs.stocks.get_symbol_by_url` can't handle these, so they always
# return empty symbol → noisy WARNING per snapshot poll
# (every ~14s in production = thousands of log lines/day).
#
# This regex distinguishes "known unresolvable" (crypto) from
# "genuinely unexpected" so the former gets DEBUG-logged silently
# while the latter still WARNs. If Robinhood ever adds another
# instrument category we don't recognize, we want the WARN to surface
# the new pattern, not blanket-suppress it.
_KNOWN_NON_EQUITY_INSTRUMENT_RE = re.compile(
    r"/currencies/c-\d+-[0-9a-f]+/?$"
)


class RobinhoodAccountBindError(RuntimeError):
    """Raised when a NUMERIC account_filter cannot be bound (absent from
    robin_stocks discovery AND not directly fetchable). We REFUSE to silently
    fall back to another account — a numeric filter must hit exactly that
    account or fail loud. This is the guard against the agentic cash account
    680725082 silently routing to the main margin account 461391328."""


class RobinhoodOrderError(RuntimeError):
    """Raised when Robinhood does NOT accept an order — the response carries no
    order id (an empty/None body, or an error dict like {'non_field_errors': …}
    / {'detail': …}). The broker MUST surface this as a failure rather than
    synthesize a FillEvent: a fabricated fill makes the engine book a PHANTOM
    position for an order that never placed (e.g. the live HTTP-400 'answer your
    investing-goals questions' compliance reject observed 2026-06-22)."""


class RobinhoodComboPending(RuntimeError):
    """A combo order was ACCEPTED (has an id) but did NOT reach a terminal
    `filled` state inside the poll window — its fate is unconfirmed. Callers MUST
    book NOTHING; the position is not real yet. The next scan reconciles the true
    state from the broker (and the resting day-order expires or the operator
    cancels it). Distinct from RobinhoodOrderError (a hard reject / no-id)."""

    def __init__(self, message: str, *, order_id: str | None = None):
        super().__init__(message)
        self.order_id = order_id


def _days_to_expiry(expiration_date: str) -> int:
    """Calendar days from today to expiration_date ('YYYY-MM-DD')."""
    try:
        exp = date.fromisoformat(expiration_date)
        return max(0, (exp - date.today()).days)
    except (ValueError, TypeError):
        return 0


def _rh_float(v) -> float:
    """Tolerant float() for robin_stocks' string/None numeric fields (e.g.
    cumulative_quantity='0.34710000', average_price=None)."""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _rh_executed_notional(info: dict) -> float | None:
    """RH `executed_notional` is {'amount': '..', 'currency_code': ..} or None."""
    en = (info or {}).get("executed_notional")
    if isinstance(en, dict):
        return _rh_float(en.get("amount"))
    return None


class RobinhoodBroker(Broker):
    name = "robinhood"
    paper = False

    def __init__(
        self,
        username: str,
        password: str,
        mfa_secret: str | None = None,
        account_filter: str | None = None,
    ) -> None:
        self._username = username
        self._password = password
        self._mfa_secret = mfa_secret
        self._account_filter = (account_filter or "").strip().lower() or None
        self._connected = False
        # Resolved after connect(): the specific account_number this instance
        # operates on. Empty string = use robin_stocks default account.
        self._account_number: str = ""
        self._account_type: str = ""        # "individual"/"ira_roth"/"joint"
        self._account_label: str = ""       # human-readable, used in logs
        self._frac_elig_cache: dict[str, bool] = {}   # per-symbol fractional eligibility

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        global _LOGIN_DONE, _ACCOUNT_LIST
        import robin_stocks.robinhood as rs  # type: ignore

        # Only one instance does the actual login; the rest piggyback.
        do_login = False
        with _LOGIN_LOCK:
            if not _LOGIN_DONE:
                do_login = True
                _LOGIN_DONE = True   # set early to prevent double-login on race

        if do_login:
            mfa_code: str | None = None
            if self._mfa_secret:
                try:
                    import pyotp  # type: ignore
                    mfa_code = pyotp.TOTP(self._mfa_secret).now()
                except ImportError:
                    log.warning("pyotp not installed — TOTP MFA skipped; SMS fallback will be used")

            try:
                await asyncio.to_thread(
                    rs.login,
                    self._username,
                    self._password,
                    mfa_code=mfa_code,
                    store_session=True,
                )
            except Exception:
                with _LOGIN_LOCK:
                    _LOGIN_DONE = False
                raise

            # Pull the full account list once, cache for all instances
            _ACCOUNT_LIST = await self._fetch_accounts()
            log.info(
                "RobinhoodBroker logged in (user=%s) — %d account(s) discovered",
                self._username, len(_ACCOUNT_LIST),
            )
            # Log every discovered account so divisions.yaml filters can be
            # tuned to match the actual labels Robinhood returns. (Some
            # account-type strings are non-obvious — e.g., a Roth IRA might
            # come back as "roth", "rothira", or even just "ira".)
            for i, acc in enumerate(_ACCOUNT_LIST):
                num = acc.get("account_number") or "?"
                acct_type = (
                    acc.get("brokerage_account_type")
                    or acc.get("account_type")
                    or acc.get("type")
                    or "?"
                )
                log.info(
                    "  RH account #%d: number=%s type=%r",
                    i + 1, num, acct_type,
                )

        # Hard-bind a numeric account_filter that discovery omits (e.g. the
        # agentic cash account 680725082) via a direct fetch — or fail loud.
        await self._ensure_numeric_filter_resolvable()
        # Resolve this instance's filter → account_number
        self._resolve_account_filter()
        self._connected = True
        log.info(
            "RobinhoodBroker bound: filter=%r → account=%s (%s)",
            self._account_filter, self._account_number or "default",
            self._account_label or "default",
        )

    # ITEM3-REAUTH: active 401 sentinel + guarded in-process re-login + latch -----------
    async def _auth_is_401(self) -> bool:
        """ACTIVE sentinel — robin_stocks swallows a 401 (returns None/0, no raise); read the
        status ourselves via a raw request (mirrors robin_stocks' own pickle validation)."""
        import robin_stocks.robinhood as rs  # type: ignore
        try:
            res = await asyncio.to_thread(
                rs.helper.request_get,
                rs.urls.positions_url(self._account_number or None),
                "pagination", {"nonzero": "true"}, False,
            )
        except Exception:
            return False
        return getattr(res, "status_code", None) == 401

    def _login_input_neutered(self, mfa_code) -> None:
        """rs.login with stdin -> /dev/null so an sms/email challenge input() raises EOFError
        immediately (GUARD 2 belt-and-suspenders; engine stdin is already systemd-null)."""
        import robin_stocks.robinhood as rs  # type: ignore
        old = sys.stdin
        try:
            sys.stdin = open(os.devnull)
            rs.login(self._username, self._password, mfa_code=mfa_code, store_session=True)
        finally:
            try:
                sys.stdin.close()
            except Exception:
                pass
            sys.stdin = old

    async def _attempt_reauth(self, *, force: bool = False, timeout: float = _REAUTH_TIMEOUT_S) -> bool:
        """ONE guarded in-process re-login off the pickle file. GUARD 1 backoff (auto path) +
        GUARD 2 timeout. force=True (ITEM 2 button) skips backoff, uses a longer timeout."""
        global _LOGIN_DONE, _reauth_last_ts
        now = time.monotonic()
        if not force:
            with _AUTH_LOCK:
                if now - _reauth_last_ts < _REAUTH_MIN_INTERVAL_S:
                    return False   # GUARD 1: don't hammer /login (429 hazard)
                _reauth_last_ts = now
        mfa_code = None
        if self._mfa_secret:
            try:
                import pyotp  # type: ignore
                mfa_code = pyotp.TOTP(self._mfa_secret).now()
            except ImportError:
                pass
        with _LOGIN_LOCK:
            _LOGIN_DONE = False
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._login_input_neutered, mfa_code),
                timeout=timeout,   # GUARD 2
            )
        except Exception as e:  # TimeoutError / EOFError / login error
            log.warning("RH in-process reauth did not complete (%s: %s) — file likely stale, escalating",
                        type(e).__name__, e)
            return False
        if await self._auth_is_401():
            return False
        with _LOGIN_LOCK:
            _LOGIN_DONE = True
        return True

    def _note_auth_state(self, *, down: bool, reason: str) -> None:
        """Update the shared latch; fire the alert hook ONLY on a down/recovered TRANSITION."""
        global _auth_down, _auth_down_since, _auth_last_good
        _ts = datetime.now(timezone.utc).isoformat()
        transition = None
        with _AUTH_LOCK:
            was = _auth_down
            if down and not was:
                _auth_down = True; _auth_down_since = _ts; transition = "down"
            elif (not down) and was:
                _auth_down = False; _auth_last_good = _ts; transition = "recovered"
            elif not down:
                _auth_last_good = _ts
        if transition == "down":
            log.error("rh_auth_failed: Robinhood session DOWN (reason=%s) — NO entries and NO exits on live positions, all RH accounts", reason)
        elif transition == "recovered":
            log.info("rh_auth_recovered: Robinhood session restored (reason=%s)", reason)
        if transition and _auth_alert_hook is not None:
            try:
                asyncio.get_running_loop().create_task(
                    _auth_alert_hook(down, {"reason": reason, "since": _auth_down_since, "last_good": _auth_last_good}))
            except RuntimeError:
                pass

    async def disconnect(self) -> None:
        # Don't actually log out unless this is the last instance — robin_stocks
        # is a singleton, logout invalidates everyone. Just mark this instance
        # disconnected. Process exit will clean up.
        self._connected = False

    @staticmethod
    async def _fetch_accounts() -> list[dict]:
        """Fetch all accounts under this login.

        Uses the /accounts/ endpoint via robin_stocks' internal helpers.
        Returns a list of dicts; keys vary by Robinhood API version, so
        we look up several common ones in `_resolve_account_filter`.
        """
        import robin_stocks.robinhood as rs  # type: ignore
        try:
            data = await asyncio.to_thread(
                rs.helper.request_get,
                "https://api.robinhood.com/accounts/?default_to_all_accounts=true",
                "pagination",
            )
            # request_get with "pagination" returns the unwrapped list
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and "results" in data:
                return list(data["results"] or [])
        except Exception as e:
            log.warning("RobinhoodBroker._fetch_accounts failed: %s", e)
        return []

    @staticmethod
    async def _fetch_account_by_number(num: str) -> dict | None:
        """Direct GET /accounts/{num}/ — for accounts robin_stocks' discovery
        list OMITS (cash accounts like the agentic 680725082). Returns the
        account dict, or None on 404 / error. No fallback."""
        import robin_stocks.robinhood as rs  # type: ignore
        try:
            data = await asyncio.to_thread(
                rs.helper.request_get,
                f"https://api.robinhood.com/accounts/{num}/",
            )
            if isinstance(data, dict) and data.get("account_number"):
                return data
        except Exception as e:
            log.warning(
                "RobinhoodBroker._fetch_account_by_number(%s) failed: %s", num, e,
            )
        return None

    async def _ensure_numeric_filter_resolvable(self) -> None:
        """If account_filter is a NUMERIC account absent from the discovery
        list, bind it via a direct /accounts/{n}/ fetch — and HARD-FAIL if that
        returns nothing. Never let _resolve_account_filter fall back to the main
        account for a numeric filter (the 680725082 silent-misroute bug)."""
        global _ACCOUNT_LIST
        f = self._account_filter or ""
        if not f.isdigit():
            return  # non-numeric (type-keyword) filter — unchanged path
        if any(str(a.get("account_number") or "") == f for a in (_ACCOUNT_LIST or [])):
            return  # already discoverable
        acc = await self._fetch_account_by_number(f)
        if acc is None:
            raise RobinhoodAccountBindError(
                f"account_filter={f!r}: absent from discovery AND direct "
                f"/accounts/{f}/ returned nothing — refusing to fall back to "
                f"another account"
            )
        _ACCOUNT_LIST = list(_ACCOUNT_LIST or []) + [acc]
        log.info("RobinhoodBroker: bound numeric account %s via direct fetch", f)

    def _resolve_account_filter(self) -> None:
        """Pick the account matching `self._account_filter`."""
        global _ACCOUNT_LIST
        if not _ACCOUNT_LIST:
            self._account_number = ""
            self._account_label = "default"
            return

        f = self._account_filter or ""
        # Helper to read a name-like field across API variants
        def _label_of(acc: dict) -> str:
            t = (
                acc.get("brokerage_account_type")
                or acc.get("account_type")
                or acc.get("type")
                or ""
            ).lower()
            return t

        # Type-match predicates — permissive substrings so we catch all the
        # variants Robinhood returns ("roth_ira", "ira_roth", "rothira",
        # "individual_roth", just "roth" with no "ira", etc.).
        # Note: "individual" must NOT swallow joint/IRA accounts even though
        # those are technically "individual" in some sense — we exclude them.
        def _matches(filter_str: str, label: str) -> bool:
            if not label:
                return False
            if filter_str in label:
                return True
            if filter_str == "individual":
                # Match plain individual / margin / cash, but NOT joint or any IRA
                if "joint" in label or "ira" in label or "roth" in label:
                    return False
                return ("individual" in label) or label in ("margin", "cash")
            if filter_str == "ira_roth":
                # Match Roth IRA only — exclude traditional/SEP variants.
                return "roth" in label
            if filter_str in ("ira_traditional", "ira_trad", "traditional_ira"):
                # Match Traditional IRA only — exclude Roth.
                return ("traditional" in label or "trad" in label) and "roth" not in label
            if filter_str == "ira":
                return "ira" in label
            if filter_str == "joint":
                return "joint" in label
            return False

        match: dict | None = None
        if not f:
            match = _ACCOUNT_LIST[0]
        else:
            # 1. Exact / substring match on account_number
            for acc in _ACCOUNT_LIST:
                num = str(acc.get("account_number") or "")
                if num and (num == f or f in num.lower()):
                    match = acc
                    break
            # 2. Type-keyword match
            if match is None:
                for acc in _ACCOUNT_LIST:
                    if _matches(f, _label_of(acc)):
                        match = acc
                        break

        if match is None:
            if f.isdigit():
                # A numeric account that didn't resolve must NEVER silently
                # become the main account. _ensure_numeric_filter_resolvable
                # should have bound or raised already — defense-in-depth.
                raise RobinhoodAccountBindError(
                    f"numeric account_filter={self._account_filter!r} unresolved "
                    f"— refusing to fall back to "
                    f"{_ACCOUNT_LIST[0].get('account_number')!r}"
                )
            log.warning(
                "RobinhoodBroker: no account matched filter=%r; falling back to default",
                self._account_filter,
            )
            match = _ACCOUNT_LIST[0]

        self._account_number = str(match.get("account_number") or "")
        self._account_type = _label_of(match)
        self._account_label = self._account_type or self._account_number or "default"

    # ------------------------------------------------------------------
    # Account data
    # ------------------------------------------------------------------

    async def snapshot(self) -> AccountSnapshot:
        self._require_connected()
        import robin_stocks.robinhood as rs  # type: ignore

        # Per-account portfolio profile. robin_stocks' load_portfolio_profile
        # accepts account_number; passing "" uses the default account.
        profile = await asyncio.to_thread(
            rs.profiles.load_portfolio_profile,
            self._account_number or None,
        )
        profile = profile or {}
        # ITEM3-SNAPSHOT: empty profile on a real RH account = the 401 signature. Confirm
        # actively, then try ONE guarded in-process reload (silent recover if the file is fresh).
        if not profile and await self._auth_is_401():
            import robin_stocks.robinhood as rs  # type: ignore
            if await self._attempt_reauth():
                profile = await asyncio.to_thread(
                    rs.profiles.load_portfolio_profile, self._account_number or None) or {}
                self._note_auth_state(down=False, reason="in_process_reauth_ok")
            else:
                self._note_auth_state(down=True, reason="reauth_failed_or_stale_file")
                # fall through: empty profile -> equity 0 / positions [] (existing), NO raise
        elif profile:
            self._note_auth_state(down=False, reason="snapshot_ok")
        equity = float(profile.get("equity") or profile.get("extended_hours_equity") or 0)
        buying_power = float(
            profile.get("margin_buying_power")
            or profile.get("buying_power")
            or profile.get("excess_margin")
            or profile.get("withdrawable_amount")
            or 0
        )

        positions: list[Position] = []
        account_label = f"{self._username}#{self._account_label}" if self._account_label else self._username

        # Stock positions — robin_stocks' build_holdings does NOT accept an
        # account_number arg (it always uses the default account). Use
        # get_open_stock_positions(account_number) instead, which returns
        # raw position dicts scoped to the requested account, then resolve
        # symbols from each instrument URL.
        raw_positions = await asyncio.to_thread(
            rs.account.get_open_stock_positions,
            self._account_number or None,
        ) or []

        for pos in raw_positions:
            qty = float(pos.get("quantity") or 0)
            if qty == 0:
                continue
            instrument_url = pos.get("instrument") or ""
            symbol = ""
            if instrument_url:
                try:
                    symbol = await asyncio.to_thread(
                        rs.stocks.get_symbol_by_url, instrument_url
                    ) or ""
                except Exception as e:
                    log.debug("symbol lookup failed for %s: %s", instrument_url, e)
            if not symbol:
                # Symbol resolution failed entirely. Skip rather than
                # synthesize a bogus identifier (downstream quote calls
                # would 404 on it). Demote to DEBUG for KNOWN
                # non-equity instruments (crypto chain IDs) so the
                # journal doesn't spam; keep WARNING for anything we
                # haven't seen before, so a new instrument category
                # surfaces visibly.
                if _KNOWN_NON_EQUITY_INSTRUMENT_RE.search(instrument_url):
                    log.debug(
                        "RobinhoodBroker: skipping non-equity position "
                        "(crypto chain id, qty=%s) — equities resolver "
                        "can't map this URL", qty,
                    )
                else:
                    log.warning(
                        "RobinhoodBroker: could not resolve symbol for %s "
                        "(qty=%s); position skipped from snapshot",
                        instrument_url[-20:] or "?", qty,
                    )
                continue
            positions.append(Position(
                account=account_label,
                symbol=symbol.upper(),
                qty=qty,
                avg_price=float(pos.get("average_buy_price") or 0),
                opened_ts=pos.get("created_at") or "",
            ))

        # Option positions — use detail endpoint for accurate strike/type info.
        detailed_opts = await self.get_option_positions_detail()
        for op in detailed_opts:
            qty = op.get("quantity") or 0
            if qty == 0:
                continue
            otype = "C" if op.get("option_type") == "call" else "P"
            strike = op.get("strike_price") or 0
            sym = (
                f"{op.get('chain_symbol', '???')} "
                f"{op.get('expiration_date', '????')} "
                f"{otype} {float(strike):.2f}"
            )
            positions.append(Position(
                account=account_label,
                symbol=sym,
                qty=float(qty),
                avg_price=float(op.get("avg_price") or 0),
                opened_ts="",
            ))

        # Crypto positions — Robinhood holds crypto in a single account-wide
        # wallet (no per-brokerage filter), so we only emit it from the
        # Individual instance. IRAs/Joint don't hold crypto on Robinhood;
        # if we emitted from all three we'd triple-count the same coins.
        if self._account_type == "individual":
            try:
                crypto_raw = await asyncio.to_thread(
                    rs.crypto.get_crypto_positions,
                ) or []
            except Exception as e:
                log.warning("RobinhoodBroker: get_crypto_positions failed: %s", e)
                crypto_raw = []
            for cpos in crypto_raw:
                try:
                    qty = float(cpos.get("quantity") or 0)
                except (TypeError, ValueError):
                    qty = 0.0
                if qty <= 0:
                    continue
                # `currency` is normally a dict {code, name, id, ...}; handle
                # the rare case where Robinhood returns it as a bare string.
                cur = cpos.get("currency")
                if isinstance(cur, dict):
                    code = (cur.get("code") or "").upper()
                elif isinstance(cur, str):
                    code = cur.upper()
                else:
                    code = ""
                if not code:
                    log.debug(
                        "RobinhoodBroker: skipping crypto position with no resolvable code: %s",
                        cpos.get("id") or "?",
                    )
                    continue
                try:
                    cost_basis = float(cpos.get("cost_basis") or 0)
                except (TypeError, ValueError):
                    cost_basis = 0.0
                avg_price = (cost_basis / qty) if qty > 0 else 0.0
                positions.append(Position(
                    account=account_label,
                    symbol=f"{code}/USD",  # match Coinbase unified format
                    qty=qty,
                    avg_price=avg_price,
                    opened_ts=cpos.get("created_at") or "",
                    extra={
                        "asset": code,
                        "venue": "robinhood",
                        "asset_type": "crypto",
                    },
                ))

        return AccountSnapshot(
            account=account_label,
            equity=equity,
            buying_power=buying_power,
            cash=buying_power,
            positions=positions,
        )

    async def quote(self, symbol: str) -> float:
        """Last trade price for a stock or crypto symbol. Returns 0.0 for options.

        robin_stocks deprecated `"last_trade_price"` as a `priceType` value
        (now only accepts "ask_price" or "bid_price"); calling without a
        priceType returns the standard last-trade price with no warning.

        Skips obviously-bogus symbols (e.g. UUID fragments left over from
        instrument-URL fallbacks) so we don't 404 on Robinhood's quotes API.

        Crypto symbols use the unified "{CODE}/USD" form (matching Coinbase)
        and route to rs.crypto.get_crypto_quote.
        """
        if " " in symbol or "#" in symbol:
            return 0.0
        self._require_connected()
        import robin_stocks.robinhood as rs  # type: ignore

        # Crypto: unified "BTC/USD" form. Route to the crypto quote endpoint.
        if "/" in symbol:
            base, _, _quote_ccy = symbol.partition("/")
            base = base.strip().upper()
            if not base or not base.isalnum():
                return 0.0
            try:
                q = await asyncio.to_thread(rs.crypto.get_crypto_quote, base)
            except Exception as e:
                log.debug("RobinhoodBroker.quote: crypto lookup failed for %s: %s", symbol, e)
                return 0.0
            if isinstance(q, dict):
                px = q.get("mark_price") or q.get("last_trade_price") or q.get("ask_price")
                try:
                    return float(px) if px else 0.0
                except (TypeError, ValueError):
                    return 0.0
            return 0.0

        # Skip non-ticker-shaped strings (e.g. "BAAF764E" UUID fragments
        # from get_symbol_by_url failures). Real tickers are 1-5 letters.
        if not symbol or not symbol.isalpha() or len(symbol) > 5:
            return 0.0
        prices = await asyncio.to_thread(rs.stocks.get_latest_price, [symbol])
        if prices and prices[0]:
            return float(prices[0])
        return 0.0

    # ------------------------------------------------------------------
    # Option-specific data — used by PMCCAgent
    # ------------------------------------------------------------------

    async def get_option_positions_detail(self) -> list[dict]:
        """Open option positions enriched with instrument details + greeks.

        Each item: chain_symbol, option_type, expiration_date, strike_price,
        quantity (signed: + = long, - = short), avg_price, delta, mark_price,
        dte, option_id.
        """
        self._require_connected()
        import robin_stocks.robinhood as rs  # type: ignore

        # Per-account option positions
        raw = await asyncio.to_thread(
            rs.options.get_open_option_positions,
            self._account_number or None,
        ) or []
        detailed: list[dict] = []

        for op in raw:
            qty = float(op.get("quantity") or 0)
            if qty == 0:
                continue
            signed_qty = qty if op.get("type") == "long" else -qty
            option_id = op.get("option_id") or ""

            instrument: dict = {}
            market_data: dict = {}
            if option_id:
                try:
                    instrument = await asyncio.to_thread(
                        rs.options.get_option_instrument_data_by_id, option_id
                    ) or {}
                    raw_market = await asyncio.to_thread(
                        rs.options.get_option_market_data_by_id, option_id
                    )
                    if isinstance(raw_market, list) and raw_market:
                        market_data = raw_market[0] or {}
                    elif isinstance(raw_market, dict):
                        market_data = raw_market
                except Exception as e:
                    log.warning("Failed to fetch option detail for id=%s: %s", option_id, e)

            expiry = instrument.get("expiration_date") or op.get("expiration_date") or ""
            strike = float(instrument.get("strike_price") or op.get("strike_price") or 0)
            otype = (instrument.get("type") or op.get("option_type") or "call").lower()
            delta_raw = market_data.get("delta")
            delta = float(delta_raw) if delta_raw is not None else None
            mark_raw = (
                market_data.get("adjusted_mark_price") or market_data.get("mark_price")
            )
            mark = float(mark_raw) if mark_raw is not None else None

            detailed.append({
                "chain_symbol": (
                    op.get("chain_symbol") or instrument.get("chain_symbol") or ""
                ).upper(),
                "option_type": otype,
                "expiration_date": expiry,
                "strike_price": strike,
                "quantity": signed_qty,
                "avg_price": float(op.get("average_price") or 0),
                "delta": delta,
                "mark_price": mark,
                "dte": _days_to_expiry(expiry) if expiry else None,
                "option_id": option_id,
            })

        return detailed

    async def get_expiration_dates(self, symbol: str) -> list[str]:
        """Available expiration dates for `symbol`, sorted ascending."""
        self._require_connected()
        import robin_stocks.robinhood as rs  # type: ignore
        chain = await asyncio.to_thread(rs.options.get_chains, symbol) or {}
        return sorted(chain.get("expiration_dates") or [])

    async def get_calls_for_expiry(self, symbol: str, expiry: str) -> list[dict]:
        """All call options for `symbol` expiring on `expiry`. See
        `_options_for_expiry` for the row shape."""
        return await self._options_for_expiry(symbol, expiry, "call")

    async def get_puts_for_expiry(self, symbol: str, expiry: str) -> list[dict]:
        """All put options for `symbol` expiring on `expiry`. See
        `_options_for_expiry` for the row shape. Added for the
        iron-condor strategy's put-side chain reads."""
        return await self._options_for_expiry(symbol, expiry, "put")

    async def _options_for_expiry(
        self, symbol: str, expiry: str, option_type: str
    ) -> list[dict]:
        """Shared chain-read implementation for calls and puts. Each item:
          option_id, expiration_date, strike_price, delta, mark_price,
          bid, ask, bid_size, ask_size, dte,
          open_interest, volume, implied_volatility, theta, gamma, vega.
        """
        self._require_connected()
        import robin_stocks.robinhood as rs  # type: ignore

        raw = await asyncio.to_thread(
            rs.options.find_options_by_expiration, [symbol], expiry, option_type,
        ) or []
        dte = _days_to_expiry(expiry)

        def _f(v) -> float | None:
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        def _i(v) -> int:
            try:
                return int(float(v))
            except (TypeError, ValueError):
                return 0

        out = []
        for opt in raw:
            mark_raw = opt.get("adjusted_mark_price") or opt.get("mark_price")
            out.append({
                "option_id": opt.get("id") or opt.get("option_id") or "",
                "expiration_date": expiry,
                "strike_price": float(opt.get("strike_price") or 0),
                "delta": _f(opt.get("delta")),
                "mark_price": _f(mark_raw),
                "bid": float(opt.get("bid_price") or 0),
                "ask": float(opt.get("ask_price") or 0),
                "bid_size": _i(opt.get("bid_size")),
                "ask_size": _i(opt.get("ask_size")),
                "dte": dte,
                # Liquidity fields (used by PMCCAgent._passes_liquidity)
                "open_interest": _i(opt.get("open_interest")),
                "volume": _i(opt.get("volume")),
                "implied_volatility": _f(opt.get("implied_volatility")),
                # Other Greeks (handy for the LLM context, optional)
                "theta": _f(opt.get("theta")),
                "gamma": _f(opt.get("gamma")),
                "vega": _f(opt.get("vega")),
            })
        return out

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    async def place_order(self, order: ProposedOrder) -> FillEvent:
        self._require_connected()
        if (order.extra or {}).get("is_option"):
            return await self._place_option_order(order)
        # ISOLATED fractional/notional path (robinhood_pead). Only orders that
        # explicitly opt in (order.fractional) divert here; the whole-share market /
        # limit / option / multi-leg paths below are reached unchanged for everything
        # else (PMCC / robinhood_joint / IC never set order.fractional).
        if getattr(order, "fractional", False):
            return await self._place_fractional_stock_order(order)
        return await self._place_stock_order(order)

    # ── shared fill construction: fail loud + carry RH's real id + account ──
    @staticmethod
    def _account_number_from(result: dict) -> str | None:
        """The account number the order actually hit, parsed from RH's account
        URL (…/accounts/<num>/). The routing-safety identity (Bug-2 fix)."""
        acct = str((result or {}).get("account") or "").rstrip("/")
        num = acct.rsplit("/", 1)[-1]
        return num or None

    def _fill_or_raise(self, result, order: ProposedOrder, price: float) -> FillEvent:
        """Build a FillEvent from a robin_stocks order response — or RAISE
        RobinhoodOrderError if the response is not a real accepted order.

        A genuinely-placed order carries an 'id'. No id (empty/None, or an error
        dict like {'non_field_errors': …}/{'detail': …}) means it did NOT place;
        we raise with RH's verbatim reason instead of synthesizing a fill
        (Bug-1 fix — a fake fill would book a phantom position). On success the
        FillEvent carries RH's real order id + the account it hit (Bug-2 fix)."""
        result = result or {}
        rh_id = result.get("id")
        if not rh_id:
            reason = (result.get("non_field_errors") or result.get("detail")
                      or result or "empty response")
            raise RobinhoodOrderError(
                f"Robinhood did not accept {order.side} {order.symbol} "
                f"x{int(order.qty)}: {reason}"
            )
        return FillEvent(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            qty=float(int(order.qty)),
            price=float(price),
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            venue="robinhood",
            broker_order_id=str(rh_id),
            account=self._account_number_from(result),
        )

    async def _place_stock_order(self, order: ProposedOrder) -> FillEvent:
        import robin_stocks.robinhood as rs  # type: ignore
        qty = int(order.qty)
        acct = self._account_number or None
        if order.order_type == "market":
            fn = (
                rs.orders.order_buy_market
                if order.side == "buy"
                else rs.orders.order_sell_market
            )
            # timeInForce='gfd' (day) — a true market order CANNOT be GTC: RH
            # rejects market sells placed GTC ("Invalid Good Til Canceled order").
            # (robin_stocks converts a regular-hours market BUY to a collared
            # limit so GTC slips through there, but a market SELL stays a real
            # market order and is rejected. Use GFD for both. Observed live
            # 2026-06-23.)
            result = await asyncio.to_thread(
                fn, order.symbol, qty, account_number=acct, timeInForce="gfd",
            )
        else:
            fn = (
                rs.orders.order_buy_limit
                if order.side == "buy"
                else rs.orders.order_sell_limit
            )
            result = await asyncio.to_thread(
                fn, order.symbol, qty, order.limit_price, account_number=acct,
            )

        price = float((result or {}).get("average_price") or order.limit_price or 0)
        return self._fill_or_raise(result, order, price)

    # ── ISOLATED fractional / notional path (robinhood_pead) ───────────────────
    # The whole-share market/limit + option + multi-leg paths above are UNCHANGED.
    # BUY = by dollars (order_buy_fractional_by_price; robin_stocks converts $→shares
    # CLIENT-SIDE via the ask, enforces a $1 minimum). SELL = by realized fractional
    # quantity (order_sell_fractional_by_quantity). Both are market / regular-hours /
    # gfd. We POLL the order to its terminal state and record the REALIZED
    # cumulative_quantity + average fill price — never the (client-computed) request
    # qty. RAISES on a non-accepted order (Bug-1 discipline) and on an unconfirmed
    # fill (cancel first, then raise — no phantom record).
    async def _poll_fractional_fill(self, rh_id, *, timeout_s: float = 90.0,
                                    interval_s: float = 1.5):
        """Poll get_stock_order_info(rh_id) until terminal. Returns
        (realized_qty, avg_fill_price, executed_notional) on any non-zero fill, else
        None (rejected/cancelled/failed with nothing filled, or timeout). A partial
        that filled before termination is returned as the realized partial (decision
        #2). On timeout the order is CANCELLED first (stop further fill), then the
        final realized qty is read."""
        import robin_stocks.robinhood as rs  # type: ignore
        iters = max(1, int(timeout_s / interval_s))
        last: dict = {}
        for _ in range(iters):
            try:
                info = await asyncio.to_thread(rs.orders.get_stock_order_info, rh_id) or {}
            except Exception:  # noqa: BLE001
                info = {}
            last = info or last
            state = str(info.get("state") or "").lower()
            cum = _rh_float(info.get("cumulative_quantity"))
            if state == "filled" and cum > 0:
                return cum, _rh_float(info.get("average_price")), _rh_executed_notional(info)
            if state in ("cancelled", "canceled", "rejected", "failed"):
                if cum > 0:   # partial filled before terminating → accept the realized part
                    return cum, _rh_float(info.get("average_price")), _rh_executed_notional(info)
                return None
            await asyncio.sleep(interval_s)
        # timeout: cancel to stop any further fill, then record whatever actually filled
        try:
            await asyncio.to_thread(rs.orders.cancel_stock_order, rh_id)
        except Exception:  # noqa: BLE001
            pass
        try:
            info = await asyncio.to_thread(rs.orders.get_stock_order_info, rh_id) or last
        except Exception:  # noqa: BLE001
            info = last
        cum = _rh_float(info.get("cumulative_quantity"))
        if cum > 0:
            return cum, _rh_float(info.get("average_price")), _rh_executed_notional(info)
        return None

    async def _place_fractional_stock_order(self, order: ProposedOrder) -> FillEvent:
        import robin_stocks.robinhood as rs  # type: ignore
        acct = self._account_number or None
        if order.side == "buy":
            notional = float(order.notional_usd or 0.0)
            if notional < 1.0:
                raise RobinhoodOrderError(
                    f"fractional buy {order.symbol}: notional ${notional:.2f} < $1 RH minimum")
            result = await asyncio.to_thread(
                rs.orders.order_buy_fractional_by_price, order.symbol, notional,
                account_number=acct, timeInForce="gfd",
            )
        else:
            qty = abs(float(order.qty))
            if qty <= 0:
                raise RobinhoodOrderError(f"fractional sell {order.symbol}: qty {qty} <= 0")
            result = await asyncio.to_thread(
                rs.orders.order_sell_fractional_by_quantity, order.symbol, qty,
                account_number=acct, timeInForce="gfd",
            )
        # Bug-1: a real accepted order carries an id; None/empty/error dict → raise
        # (order_buy_fractional_by_price returns None below $1 or on a price-fetch fail).
        result = result or {}
        rh_id = result.get("id")
        if not rh_id:
            reason = (result.get("non_field_errors") or result.get("detail")
                      or result or "empty/None response (below $1, price-fetch fail, or rejected)")
            raise RobinhoodOrderError(
                f"Robinhood did not accept fractional {order.side} {order.symbol}: {reason}")
        realized = await self._poll_fractional_fill(str(rh_id))
        if realized is None:
            raise RobinhoodOrderError(
                f"fractional {order.side} {order.symbol} not confirmed filled (cancelled) id={rh_id}")
        filled_qty, avg_price, exec_notional = realized
        return FillEvent(
            order_id=order.id, symbol=order.symbol, side=order.side,
            qty=float(filled_qty), price=float(avg_price),
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            venue="robinhood", broker_order_id=str(rh_id),
            account=self._account_number_from(result), executed_notional=exec_notional,
        )

    # ── Flag-2: deferred-fill reconcile (robinhood_pead) ───────────────────────
    # PEAD scans PRE-OPEN (8:30-9:25 ET). A synchronous poll-to-fill would CANCEL a
    # GFD order that queues to the 9:30 open before it ever fills, so the production
    # entry must DEFER: place WITHOUT polling, store the order id, and reconcile the
    # realized fill at/after the open. These three methods are the broker side of
    # that flow — ADDITIVE, used only by pead_strategy's deferred path; the polling
    # `_place_fractional_stock_order` above + whole-share/limit/option are UNTOUCHED.
    async def place_fractional_pending(self, order: ProposedOrder) -> str:
        """Submit a GFD fractional BUY and return RH's order id WITHOUT polling for
        the fill (the reconcile loop confirms it at the open). RAISES on a non-
        accepted order (Bug-1: no id → no phantom). Buy-only — the deferred path is
        for entries; exits use the synchronous fractional sell."""
        import robin_stocks.robinhood as rs  # type: ignore
        if order.side != "buy":
            raise RobinhoodOrderError(
                f"place_fractional_pending is buy-only (got {order.side} {order.symbol})")
        notional = float(order.notional_usd or 0.0)
        if notional < 1.0:
            raise RobinhoodOrderError(
                f"fractional buy {order.symbol}: notional ${notional:.2f} < $1 RH minimum")
        acct = self._account_number or None
        result = await asyncio.to_thread(
            rs.orders.order_buy_fractional_by_price, order.symbol, notional,
            account_number=acct, timeInForce="gfd",
        )
        result = result or {}
        rh_id = result.get("id")
        if not rh_id:
            reason = (result.get("non_field_errors") or result.get("detail")
                      or result or "empty/None response (below $1, price-fetch fail, or rejected)")
            raise RobinhoodOrderError(
                f"Robinhood did not accept fractional buy {order.symbol}: {reason}")
        return str(rh_id)

    async def read_fractional_order(self, rh_id) -> dict:
        """Single (non-blocking) read of a fractional order's state for the reconcile
        loop — NEVER cancels. Returns the normalized terminal-truth fields
        {state, filled_qty, avg_price, executed_notional, account}. `filled_qty` is
        RH's realized `cumulative_quantity`; `account` is parsed from the order's
        account URL for the routing-safety record."""
        import robin_stocks.robinhood as rs  # type: ignore
        info = await asyncio.to_thread(rs.orders.get_stock_order_info, rh_id) or {}
        return {
            "state": str(info.get("state") or "").lower(),
            "filled_qty": _rh_float(info.get("cumulative_quantity")),
            "avg_price": _rh_float(info.get("average_price")),
            "executed_notional": _rh_executed_notional(info),
            "account": self._account_number_from(info),
        }

    async def cancel_fractional_order(self, rh_id) -> bool:
        """Cancel a still-resting fractional order (the collar-miss branch — a GFD
        order rests ALL DAY, so an un-cancelled miss could fill UNWATCHED later =
        phantom position). Best-effort: returns False on a cancel hiccup rather than
        raising (the reconcile loop logs + clears the pending row regardless)."""
        import robin_stocks.robinhood as rs  # type: ignore
        try:
            await asyncio.to_thread(rs.orders.cancel_stock_order, rh_id)
            return True
        except Exception as e:  # noqa: BLE001
            log.warning("cancel_fractional_order(%s) failed: %s", rh_id, e)
            return False

    async def fractional_eligible(self, symbol: str) -> bool:
        """RH per-symbol fractional eligibility (instrument.fractional_tradability ==
        'tradable'), cached per-process. Fail-open on a lookup hiccup — the order
        placement still surfaces a real reject (Bug-1)."""
        sym = (symbol or "").upper()
        if sym in self._frac_elig_cache:
            return self._frac_elig_cache[sym]
        import robin_stocks.robinhood as rs  # type: ignore
        ok = True
        try:
            inst = await asyncio.to_thread(rs.stocks.get_instruments_by_symbols, sym) or []
            ft = inst[0].get("fractional_tradability") if inst else None
            ok = (ft == "tradable")
        except Exception as e:  # noqa: BLE001
            log.debug("RobinhoodBroker.fractional_eligible(%s) failed: %s — fail-open", sym, e)
            ok = True
        self._frac_elig_cache[sym] = ok
        return ok

    async def _place_option_order(self, order: ProposedOrder) -> FillEvent:
        import robin_stocks.robinhood as rs  # type: ignore
        extra = order.extra or {}
        if extra.get("is_multi_leg"):
            # Programming guard: combo legs must NEVER be placed one at a
            # time. The iron-condor strategy submits 4 ProposedOrders
            # sharing a combo_id; data_exec.place_combo() aggregates and
            # calls broker.place_multi_leg(orders). If a leg ever reaches
            # this single-leg path, it would partially open the combo and
            # leave naked exposure — fail fast instead.
            raise ValueError(
                f"order {order.id} is a multi-leg combo leg "
                f"(combo_id={extra.get('combo_id')!r}); use "
                "broker.place_multi_leg(orders) — never _place_option_order "
                "on a single leg."
            )
        underlying = extra.get("underlying", order.symbol)
        expiry = extra.get("expiration", "")
        strike = float(extra.get("strike") or 0)
        otype = extra.get("option_type", "call")
        position_effect = extra.get("position_effect", "open")
        qty = int(order.qty)
        price = float(order.limit_price or 0)
        credit_debit = "debit" if order.side == "buy" else "credit"

        acct = self._account_number or None
        if order.side == "buy":
            result = await asyncio.to_thread(
                rs.orders.order_buy_option_limit,
                position_effect, credit_debit, price,
                underlying, qty, expiry, strike, otype,
                account_number=acct,
            )
        else:
            result = await asyncio.to_thread(
                rs.orders.order_sell_option_limit,
                position_effect, credit_debit, price,
                underlying, qty, expiry, strike, otype,
                account_number=acct,
            )

        fill_price = float(
            (result or {}).get("processed_premium")
            or (result or {}).get("price")
            or price
        )
        return self._fill_or_raise(result, order, fill_price)

    async def cancel_order(self, order_id: str) -> bool:
        self._require_connected()
        import robin_stocks.robinhood as rs  # type: ignore
        result = await asyncio.to_thread(rs.orders.cancel_option_order, order_id)
        return bool(result)

    # ------------------------------------------------------------------
    # Multi-leg combo orders (iron condor, vertical spreads, etc.)
    # ------------------------------------------------------------------

    async def place_multi_leg(
        self, orders: list[ProposedOrder], *, ref_id: str | None = None,
    ) -> list[FillEvent]:
        """Submit a multi-leg option combo via robin_stocks.order_option_spread.

        All `orders` must share `extra["combo_id"]`, `combo_direction`
        ("credit" | "debit"), `net_limit_price`, `underlying`, and `qty`.
        Each leg supplies its own `expiration`, `strike`, `option_type`,
        `position_effect` ("open" | "close"), `side` ("buy" | "sell"),
        and optional `ratio_quantity` (defaults to 1).

        Robinhood's combo engine fills all legs atomically (single
        ref_id, single POST) or rejects the whole order — no naked-leg
        execution window. Returns one FillEvent per input order; per-leg
        fill prices are best-effort (combo-level net is authoritative
        for P&L).
        """
        self._require_connected()
        if not orders:
            return []

        combo = validate_combo_cohesion(orders)

        # Build the spread[] list in robin_stocks's expected shape.
        spread: list[dict] = []
        for o in orders:
            ex = o.extra or {}
            for required in ("expiration", "strike", "option_type", "position_effect"):
                if required not in ex:
                    raise ValueError(
                        f"leg missing required extra key {required!r} "
                        f"in combo {combo.combo_id!r}"
                    )
            spread.append({
                "expirationDate": ex["expiration"],
                "strike": float(ex["strike"]),
                "optionType": ex["option_type"],
                "effect": ex["position_effect"],
                "action": o.side,                                  # "buy" | "sell"
                "ratio_quantity": int(ex.get("ratio_quantity", 1)),
            })

        import robin_stocks.robinhood as rs  # type: ignore
        acct = self._account_number or None

        if ref_id is None:
            # Legacy path (iron condor): robin_stocks mints its own uuid4 ref_id.
            result = await asyncio.to_thread(
                rs.orders.order_option_spread,
                combo.direction,
                combo.net_limit,
                combo.underlying,
                combo.quantity,
                spread,
                account_number=acct,
                timeInForce="gfd",     # day-only — matches PMCC; no resting GTC
            )
        else:
            # Deterministic-ref_id path: replicate order_option_spread's payload
            # build (SAME rs internals) but stamp OUR ref_id so a transient retry
            # of the same combo dedupes at Robinhood instead of double-placing.
            result = await asyncio.to_thread(
                self._submit_spread_with_ref_id, spread, combo, acct, ref_id,
            )

        # Combo accepted? A genuinely-placed combo carries an 'id' (single ref).
        # No id (None / empty / error dict) means the whole combo did NOT place —
        # RAISE, never synthesize per-leg fills (the same fake-fill bug, here on
        # the LIVE iron-condor path: a fabricated fill books a PHANTOM IC). This
        # is DISTINCT from a SUCCESSFUL combo that just didn't echo legs[] — that
        # still has an id and legitimately falls back to limit_price below.
        result = result or {}
        if not result.get("id"):
            reason = (result.get("non_field_errors") or result.get("detail")
                      or result or "empty response")
            raise RobinhoodOrderError(
                f"Robinhood did not accept the {combo.direction} combo on "
                f"{combo.underlying} x{combo.quantity}: {reason}"
            )
        rh_combo_id = str(result.get("id"))
        # The account the combo hit: RH may not echo it on a spread, so fall back
        # to the bound account_number we placed on (Bug-2 routing identity).
        rh_account = self._account_number_from(result) or self._account_number or None

        # ── STATE-INTEGRITY GATE (2026-07-23) ─────────────────────────────────
        # Book positions ONLY on a TERMINAL `filled` state — NEVER on the submit
        # acknowledgement. A resting/`confirmed` spread booked as filled desyncs
        # the position tracker from the broker (today's failure). Poll to terminal,
        # then branch. Applies to the IC path too (shared code).
        final = await self._await_terminal_option_order(result, rh_combo_id)
        state = str((final or {}).get("state") or "").lower()
        if state in ("rejected", "cancelled", "canceled", "failed", "voided"):
            raise RobinhoodOrderError(
                f"combo {rh_combo_id} reached terminal state {state!r} — booked "
                f"NOTHING ({combo.direction} {combo.underlying} x{combo.quantity})"
            )
        if state == "partially_filled":
            # An atomic spread should not partial-fill; surface loudly and book
            # NOTHING. PMCC/IC re-derive positions from the broker each scan, which
            # reconciles the true state — never book a half position here.
            raise RobinhoodOrderError(
                f"combo {rh_combo_id} PARTIALLY_FILLED — anomaly on an atomic "
                "spread; booked NOTHING, needs reconciliation"
            )
        if state != "filled":
            # Timed out while still queued/confirmed/unconfirmed → NOT confirmed.
            raise RobinhoodComboPending(
                f"combo {rh_combo_id} still {state or 'unknown'!r} after "
                f"{self._COMBO_FILL_TIMEOUT_S:.0f}s poll — pending/unconfirmed; "
                "booked NOTHING",
                order_id=rh_combo_id,
            )

        legs_result = (final or {}).get("legs") or []
        fill_ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        fills: list[FillEvent] = []
        for i, o in enumerate(orders):
            # Best-effort per-leg fill price. Sources, in priority order:
            #  1. legs_result[i]["price"]                — if RH echoes per leg
            #  2. legs_result[i]["executions"][0]["price"]  — alt shape
            #  3. order.limit_price                      — fallback
            leg_price = None
            if i < len(legs_result):
                leg = legs_result[i] or {}
                leg_price = leg.get("price")
                if leg_price is None:
                    execs = leg.get("executions") or []
                    if execs and isinstance(execs, list):
                        leg_price = (execs[0] or {}).get("price")
            try:
                price_f = float(leg_price) if leg_price is not None else float(o.limit_price or 0)
            except (TypeError, ValueError):
                price_f = float(o.limit_price or 0)

            fills.append(FillEvent(
                order_id=o.id,
                symbol=o.symbol,
                side=o.side,
                qty=float(o.qty),
                price=price_f,
                ts=fill_ts,
                venue="robinhood",
                broker_order_id=rh_combo_id,   # Bug-2: the combo's RH order id
                account=rh_account,
            ))
        return fills

    # Terminal-fill poll (item-1 state integrity). Instance-overridable so tests
    # can shrink the window; PMCC re-prices marketable so fills land in < 1s.
    _COMBO_FILL_TIMEOUT_S: float = 20.0
    _COMBO_FILL_POLL_S: float = 1.0
    _TERMINAL_OPTION_STATES: frozenset = frozenset({
        "filled", "partially_filled", "rejected", "cancelled", "canceled",
        "failed", "voided",
    })

    async def _await_terminal_option_order(self, submit_result: dict,
                                           order_id: str) -> dict:
        """Poll an option order to a TERMINAL state (bounded). Returns the latest
        order dict. If the submit response is already terminal (fast fills /
        offline tests) it returns immediately without polling. On timeout it
        returns the last non-terminal dict — the caller treats a non-`filled`
        result as pending and books nothing."""
        st = str((submit_result or {}).get("state") or "").lower()
        if st in self._TERMINAL_OPTION_STATES:
            return submit_result or {}
        import robin_stocks.robinhood as rs  # type: ignore
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._COMBO_FILL_TIMEOUT_S
        last = submit_result or {}
        while loop.time() < deadline:
            await asyncio.sleep(self._COMBO_FILL_POLL_S)
            try:
                info = await asyncio.to_thread(
                    rs.orders.get_option_order_info, order_id)
            except Exception as e:      # transient poll failure — keep trying
                log.warning("combo %s poll get_option_order_info failed: %s",
                            order_id, e)
                continue
            if info:
                last = info
                if str(info.get("state") or "").lower() in self._TERMINAL_OPTION_STATES:
                    return info
        return last

    def _submit_spread_with_ref_id(self, spread, combo, acct, ref_id):
        """Build + POST an option-spread order with a caller-supplied ref_id.

        The pinned `rs.orders.order_option_spread` mints its own `uuid4` ref_id
        (no param), so a retry double-places. This replicates its exact payload
        build using the SAME rs internals (`id_for_option` /
        `option_instruments_url` / `load_account_profile` / `option_orders_url` /
        `request_post`) but stamps OUR deterministic ref_id. Runs on a worker
        thread (network I/O). `spread` already carries per-leg expirationDate/
        strike/optionType/effect/action/ratio_quantity.
        """
        import robin_stocks.robinhood.orders as O  # type: ignore
        legs = []
        for leg in spread:
            option_id = O.id_for_option(
                combo.underlying, leg["expirationDate"], leg["strike"],
                leg["optionType"],
            )
            legs.append({
                "position_effect": leg["effect"],
                "side": leg["action"],
                "ratio_quantity": leg["ratio_quantity"],
                "option": O.option_instruments_url(option_id),
            })
        payload = {
            "account": O.load_account_profile(account_number=acct, info="url"),
            "direction": combo.direction,
            "time_in_force": "gfd",
            "legs": legs,
            "type": "limit",
            "trigger": "immediate",
            "price": combo.net_limit,
            "quantity": combo.quantity,
            "override_day_trade_checks": False,
            "override_dtbp_checks": False,
            "ref_id": ref_id,
        }
        url = O.option_orders_url(account_number=acct)
        return O.request_post(url, payload, json=True, jsonify_data=True)

    async def get_option_quote(
        self, symbol: str, expiration: str, strike: float, option_type: str,
    ) -> dict[str, float | None]:
        """Live {bid, ask, mark} for one contract by (symbol, expiration, strike,
        type) — used by the combo dispatch to re-price a spread from the natural.
        Wraps rs.options.get_option_market_data (which returns a nested list)."""
        self._require_connected()
        import robin_stocks.robinhood as rs  # type: ignore
        raw = await asyncio.to_thread(
            rs.options.get_option_market_data, symbol, expiration,
            str(strike), option_type,
        )
        md = raw
        while isinstance(md, list) and md:
            md = md[0]
        md = md if isinstance(md, dict) else {}

        def _f(v) -> float | None:
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        # min_ticks (for the NET-price tick rule): RH echoes {above_tick,
        # below_tick, cutoff_price} on the instrument/market-data. May be absent →
        # the re-pricer falls back to the standard 0.05≥$3 / 0.01 rule.
        mt = md.get("min_ticks") or {}
        return {
            "bid": _f(md.get("bid_price")),
            "ask": _f(md.get("ask_price")),
            "mark": _f(md.get("adjusted_mark_price") or md.get("mark_price")),
            "below_tick": _f(mt.get("below_tick")),
            "above_tick": _f(mt.get("above_tick")),
            "cutoff": _f(mt.get("cutoff_price")),
        }

    async def get_option_greeks(
        self, option_id: str
    ) -> dict[str, float | None]:
        """Return delta, gamma, theta, vega, iv, mark_price for an option.

        Thin wrapper over robin_stocks.options.get_option_market_data_by_id.
        Already used internally by get_option_positions_detail (line ~509);
        this method exposes it on the Broker ABC so strategy code can
        identify the tested side of an open iron condor without holding
        the position dict.

        Returns None for any individual field that the venue omits or
        that fails float() coercion. Network/venue failure propagates as
        an exception — callers (`_identify_tested_side` in the IC strategy)
        treat this as "tested side undetermined" and skip adjustment.
        """
        self._require_connected()
        import robin_stocks.robinhood as rs  # type: ignore
        raw = await asyncio.to_thread(
            rs.options.get_option_market_data_by_id, option_id
        )

        if isinstance(raw, list) and raw:
            md = raw[0] or {}
        elif isinstance(raw, dict):
            md = raw
        else:
            md = {}

        def _f(v) -> float | None:
            try:
                return float(v) if v is not None else None
            except (TypeError, ValueError):
                return None

        return {
            "delta": _f(md.get("delta")),
            "gamma": _f(md.get("gamma")),
            "theta": _f(md.get("theta")),
            "vega":  _f(md.get("vega")),
            "iv":    _f(md.get("implied_volatility")),
            "mark_price": _f(
                md.get("adjusted_mark_price") or md.get("mark_price")
            ),
            "bid": _f(md.get("bid_price")),
            "ask": _f(md.get("ask_price")),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _require_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("RobinhoodBroker not connected — call connect() first")

