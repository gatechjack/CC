"""IC Candidate Grader — research/grading tool for /telemetry/iron_condor.

The operator pastes a Barchart options-screener block; this module grades
each row against the live `robinhood_joint_iron_condor` rules using LIVE
data from the existing MarketDataProvider — never the pasted Barchart
numbers.

CRITICAL INVARIANT — NO EXECUTION PATH:
This module MUST NOT import or call `place_combo`, `PendingComboRegistry`,
`dispatch_approved_ic_combo`, `data_exec`, or any other order-surface
function. It grades and returns verdicts; nothing else. The AST-walk
test in `tests/test_ic_grader.py:test_no_execution_invariant` enforces
this structurally.

Gate order (cheap → expensive, first failure wins):
  1. universe              — pure string match, no provider call
  2. expiration_on_chain   — get_option_chain (also covers symbol resolvability)
  3. dte                   — derived from chain, no extra call
  4. ivr                   — get_iv_rank
  5. strikes_exist         — derived from chain, no extra call
  6. delta_proximity       — chain delta first; get_greeks fallback if None
  7. term_structure        — get_atm_iv (front + back)
  8. credit                — derived from chain mids, no extra call

Cfg snapshot is taken once at the top of `grade_paste()` and frozen on
RunContext. Within-run consistency is correct for a ≤5s grading run.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Literal, Protocol

from trading_corp.data.market_data_provider import (
    MarketDataProvider,
    OptionContract,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants — hardcoded tolerances mirror the strategy (not in YAML)
# ---------------------------------------------------------------------------

TARGET_DTE_TOLERANCE = 7         # ±7 days from entry.target_dte
SHORT_DELTA_TOLERANCE = 0.05     # ±0.05 from entry.short_delta
SHORT_DELTA_HARD_CUTOFF = 0.30   # |delta| ≥ this is "too high"
BACK_DTE = 75                    # back-leg DTE for term-structure check
BACK_DTE_TOLERANCE = 15

DEFAULT_PER_CALL_TIMEOUT = 5.0
DEFAULT_CALL_BUDGET = 20
DEFAULT_ROW_CAP = 25


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ParsedCandidate:
    """One row parsed from the operator's paste."""
    raw_line: str
    symbol: str | None
    expiration: date | None
    pasted_dte: int | None
    short_put_strike: float | None
    long_put_strike: float | None
    short_call_strike: float | None
    long_call_strike: float | None
    pasted_iv_rank: float | None  # informational only; never gated against
    parse_errors: tuple[str, ...] = ()


@dataclass(frozen=True)
class GradeResult:
    """Verdict for one ParsedCandidate."""
    candidate: ParsedCandidate
    verdict: Literal["PASS", "FAIL", "NEEDS_LIVE_DATA", "PARSE_ERROR"]
    failed_gate: str | None
    reason: str
    measurements: dict[str, Any]
    provider_calls: int
    graded_at_iso: str


@dataclass(frozen=True)
class GraderRunResult:
    """Output of one grade_paste run."""
    now_iso: str
    config_mtime_iso: str
    config_version_hash: str
    rows: tuple[GradeResult, ...]
    paste_warnings: tuple[str, ...]
    summary: dict[str, Any]
    provider_calls_total: int


# ---------------------------------------------------------------------------
# Strategy / provider protocols (duck-typed so this module imports nothing
# from the strategy module — avoids any transitive coupling risk)
# ---------------------------------------------------------------------------

class _StrategyLike(Protocol):
    @property
    def universe(self) -> list[str]: ...
    @property
    def wing_widths(self) -> dict[str, float]: ...
    def cfg(self, dotted: str) -> Any: ...
    # Optional attribute (defaults True via getattr fallback in _snapshot_cfg):
    # when False, off-universe symbols don't fail gate 1 — they get a
    # "off_watchlist" warning tag on the final result but proceed through
    # gates 2-8 normally. Tasty Options sets False so its "watchlist" is
    # informational/curated rather than a hard gate; Robinhood Joint leaves
    # the attribute undefined → defaults True → original behavior.
    strict_universe: bool


class _LoggerLike(Protocol):
    def log_event(
        self, actor: str, kind: str, payload: dict[str, Any],
    ) -> int | None: ...


# ---------------------------------------------------------------------------
# Internal exception types — caught inside gates, converted to NEEDS_LIVE_DATA
# ---------------------------------------------------------------------------

class _BudgetExhausted(Exception):
    pass


class _ProviderTimeout(Exception):
    def __init__(self, key: str):
        self.key = key


# ---------------------------------------------------------------------------
# RunContext — frozen snapshot of cfg + run-scoped mutable counters
# ---------------------------------------------------------------------------

@dataclass
class _RunContext:
    universe: tuple[str, ...]
    wing_widths: dict[str, float]
    target_dte: int
    short_delta: float
    min_credit_pct_of_width: float
    min_ivr: float
    term_structure_max_diff: float
    config_mtime_iso: str
    config_version_hash: str
    provider: MarketDataProvider
    per_call_timeout: float
    call_budget: int
    calls_used: int = 0
    clock: Any = None  # callable returning current datetime
    # When True (default), `_gate_universe` fails any candidate whose symbol
    # is not in `universe` — Robinhood Joint behavior. When False (Tasty
    # Options), off-universe symbols proceed through gates 2-8 and the final
    # row is tagged `watchlist_membership: "off"` in measurements.
    strict_universe: bool = True
    # Per-run cache keyed by (method, *args). Cache hits don't consume
    # budget. Makes `test_cache_reuse_two_rows_same_symbol_expiration`
    # deterministic without depending on the provider's internal cache.
    cache: dict = field(default_factory=dict)


def _snapshot_cfg(
    strategy: _StrategyLike, *, clock,
) -> _RunContext:
    """Take a frozen cfg snapshot. Reads YAML once via strategy.cfg(...)."""
    cfg_snapshot: dict[str, Any] = {
        "universe": list(strategy.universe),
        "wing_widths": dict(strategy.wing_widths),
        "target_dte": int(strategy.cfg("entry.target_dte")),
        "short_delta": float(strategy.cfg("entry.short_delta")),
        "min_credit_pct_of_width": float(strategy.cfg("entry.min_credit_pct_of_width")),
        "min_ivr": float(strategy.cfg("entry.min_ivr")),
        "term_structure_max_diff": float(strategy.cfg("entry.term_structure_max_diff")),
    }
    canonical = json.dumps(cfg_snapshot, sort_keys=True, default=str)
    version_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]

    # Best-effort cfg mtime — read the strategies.yaml file directly if
    # it exists at the conventional path; otherwise stamp "unknown".
    cfg_path = os.path.join("config", "strategies.yaml")
    try:
        mtime = os.path.getmtime(cfg_path)
        config_mtime_iso = (
            datetime.fromtimestamp(mtime, tz=timezone.utc)
            .isoformat(timespec="seconds")
        )
    except OSError:
        config_mtime_iso = "unknown"

    return _RunContext(
        universe=tuple(cfg_snapshot["universe"]),
        wing_widths=cfg_snapshot["wing_widths"],
        target_dte=cfg_snapshot["target_dte"],
        short_delta=cfg_snapshot["short_delta"],
        min_credit_pct_of_width=cfg_snapshot["min_credit_pct_of_width"],
        min_ivr=cfg_snapshot["min_ivr"],
        term_structure_max_diff=cfg_snapshot["term_structure_max_diff"],
        config_mtime_iso=config_mtime_iso,
        config_version_hash=version_hash,
        provider=None,  # filled by grade_paste before any gate runs
        per_call_timeout=DEFAULT_PER_CALL_TIMEOUT,
        call_budget=DEFAULT_CALL_BUDGET,
        clock=clock,
        # RH Joint strategy doesn't define this attribute → defaults True →
        # original gate-1-blocks-off-universe behavior. Tasty Options strategy
        # sets False as a class-level attribute.
        strict_universe=bool(getattr(strategy, "strict_universe", True)),
    )


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_MD_LINK_RE = re.compile(r"\[([^\[\]]+?)\]\([^)]*\)")
_BARE_BRACKET_RE = re.compile(r"\[([A-Z]{1,5})\]")
_DATE_DTE_RE = re.compile(
    r"(\d{1,2}/\d{1,2}/\d{2,4})\s*\((\d{1,3})\)"
)
_STRIKE_PAIR_RE = re.compile(r"(\d+(?:\.\d+)?)/(\d+(?:\.\d+)?)")
_PCT_RE = re.compile(r"^(\d+(?:\.\d+)?)%$")
_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.]{0,5}$")
_NUMERIC_TOKEN_RE = re.compile(r"^\$?-?[\d,]+(?:\.\d+)?%?$")


def _strip_markdown_links(line: str) -> tuple[str, list[str]]:
    """Strip `[NAME](url)` and `[NAME]` wrappers. Return (cleaned, errors)."""
    errors: list[str] = []
    cleaned = _MD_LINK_RE.sub(lambda m: m.group(1), line)
    cleaned = _BARE_BRACKET_RE.sub(lambda m: m.group(1), cleaned)
    if "[" in cleaned or "]" in cleaned:
        errors.append("unclosed_bracket")
    return cleaned, errors


def _parse_date_2y(s: str) -> date | None:
    """Parse MM/DD/YY or MM/DD/YYYY; 2-digit year assumed 20YY."""
    parts = s.split("/")
    if len(parts) != 3:
        return None
    try:
        mm, dd, yy = (int(p) for p in parts)
    except ValueError:
        return None
    if yy < 100:
        yy += 2000
    try:
        return date(yy, mm, dd)
    except ValueError:
        return None


def _strip_numeric_commas(token: str) -> str:
    """Strip commas inside numeric tokens (1,234.50 → 1234.50)."""
    if _NUMERIC_TOKEN_RE.match(token):
        return token.replace(",", "")
    return token


def _parse_row(raw_line: str) -> ParsedCandidate:
    """Parse one Barchart row. Never raises — packages errors into the
    ParsedCandidate's parse_errors tuple."""
    errors: list[str] = []
    try:
        cleaned, link_errors = _strip_markdown_links(raw_line.strip())
        errors.extend(link_errors)

        # Split on any whitespace run — Barchart exports use tabs OR
        # 2+ spaces between fields, but single-space-separated rows
        # also need to tokenize cleanly. Regex search on the full
        # cleaned string is what extracts date/DTE and strike pairs,
        # so token boundaries don't affect those.
        tokens = re.split(r"\s+", cleaned)
        tokens = [_strip_numeric_commas(t) for t in tokens if t.strip()]
        if not tokens:
            return ParsedCandidate(
                raw_line=raw_line, symbol=None, expiration=None,
                pasted_dte=None, short_put_strike=None, long_put_strike=None,
                short_call_strike=None, long_call_strike=None,
                pasted_iv_rank=None,
                parse_errors=("empty_row",),
            )

        # Symbol = first non-empty token (post markdown strip)
        symbol_raw = tokens[0].upper()
        symbol = symbol_raw if _SYMBOL_RE.match(symbol_raw) else None
        if symbol is None:
            errors.append(f"invalid_symbol:{symbol_raw[:10]}")

        # Date + DTE — scan the joined cleaned line so split boundaries
        # don't break the regex match.
        expiration: date | None = None
        pasted_dte: int | None = None
        dm = _DATE_DTE_RE.search(cleaned)
        if dm:
            expiration = _parse_date_2y(dm.group(1))
            try:
                pasted_dte = int(dm.group(2))
            except ValueError:
                pasted_dte = None

        # Strikes — find two adjacent strike-pair tokens.  The
        # date+DTE substring (e.g. "07/06/26 (45)") contains slashes
        # that look like strike pairs to the regex, so we strip it
        # from the search text before scanning.
        strike_search_text = cleaned
        if dm:
            strike_search_text = (
                cleaned[: dm.start()] + " " + cleaned[dm.end():]
            )
        strike_pairs = _STRIKE_PAIR_RE.findall(strike_search_text)
        short_put = long_put = short_call = long_call = None
        if len(strike_pairs) >= 2:
            try:
                p1_a, p1_b = float(strike_pairs[0][0]), float(strike_pairs[0][1])
                p2_a, p2_b = float(strike_pairs[1][0]), float(strike_pairs[1][1])
                # First pair = puts: lower = long_put, higher = short_put
                long_put, short_put = sorted((p1_a, p1_b))
                # Second pair = calls: lower = short_call, higher = long_call
                short_call, long_call = sorted((p2_a, p2_b))
            except (ValueError, TypeError):
                errors.append("strike_parse_failed")
        elif len(strike_pairs) == 1:
            errors.append("missing_strike_pair")

        # IV Rank — first %-suffixed numeric token. Informational only.
        pasted_iv_rank: float | None = None
        for t in tokens:
            m = _PCT_RE.match(t)
            if m:
                try:
                    pasted_iv_rank = float(m.group(1))
                    break
                except ValueError:
                    pass

        return ParsedCandidate(
            raw_line=raw_line,
            symbol=symbol,
            expiration=expiration,
            pasted_dte=pasted_dte,
            short_put_strike=short_put,
            long_put_strike=long_put,
            short_call_strike=short_call,
            long_call_strike=long_call,
            pasted_iv_rank=pasted_iv_rank,
            parse_errors=tuple(errors),
        )
    except Exception as exc:
        # Final defensive net — any unforeseen parser bug becomes a
        # parseable error rather than crashing the whole run.
        return ParsedCandidate(
            raw_line=raw_line, symbol=None, expiration=None,
            pasted_dte=None, short_put_strike=None, long_put_strike=None,
            short_call_strike=None, long_call_strike=None,
            pasted_iv_rank=None,
            parse_errors=(f"unparseable:{type(exc).__name__}",),
        )


def parse_paste(
    text: str, *, row_cap: int = DEFAULT_ROW_CAP, clock=None,
) -> tuple[list[ParsedCandidate], list[str]]:
    """Parse the operator's paste. Return (parsed_rows, paste_warnings).

    paste_warnings is non-empty when the paste is structurally rejected
    (over row_cap) or carries a non-blocking notice (stale-paste guard).
    """
    if clock is None:
        clock = lambda: datetime.now(timezone.utc)
    paste_warnings: list[str] = []

    # Pre-clean and count non-empty lines.
    raw_lines = text.replace("\r", "").replace("​", "").splitlines()
    non_empty = [ln for ln in raw_lines if ln.strip()]

    if len(non_empty) > row_cap:
        paste_warnings.append(
            f"trim to {row_cap} rows (got {len(non_empty)})"
        )
        return [], paste_warnings

    rows = [_parse_row(ln) for ln in non_empty]

    # Stale-paste guard — non-blocking.
    today = clock().date()
    expired_examples = [
        c.expiration.isoformat()
        for c in rows
        if c.expiration is not None and c.expiration < today
    ]
    if expired_examples:
        paste_warnings.insert(
            0,
            f"this paste contains expired dates (e.g. {expired_examples[0]})"
            " — is it from a previous session?",
        )

    return rows, paste_warnings


# ---------------------------------------------------------------------------
# Gate-call helper — single chokepoint for timeout + budget
# ---------------------------------------------------------------------------

async def _provider_call(
    ctx: _RunContext, cache_key: tuple, op_key: str, coro_factory,
):
    """Single chokepoint for provider calls: cache → budget → timeout.

    cache_key is a tuple uniquely identifying the call's (method, args).
    `coro_factory` is a zero-arg callable that returns the provider
    coroutine — passed as a factory (not a pre-built coroutine) so cache
    hits don't trigger the provider call at all.  This matters for
    test-time AsyncMock: pre-building the coroutine increments the
    mock's call_count even on cache hit.
    """
    if cache_key in ctx.cache:
        return ctx.cache[cache_key]
    if ctx.calls_used >= ctx.call_budget:
        raise _BudgetExhausted()
    ctx.calls_used += 1
    try:
        result = await asyncio.wait_for(
            coro_factory(), timeout=ctx.per_call_timeout,
        )
    except asyncio.TimeoutError:
        raise _ProviderTimeout(op_key)
    ctx.cache[cache_key] = result
    return result


def _now_iso(ctx: _RunContext) -> str:
    return ctx.clock().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Gates — each returns None to continue, or a terminal GradeResult.
# ---------------------------------------------------------------------------

def _gate_universe(
    c: ParsedCandidate, ctx: _RunContext,
) -> GradeResult | None:
    # Missing symbol is a parse-level failure regardless of strict mode —
    # gates 2-8 cannot run without one. Always FAILs.
    if c.symbol is None:
        return GradeResult(
            candidate=c, verdict="FAIL", failed_gate="universe",
            reason=(
                f"symbol {c.symbol} not in universe "
                f"[{', '.join(ctx.universe)}]"
            ),
            measurements={"pasted_symbol": c.symbol},
            provider_calls=ctx.calls_used,
            graded_at_iso=_now_iso(ctx),
        )
    if c.symbol not in ctx.universe:
        # Non-strict mode (Tasty Options): off-universe symbol proceeds
        # through gates 2-8; the watchlist_membership tag is added to the
        # final result in _grade_row. Returning None here lets the live
        # provider gates do the real work — the watchlist is informational.
        if not ctx.strict_universe:
            return None
        return GradeResult(
            candidate=c, verdict="FAIL", failed_gate="universe",
            reason=(
                f"symbol {c.symbol} not in universe "
                f"[{', '.join(ctx.universe)}]"
            ),
            measurements={"pasted_symbol": c.symbol},
            provider_calls=ctx.calls_used,
            graded_at_iso=_now_iso(ctx),
        )
    return None


async def _gate_expiration_on_chain(
    c: ParsedCandidate, ctx: _RunContext, chain: list[OptionContract],
) -> GradeResult | None:
    """Returns None on pass. chain is the result of get_option_chain;
    empty chain → FAIL expiration_not_available (also covers
    symbol-not-resolvable since an unknown symbol returns empty)."""
    if not chain:
        return GradeResult(
            candidate=c, verdict="FAIL",
            failed_gate="expiration_not_available",
            reason=(
                f"expiration {c.expiration} not on live chain "
                f"(empty chain; also the verdict for an unresolvable symbol)"
            ),
            measurements={
                "pasted_expiration": (
                    c.expiration.isoformat() if c.expiration else None
                ),
            },
            provider_calls=ctx.calls_used,
            graded_at_iso=_now_iso(ctx),
        )
    return None


def _gate_dte(
    c: ParsedCandidate, ctx: _RunContext, chain: list[OptionContract],
) -> GradeResult | None:
    # Derive live_dte from the first chain contract — all share an expiration.
    live_dte_raw = chain[0].dte
    live_dte = int(live_dte_raw) if live_dte_raw is not None else None
    if live_dte is None:
        return GradeResult(
            candidate=c, verdict="NEEDS_LIVE_DATA", failed_gate="dte",
            reason="DTE missing from chain row (provider didn't populate dte)",
            measurements={"pasted_dte": c.pasted_dte},
            provider_calls=ctx.calls_used,
            graded_at_iso=_now_iso(ctx),
        )
    if abs(live_dte - ctx.target_dte) > TARGET_DTE_TOLERANCE:
        return GradeResult(
            candidate=c, verdict="FAIL", failed_gate="dte",
            reason=(
                f"DTE {live_dte} (target {ctx.target_dte}"
                f"±{TARGET_DTE_TOLERANCE})"
            ),
            measurements={
                "live_dte": live_dte,
                "target_dte": ctx.target_dte,
                "tolerance": TARGET_DTE_TOLERANCE,
                "pasted_dte": c.pasted_dte,
            },
            provider_calls=ctx.calls_used,
            graded_at_iso=_now_iso(ctx),
        )
    return None


async def _gate_ivr(
    c: ParsedCandidate, ctx: _RunContext,
) -> GradeResult | None:
    try:
        ivr_decimal = await _provider_call(
            ctx, ("ivr", c.symbol), "get_iv_rank",
            lambda: ctx.provider.get_iv_rank(c.symbol),
        )
    except _BudgetExhausted:
        return _budget_result(c, ctx, "ivr")
    except _ProviderTimeout as t:
        return _timeout_result(c, ctx, "ivr", t.key)
    if ivr_decimal is None:
        return GradeResult(
            candidate=c, verdict="NEEDS_LIVE_DATA", failed_gate="ivr",
            reason="live IVR unavailable (provider returned None)",
            measurements={"pasted_ivr_pct": c.pasted_iv_rank},
            provider_calls=ctx.calls_used,
            graded_at_iso=_now_iso(ctx),
        )
    live_ivr_pct = float(ivr_decimal) * 100.0
    if live_ivr_pct < ctx.min_ivr:
        return GradeResult(
            candidate=c, verdict="FAIL", failed_gate="ivr",
            reason=f"live IVR {live_ivr_pct:.0f} < {ctx.min_ivr:.0f}",
            measurements={
                "live_ivr_pct": live_ivr_pct,
                "min_ivr": ctx.min_ivr,
                "pasted_ivr_pct": c.pasted_iv_rank,
            },
            provider_calls=ctx.calls_used,
            graded_at_iso=_now_iso(ctx),
        )
    return None


def _gate_strikes_exist(
    c: ParsedCandidate, ctx: _RunContext, chain: list[OptionContract],
) -> GradeResult | None:
    """Validate that the four pasted strikes appear on the live chain."""
    if any(
        s is None for s in (
            c.short_put_strike, c.long_put_strike,
            c.short_call_strike, c.long_call_strike,
        )
    ):
        return GradeResult(
            candidate=c, verdict="FAIL", failed_gate="strikes_missing",
            reason="one or more strikes not parsed from paste",
            measurements={
                "short_put": c.short_put_strike,
                "long_put": c.long_put_strike,
                "short_call": c.short_call_strike,
                "long_call": c.long_call_strike,
            },
            provider_calls=ctx.calls_used,
            graded_at_iso=_now_iso(ctx),
        )

    chain_strikes_by_type: dict[str, set[float]] = {"put": set(), "call": set()}
    for contract in chain:
        if contract.strike is None:
            continue
        chain_strikes_by_type.setdefault(contract.option_type, set()).add(
            float(contract.strike)
        )

    missing: list[str] = []
    for label, strike, opt_type in (
        ("short put", c.short_put_strike, "put"),
        ("long put",  c.long_put_strike,  "put"),
        ("short call", c.short_call_strike, "call"),
        ("long call",  c.long_call_strike,  "call"),
    ):
        if strike not in chain_strikes_by_type.get(opt_type, set()):
            missing.append(f"{label} {strike}")

    if missing:
        return GradeResult(
            candidate=c, verdict="FAIL", failed_gate="strike_not_found",
            reason=f"{missing[0]} not on live chain",
            measurements={
                "missing_strikes": missing,
                "pasted_strikes": {
                    "short_put": c.short_put_strike,
                    "long_put": c.long_put_strike,
                    "short_call": c.short_call_strike,
                    "long_call": c.long_call_strike,
                },
            },
            provider_calls=ctx.calls_used,
            graded_at_iso=_now_iso(ctx),
        )
    return None


def _find_contract(
    chain: list[OptionContract], opt_type: str, strike: float,
) -> OptionContract | None:
    for c in chain:
        if c.option_type == opt_type and c.strike == strike:
            return c
    return None


async def _resolve_delta(
    contract: OptionContract, ctx: _RunContext,
) -> float | None:
    """Use chain delta if present; otherwise fall back to get_greeks."""
    if contract.delta is not None:
        return float(contract.delta)
    # Chain didn't carry delta → fall back to provider.get_greeks
    gk = await _provider_call(
        ctx, ("greeks", contract.option_id), "get_greeks",
        lambda: ctx.provider.get_greeks(contract.option_id),
    )
    if not gk:
        return None
    raw = gk.get("delta")
    return float(raw) if raw is not None else None


async def _gate_delta_proximity(
    c: ParsedCandidate, ctx: _RunContext, chain: list[OptionContract],
) -> GradeResult | None:
    short_put = _find_contract(chain, "put", c.short_put_strike)
    short_call = _find_contract(chain, "call", c.short_call_strike)
    # Resolve deltas (chain first, fallback to get_greeks).
    try:
        sp_delta = (
            await _resolve_delta(short_put, ctx)
            if short_put is not None else None
        )
        sc_delta = (
            await _resolve_delta(short_call, ctx)
            if short_call is not None else None
        )
    except _BudgetExhausted:
        return _budget_result(c, ctx, "delta_proximity")
    except _ProviderTimeout as t:
        return _timeout_result(c, ctx, "delta_proximity", t.key)

    if sp_delta is None or sc_delta is None:
        return GradeResult(
            candidate=c, verdict="NEEDS_LIVE_DATA",
            failed_gate="delta_proximity",
            reason=(
                "short-leg delta unavailable "
                f"(short_put_delta={sp_delta}, short_call_delta={sc_delta})"
            ),
            measurements={
                "short_put_delta": sp_delta,
                "short_call_delta": sc_delta,
            },
            provider_calls=ctx.calls_used,
            graded_at_iso=_now_iso(ctx),
        )

    # Hard cutoff (above 0.30) — short_delta_too_high.
    if abs(sp_delta) >= SHORT_DELTA_HARD_CUTOFF:
        return GradeResult(
            candidate=c, verdict="FAIL",
            failed_gate="short_delta_too_high",
            reason=(
                f"short put delta {sp_delta:.2f} — above "
                f"{SHORT_DELTA_HARD_CUTOFF:.2f} cutoff (short_delta_too_high)"
            ),
            measurements={
                "short_put_delta": sp_delta,
                "short_call_delta": sc_delta,
                "hard_cutoff": SHORT_DELTA_HARD_CUTOFF,
            },
            provider_calls=ctx.calls_used,
            graded_at_iso=_now_iso(ctx),
        )
    if abs(sc_delta) >= SHORT_DELTA_HARD_CUTOFF:
        return GradeResult(
            candidate=c, verdict="FAIL",
            failed_gate="short_delta_too_high",
            reason=(
                f"short call delta {sc_delta:.2f} — above "
                f"{SHORT_DELTA_HARD_CUTOFF:.2f} cutoff (short_delta_too_high)"
            ),
            measurements={
                "short_put_delta": sp_delta,
                "short_call_delta": sc_delta,
                "hard_cutoff": SHORT_DELTA_HARD_CUTOFF,
            },
            provider_calls=ctx.calls_used,
            graded_at_iso=_now_iso(ctx),
        )

    # Proximity check: |delta - target| ≤ tolerance. Put target is
    # negative; call target is positive.
    target_put = -ctx.short_delta
    target_call = ctx.short_delta
    if abs(sp_delta - target_put) > SHORT_DELTA_TOLERANCE:
        return GradeResult(
            candidate=c, verdict="FAIL", failed_gate="delta_too_far",
            reason=(
                f"short put delta {sp_delta:.2f} outside |target − "
                f"{target_put:+.2f}| ≤ {SHORT_DELTA_TOLERANCE:.2f} "
                "(delta_too_far)"
            ),
            measurements={
                "short_put_delta": sp_delta,
                "target_put_delta": target_put,
                "tolerance": SHORT_DELTA_TOLERANCE,
                "short_call_delta": sc_delta,
            },
            provider_calls=ctx.calls_used,
            graded_at_iso=_now_iso(ctx),
        )
    if abs(sc_delta - target_call) > SHORT_DELTA_TOLERANCE:
        return GradeResult(
            candidate=c, verdict="FAIL", failed_gate="delta_too_far",
            reason=(
                f"short call delta {sc_delta:.2f} outside |target − "
                f"{target_call:+.2f}| ≤ {SHORT_DELTA_TOLERANCE:.2f} "
                "(delta_too_far)"
            ),
            measurements={
                "short_call_delta": sc_delta,
                "target_call_delta": target_call,
                "tolerance": SHORT_DELTA_TOLERANCE,
                "short_put_delta": sp_delta,
            },
            provider_calls=ctx.calls_used,
            graded_at_iso=_now_iso(ctx),
        )
    return None


async def _gate_term_structure(
    c: ParsedCandidate, ctx: _RunContext,
) -> GradeResult | None:
    """Term-structure check. Comparison direction VERBATIM from
    robinhood_joint_iron_condor.py:477-491 — `(front - back) <= max_diff`.
    Per Q2 design decision: if either leg is None → NEEDS_LIVE_DATA
    (diverges from strategy's fail-open, by intent — grader is
    operator-transparency)."""
    try:
        front = await _provider_call(
            ctx,
            ("atm_iv", c.symbol, ctx.target_dte, TARGET_DTE_TOLERANCE),
            "get_atm_iv_front",
            lambda: ctx.provider.get_atm_iv(
                c.symbol, ctx.target_dte, tolerance_days=TARGET_DTE_TOLERANCE,
            ),
        )
        back = await _provider_call(
            ctx,
            ("atm_iv", c.symbol, BACK_DTE, BACK_DTE_TOLERANCE),
            "get_atm_iv_back",
            lambda: ctx.provider.get_atm_iv(
                c.symbol, BACK_DTE, tolerance_days=BACK_DTE_TOLERANCE,
            ),
        )
    except _BudgetExhausted:
        return _budget_result(c, ctx, "term_structure")
    except _ProviderTimeout as t:
        return _timeout_result(c, ctx, "term_structure", t.key)

    if front is None or back is None:
        return GradeResult(
            candidate=c, verdict="NEEDS_LIVE_DATA",
            failed_gate="term_structure",
            reason=(
                "term-structure data unavailable "
                f"(provider degraded — front={front}, back={back})"
            ),
            measurements={
                "front_atm_iv": front, "back_atm_iv": back,
                "max_diff": ctx.term_structure_max_diff,
            },
            provider_calls=ctx.calls_used,
            graded_at_iso=_now_iso(ctx),
        )

    spread = float(front) - float(back)
    if spread > ctx.term_structure_max_diff:
        return GradeResult(
            candidate=c, verdict="FAIL", failed_gate="term_structure",
            reason=(
                f"term structure spread {spread:+.2f} > "
                f"{ctx.term_structure_max_diff:.2f}"
            ),
            measurements={
                "front_atm_iv": float(front),
                "back_atm_iv": float(back),
                "spread": spread,
                "max_diff": ctx.term_structure_max_diff,
            },
            provider_calls=ctx.calls_used,
            graded_at_iso=_now_iso(ctx),
        )
    return None


def _mid_or_none(contract: OptionContract) -> float | None:
    """Prefer mark, fall back to (bid+ask)/2, then None."""
    if contract.mark is not None:
        return float(contract.mark)
    if contract.bid is not None and contract.ask is not None:
        return (float(contract.bid) + float(contract.ask)) / 2.0
    return None


def _gate_credit(
    c: ParsedCandidate, ctx: _RunContext, chain: list[OptionContract],
) -> GradeResult | None:
    """Net credit = (short_put + short_call mids) − (long_put + long_call mids).
    Compare to min_credit_pct_of_width × wing_width."""
    sp = _find_contract(chain, "put", c.short_put_strike)
    lp = _find_contract(chain, "put", c.long_put_strike)
    sc = _find_contract(chain, "call", c.short_call_strike)
    lc = _find_contract(chain, "call", c.long_call_strike)
    mids = {
        "short_put": _mid_or_none(sp) if sp else None,
        "long_put":  _mid_or_none(lp) if lp else None,
        "short_call": _mid_or_none(sc) if sc else None,
        "long_call":  _mid_or_none(lc) if lc else None,
    }
    if any(v is None for v in mids.values()):
        return GradeResult(
            candidate=c, verdict="NEEDS_LIVE_DATA", failed_gate="credit",
            reason="leg mid prices unavailable",
            measurements={"leg_mids": mids},
            provider_calls=ctx.calls_used,
            graded_at_iso=_now_iso(ctx),
        )

    credit = (
        mids["short_put"] + mids["short_call"]
        - mids["long_put"] - mids["long_call"]
    )
    wing_width = ctx.wing_widths.get(c.symbol)
    if not wing_width or wing_width <= 0:
        return GradeResult(
            candidate=c, verdict="NEEDS_LIVE_DATA", failed_gate="credit",
            reason=f"no wing_width configured for {c.symbol}",
            measurements={"credit": credit, "wing_width": wing_width},
            provider_calls=ctx.calls_used,
            graded_at_iso=_now_iso(ctx),
        )

    pct_of_width = credit / wing_width
    if pct_of_width < ctx.min_credit_pct_of_width:
        return GradeResult(
            candidate=c, verdict="FAIL", failed_gate="credit",
            reason=(
                f"credit ${credit:.2f} = {pct_of_width*100:.0f}% of width "
                f"(need ≥{ctx.min_credit_pct_of_width*100:.0f}%)"
            ),
            measurements={
                "credit": credit,
                "pct_of_width": pct_of_width,
                "wing_width": wing_width,
                "min_credit_pct_of_width": ctx.min_credit_pct_of_width,
                "leg_mids": mids,
            },
            provider_calls=ctx.calls_used,
            graded_at_iso=_now_iso(ctx),
        )
    return None


def _budget_result(
    c: ParsedCandidate, ctx: _RunContext, failed_gate: str,
) -> GradeResult:
    return GradeResult(
        candidate=c, verdict="NEEDS_LIVE_DATA", failed_gate=failed_gate,
        reason=f"call budget exhausted ({ctx.call_budget} calls used)",
        measurements={"call_budget": ctx.call_budget},
        provider_calls=ctx.calls_used,
        graded_at_iso=_now_iso(ctx),
    )


def _timeout_result(
    c: ParsedCandidate, ctx: _RunContext,
    failed_gate: str, op_key: str,
) -> GradeResult:
    return GradeResult(
        candidate=c, verdict="NEEDS_LIVE_DATA", failed_gate=failed_gate,
        reason=f"provider timeout {ctx.per_call_timeout}s on {op_key}",
        measurements={"timeout_sec": ctx.per_call_timeout, "op": op_key},
        provider_calls=ctx.calls_used,
        graded_at_iso=_now_iso(ctx),
    )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

async def _grade_row(
    c: ParsedCandidate, ctx: _RunContext,
) -> GradeResult:
    """Grade one candidate through the 8 gates sequentially."""
    # Parse-error short-circuit (universe still cheaper, but parse errors
    # mean we can't meaningfully test rules — surface immediately).
    if c.parse_errors:
        return GradeResult(
            candidate=c, verdict="PARSE_ERROR", failed_gate=None,
            reason=f"parse errors: {', '.join(c.parse_errors)}",
            measurements={},
            provider_calls=ctx.calls_used,
            graded_at_iso=_now_iso(ctx),
        )

    # Expired-row short-circuit (no provider call).
    today = ctx.clock().date()
    if c.expiration is not None and c.expiration < today:
        return GradeResult(
            candidate=c, verdict="FAIL", failed_gate="expiration_past",
            reason=f"expiration {c.expiration} is in the past",
            measurements={"pasted_expiration": c.expiration.isoformat()},
            provider_calls=ctx.calls_used,
            graded_at_iso=_now_iso(ctx),
        )

    # Gate 1 — universe (no provider calls).
    r = _gate_universe(c, ctx)
    if r is not None:
        return r

    # All subsequent gates need the chain — fetch once and pass through.
    try:
        chain = await _provider_call(
            ctx,
            ("chain", c.symbol, c.expiration.isoformat()),
            "get_option_chain",
            lambda: ctx.provider.get_option_chain(c.symbol, c.expiration),
        )
    except _BudgetExhausted:
        return _budget_result(c, ctx, "expiration_on_chain")
    except _ProviderTimeout as t:
        return _timeout_result(c, ctx, "expiration_on_chain", t.key)

    r = await _gate_expiration_on_chain(c, ctx, chain)
    if r is not None:
        return r
    r = _gate_dte(c, ctx, chain)
    if r is not None:
        return r
    r = await _gate_ivr(c, ctx)
    if r is not None:
        return r
    r = _gate_strikes_exist(c, ctx, chain)
    if r is not None:
        return r
    r = await _gate_delta_proximity(c, ctx, chain)
    if r is not None:
        return r
    r = await _gate_term_structure(c, ctx)
    if r is not None:
        return r
    r = _gate_credit(c, ctx, chain)
    if r is not None:
        return r

    # All gates passed.
    return GradeResult(
        candidate=c, verdict="PASS", failed_gate=None,
        reason="all gates passed against live data",
        measurements={"symbol": c.symbol, "expiration": c.expiration.isoformat()},
        provider_calls=ctx.calls_used,
        graded_at_iso=_now_iso(ctx),
    )


async def grade_paste(
    text: str, *,
    strategy: _StrategyLike,
    provider: MarketDataProvider,
    clock=None,
    per_call_timeout: float = DEFAULT_PER_CALL_TIMEOUT,
    call_budget: int = DEFAULT_CALL_BUDGET,
    logger: _LoggerLike | None = None,
    row_cap: int = DEFAULT_ROW_CAP,
    division: str = "robinhood_joint",
    strategy_slug: str = "robinhood_joint_iron_condor",
) -> GraderRunResult:
    """Top-level entry. Parse, snapshot cfg, grade each row, log summary.

    `strategy` must expose `universe`, `wing_widths`, and `cfg(dotted)` —
    duck-typed against `RobinhoodJointIronCondorAgent`. The grader does
    NOT import the strategy class, preserving the no-execution invariant.

    `division` and `strategy_slug` stamp the `ic_grader_run` audit row;
    default to Robinhood Joint for backwards compatibility. Tasty Options
    passes "tasty_options" / "tasty_options_iron_condor".
    """
    if clock is None:
        clock = lambda: datetime.now(timezone.utc)

    # 1. Parse the paste.
    candidates, paste_warnings = parse_paste(
        text, row_cap=row_cap, clock=clock,
    )
    now_iso = clock().isoformat(timespec="seconds")

    if not candidates:
        # Paste rejected (e.g. row-cap) — write the audit row and return.
        result = GraderRunResult(
            now_iso=now_iso,
            config_mtime_iso="n/a",
            config_version_hash="n/a",
            rows=(),
            paste_warnings=tuple(paste_warnings),
            summary={"rows_pasted": 0},
            provider_calls_total=0,
        )
        _emit_summary_audit(
            logger, result, had_expired=False,
            division=division, strategy_slug=strategy_slug,
        )
        return result

    # 2. Snapshot cfg. Stamp ctx with provider + budget + clock.
    ctx = _snapshot_cfg(strategy, clock=clock)
    ctx.provider = provider
    ctx.per_call_timeout = per_call_timeout
    ctx.call_budget = call_budget

    # 3. Grade each row sequentially (within-row gates are inherently
    #    sequential; across-row concurrency is intentionally NOT used in
    #    v1 — provider's 60s TTL cache already dedupes (symbol,exp)
    #    fetches, and sequential grading makes the call-budget account
    #    deterministic and testable).
    rows: list[GradeResult] = []
    for c in candidates:
        r = await _grade_row(c, ctx)
        # Tag the row with watchlist_membership when meaningful (parseable
        # symbol present). Strict-universe runs (RH Joint) get "in" on
        # passes / FAILs that reached past gate 1 with "off" recorded on
        # gate-1 fails; non-strict runs (Tasty Options) get the full
        # in/off signal regardless of verdict — that's the whole point
        # of the watchlist semantics.
        if c.symbol is not None and r.verdict != "PARSE_ERROR":
            membership = "in" if c.symbol in ctx.universe else "off"
            r = dataclasses.replace(
                r,
                measurements={**r.measurements, "watchlist_membership": membership},
            )
        rows.append(r)

    # 4. Summary.
    summary = _summarize(rows)
    had_expired = any(
        c.expiration is not None and c.expiration < clock().date()
        for c in candidates
    )

    result = GraderRunResult(
        now_iso=now_iso,
        config_mtime_iso=ctx.config_mtime_iso,
        config_version_hash=ctx.config_version_hash,
        rows=tuple(rows),
        paste_warnings=tuple(paste_warnings),
        summary=summary,
        provider_calls_total=ctx.calls_used,
    )

    # 5. Audit summary (no raw paste content).
    _emit_summary_audit(
        logger, result, had_expired=had_expired,
        division=division, strategy_slug=strategy_slug,
    )

    # 6. Stdout log line.
    log.info(
        "ic_grader: %d pasted · %d passed · %d failed · %d needs-data "
        "· %d unparseable · %d calls",
        summary["rows_pasted"], summary["rows_passed"],
        summary["rows_failed"], summary["rows_needs_data"],
        summary["rows_unparseable"], ctx.calls_used,
    )
    return result


def _summarize(rows: list[GradeResult]) -> dict[str, Any]:
    breakdown: dict[str, int] = {}
    counts = {"PASS": 0, "FAIL": 0, "NEEDS_LIVE_DATA": 0, "PARSE_ERROR": 0}
    for r in rows:
        counts[r.verdict] += 1
        if r.failed_gate:
            breakdown[r.failed_gate] = breakdown.get(r.failed_gate, 0) + 1
    return {
        "rows_pasted": len(rows),
        "rows_passed": counts["PASS"],
        "rows_failed": counts["FAIL"],
        "rows_needs_data": counts["NEEDS_LIVE_DATA"],
        "rows_unparseable": counts["PARSE_ERROR"],
        "failure_breakdown": breakdown,
    }


def _emit_summary_audit(
    logger: _LoggerLike | None,
    result: GraderRunResult,
    *,
    had_expired: bool,
    division: str = "robinhood_joint",
    strategy_slug: str = "robinhood_joint_iron_condor",
) -> None:
    if logger is None:
        return
    payload = {
        "strategy": strategy_slug,
        "division": division,
        "rows_pasted": result.summary.get("rows_pasted", 0),
        "rows_passed": result.summary.get("rows_passed", 0),
        "rows_failed": result.summary.get("rows_failed", 0),
        "rows_needs_data": result.summary.get("rows_needs_data", 0),
        "rows_unparseable": result.summary.get("rows_unparseable", 0),
        "failure_breakdown": result.summary.get("failure_breakdown", {}),
        "provider_calls_used": result.provider_calls_total,
        "had_expired_dates": had_expired,
        "config_version_hash": result.config_version_hash,
    }
    try:
        logger.log_event(
            actor="ic_candidate_grader",
            kind="ic_grader_run",
            payload=payload,
        )
    except Exception as exc:
        log.warning("ic_grader: audit emit failed: %s", exc)
