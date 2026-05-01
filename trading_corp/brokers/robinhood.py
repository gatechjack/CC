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
from datetime import date, datetime, timezone
from threading import Lock

from trading_corp.brokers.base import AccountSnapshot, Broker
from trading_corp.persistence.models import FillEvent, Position, ProposedOrder

log = logging.getLogger(__name__)

# Module-level shared login state. robin_stocks itself is a process-wide
# singleton, so we just need to ensure rs.login() runs at most once across
# all RobinhoodBroker instances.
_LOGIN_LOCK = Lock()
_LOGIN_DONE = False
_ACCOUNT_LIST: list[dict] = []   # cached account list from /accounts/ endpoint

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


def _days_to_expiry(expiration_date: str) -> int:
    """Calendar days from today to expiration_date ('YYYY-MM-DD')."""
    try:
        exp = date.fromisoformat(expiration_date)
        return max(0, (exp - date.today()).days)
    except (ValueError, TypeError):
        return 0


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

        # Resolve this instance's filter → account_number
        self._resolve_account_filter()
        self._connected = True
        log.info(
            "RobinhoodBroker bound: filter=%r → account=%s (%s)",
            self._account_filter, self._account_number or "default",
            self._account_label or "default",
        )

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
        """All call options for `symbol` expiring on `expiry` with greeks +
        liquidity fields used by PMCCAgent's gates.

        Each item:
          option_id, expiration_date, strike_price, delta, mark_price,
          bid, ask, bid_size, ask_size, dte,
          open_interest, volume, implied_volatility, theta, gamma, vega.
        """
        self._require_connected()
        import robin_stocks.robinhood as rs  # type: ignore

        raw = await asyncio.to_thread(
            rs.options.find_options_by_expiration, [symbol], expiry, "call"
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

        calls = []
        for opt in raw:
            mark_raw = opt.get("adjusted_mark_price") or opt.get("mark_price")
            calls.append({
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
        return calls

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    async def place_order(self, order: ProposedOrder) -> FillEvent:
        self._require_connected()
        if (order.extra or {}).get("is_option"):
            return await self._place_option_order(order)
        return await self._place_stock_order(order)

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
            result = await asyncio.to_thread(
                fn, order.symbol, qty, account_number=acct,
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

        result = result or {}
        price = float(result.get("average_price") or order.limit_price or 0)
        return FillEvent(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            qty=float(qty),
            price=price,
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            venue="robinhood",
        )

    async def _place_option_order(self, order: ProposedOrder) -> FillEvent:
        import robin_stocks.robinhood as rs  # type: ignore
        extra = order.extra or {}
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

        result = result or {}
        fill_price = float(
            result.get("processed_premium") or result.get("price") or price
        )
        return FillEvent(
            order_id=order.id,
            symbol=order.symbol,
            side=order.side,
            qty=float(qty),
            price=fill_price,
            ts=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            venue="robinhood",
        )

    async def cancel_order(self, order_id: str) -> bool:
        self._require_connected()
        import robin_stocks.robinhood as rs  # type: ignore
        result = await asyncio.to_thread(rs.orders.cancel_option_order, order_id)
        return bool(result)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _require_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("RobinhoodBroker not connected — call connect() first")

