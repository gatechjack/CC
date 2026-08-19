"""MACE config — frozen dataclasses over config/mace.yaml (plan T1).

Split: `config/strategies.yaml` keeps ONLY the hot kill-switches
(enabled / auto_execute); EVERYTHING else lives in `config/mace.yaml`,
loaded ONCE at boot into a frozen MaceConfig, restart-gated.

Fail-fast: ANY invalid field aborts the load with EVERY violation listed
(one pass, fix-all). The sha256 of the raw file bytes travels on the
object as `config_hash` — logged at boot and shown on /mace so the
operator can prove which config is live.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import time as dtime
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import yaml

_ENFORCEMENT_MODES = ("off", "pause_entries", "halt_flat")
_WEEKDAYS = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")
_ACCT_RE = re.compile(r"^\d+$")


@dataclass(frozen=True)
class EntryConfig:
    eval_time_et: str
    entry_cutoff_et: str
    dte_min: int
    dte_max: int
    short_delta_target: float
    short_delta_band: tuple[float, float]
    credit_floor_pct_of_width: float
    risk_band_min_per_width_usd: float   # min max-risk = this * width_dollars (width-scaled)
    risk_band_max_usd: float             # absolute max-risk ceiling
    enforce_risk_band: bool
    ivr_floor: float
    weekly_new_rungs_per_symbol: int
    max_rungs_per_symbol: int
    stop_cooldown_sessions: int
    ibit_overflow_cap: int
    overflow_max_per_symbol_session: int
    strike_band_pct: float               # chain() fetch bound: +/- this * spot (rh_broker)


@dataclass(frozen=True)
class SizingConfig:
    rung_risk_pct: float
    deployment_target_pct: float
    equity_snapshot_time_et: str


@dataclass(frozen=True)
class ManagementConfig:
    check_interval_sec: int
    window_et: tuple[str, str]
    pt_pct_of_credit: float
    stop_multiple: float
    time_exit_dte: int
    time_exit_at_et: str
    exdiv_guard_sessions: int


@dataclass(frozen=True)
class ExecutionConfig:
    entry_start_offset_usd: float
    entry_tick_usd: float
    entry_fill_wait_sec: int
    entry_max_attempts: int
    exit_fill_wait_sec: int
    exit_max_attempts: int
    exit_hard_ceiling_mult_of_width: float


@dataclass(frozen=True)
class BreakersConfig:
    day_loss_pct: float
    week_loss_pct: float
    hwm_soft_pct: float
    hwm_hard_pct: float
    breaker_enforcement: str  # off | pause_entries | halt_flat (SHIPS "off")


@dataclass(frozen=True)
class DataConfig:
    ivr_source: str
    iv_snapshot_daily: bool
    calendar_refresh_weekday: str


@dataclass(frozen=True)
class NotificationsConfig:
    daily_summary_time_et: str


@dataclass(frozen=True)
class SymbolConfig:
    enabled: bool
    width_dollars: float
    blackout_event_types: tuple[str, ...]
    exdiv_guard: bool
    fallback_width_dollars: float | None = None
    overflow_only: bool = False


@dataclass(frozen=True)
class MaceConfig:
    account_number: str
    acknowledge_foreign_positions: bool
    universe: tuple[str, ...]
    max_contracts: int
    entry: EntryConfig
    sizing: SizingConfig
    management: ManagementConfig
    execution: ExecutionConfig
    breakers: BreakersConfig
    data: DataConfig
    notifications: NotificationsConfig
    symbols: Mapping[str, SymbolConfig]
    config_hash: str
    source_path: str


def _symbols_with_ex_div_data(path: str | Path) -> set[str] | None:
    """Symbols with >=1 entry in the ex-dividend calendar YAML (schema owned by
    data/ex_dividend_calendar.py). Returns None if the file can't be read.

    Feeds the boot gate below (Board ruling 2026-08-09, Checkpoint 1): a
    zero-HITL engine must NEVER enable a symbol whose exdiv guard is on but has
    no ex-div dates to close against — the guard would be silently inert (false
    protection). EWZ/FXI are structured-empty until real dates are sourced, so
    enabling either without dates fails the load."""
    try:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (FileNotFoundError, OSError, yaml.YAMLError):
        return None
    if not isinstance(data, dict):
        return None
    return {
        str(e["symbol"]).upper()
        for e in (data.get("ex_dividends") or [])
        if isinstance(e, dict) and e.get("symbol")
    }


def load_mace_config(
    path: str | Path = "config/mace.yaml",
    *,
    exdiv_calendar_path: str | Path = "config/ex_dividend_calendar.yaml",
) -> MaceConfig:
    """Load + validate. Raises ValueError listing EVERY violation."""
    p = Path(path)
    raw = p.read_bytes()
    cfg_hash = hashlib.sha256(raw).hexdigest()
    data = yaml.safe_load(raw.decode("utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{p}: top level is not a mapping")

    errs: list[str] = []

    def sect(name: str) -> dict:
        v = data.get(name)
        if not isinstance(v, dict):
            errs.append(f"{name}: missing or not a mapping")
            return {}
        return v

    def num(d: dict, key: str, ctx: str, *, typ=float, lo=None, hi=None,
            lo_excl: bool = False, hi_excl: bool = False):
        v = d.get(key)
        if v is None:
            errs.append(f"{ctx}.{key}: missing")
            return None
        if isinstance(v, bool):  # bool is an int subclass — reject explicitly
            errs.append(f"{ctx}.{key}: expected {typ.__name__}, got bool")
            return None
        try:
            v = typ(v)
        except (TypeError, ValueError):
            errs.append(f"{ctx}.{key}: not a {typ.__name__}: {v!r}")
            return None
        if lo is not None and (v <= lo if lo_excl else v < lo):
            errs.append(f"{ctx}.{key}: {v} below {'open' if lo_excl else ''} bound {lo}")
        if hi is not None and (v >= hi if hi_excl else v > hi):
            errs.append(f"{ctx}.{key}: {v} above {'open' if hi_excl else ''} bound {hi}")
        return v

    def flag(d: dict, key: str, ctx: str):
        v = d.get(key)
        if not isinstance(v, bool):
            errs.append(f"{ctx}.{key}: missing or not a bool: {v!r}")
            return None
        return v

    def hhmm(d: dict, key: str, ctx: str):
        v = d.get(key)
        if not isinstance(v, str):
            errs.append(f"{ctx}.{key}: missing or not an HH:MM string: {v!r}")
            return None
        try:
            dtime.fromisoformat(v)
        except ValueError:
            errs.append(f"{ctx}.{key}: not a valid HH:MM time: {v!r}")
            return None
        return v

    def pair(d: dict, key: str, ctx: str, *, typ=float):
        v = d.get(key)
        if not isinstance(v, (list, tuple)) or len(v) != 2:
            errs.append(f"{ctx}.{key}: expected a 2-item list: {v!r}")
            return None
        try:
            lo, hi = typ(v[0]), typ(v[1])
        except (TypeError, ValueError):
            errs.append(f"{ctx}.{key}: non-numeric items: {v!r}")
            return None
        if lo >= hi:
            errs.append(f"{ctx}.{key}: lower bound {lo} must be < upper {hi}")
        return (lo, hi)

    # ── top level ─────────────────────────────────────────────────────
    acct = data.get("account_number")
    if not isinstance(acct, str) or not _ACCT_RE.match(acct):
        errs.append(f"account_number: must be a digits-only string, got {acct!r}")
    ack_foreign = flag(data, "acknowledge_foreign_positions", "<top>")
    max_contracts = num(data, "max_contracts", "<top>", typ=int, lo=1)

    uni_raw = data.get("universe")
    universe: tuple[str, ...] = ()
    if not isinstance(uni_raw, list) or not uni_raw:
        errs.append(f"universe: must be a non-empty list, got {uni_raw!r}")
    else:
        universe = tuple(str(s).upper() for s in uni_raw)
        if len(set(universe)) != len(universe):
            errs.append("universe: contains duplicates")

    # ── sections ──────────────────────────────────────────────────────
    e = sect("entry")
    entry = None
    eval_t = hhmm(e, "eval_time_et", "entry")
    cutoff_t = hhmm(e, "entry_cutoff_et", "entry")
    if eval_t and cutoff_t and eval_t >= cutoff_t:
        errs.append(f"entry: eval_time_et {eval_t} must be before entry_cutoff_et {cutoff_t}")
    dte_min = num(e, "dte_min", "entry", typ=int, lo=1)
    dte_max = num(e, "dte_max", "entry", typ=int, lo=1)
    if dte_min is not None and dte_max is not None and dte_min > dte_max:
        errs.append(f"entry: dte_min {dte_min} > dte_max {dte_max}")
    delta_target = num(e, "short_delta_target", "entry", lo=0.0, hi=1.0,
                       lo_excl=True, hi_excl=True)
    delta_band = pair(e, "short_delta_band", "entry")
    if delta_band and delta_target is not None and not (
        delta_band[0] <= delta_target <= delta_band[1]
    ):
        errs.append(
            f"entry: short_delta_target {delta_target} outside band {delta_band}"
        )
    credit_floor = num(e, "credit_floor_pct_of_width", "entry", lo=0.0, hi=1.0,
                       lo_excl=True, hi_excl=True)
    rb_min_per_width = num(e, "risk_band_min_per_width_usd", "entry", lo=0.0, lo_excl=True)
    rb_max = num(e, "risk_band_max_usd", "entry", lo=0.0, lo_excl=True)
    enforce_rb = flag(e, "enforce_risk_band", "entry")
    ivr_floor = num(e, "ivr_floor", "entry", lo=0.0, hi=100.0)
    weekly = num(e, "weekly_new_rungs_per_symbol", "entry", typ=int, lo=1)
    max_rungs = num(e, "max_rungs_per_symbol", "entry", typ=int, lo=1)
    cooldown = num(e, "stop_cooldown_sessions", "entry", typ=int, lo=0)
    ibit_cap = num(e, "ibit_overflow_cap", "entry", typ=int, lo=0)
    overflow_max = num(e, "overflow_max_per_symbol_session", "entry", typ=int, lo=0)
    strike_band = num(e, "strike_band_pct", "entry", lo=0.0, hi=1.0, lo_excl=True)

    s = sect("sizing")
    rung_risk = num(s, "rung_risk_pct", "sizing", lo=0.0, hi=1.0, lo_excl=True)
    deploy_target = num(s, "deployment_target_pct", "sizing", lo=0.0, hi=1.0,
                        lo_excl=True)
    snap_t = hhmm(s, "equity_snapshot_time_et", "sizing")
    if snap_t and eval_t and snap_t >= eval_t:
        errs.append(
            f"sizing: equity_snapshot_time_et {snap_t} must be before "
            f"entry.eval_time_et {eval_t}"
        )

    m = sect("management")
    check_iv = num(m, "check_interval_sec", "management", typ=int, lo=10)
    window = m.get("window_et")
    win: tuple[str, str] | None = None
    if (
        isinstance(window, (list, tuple)) and len(window) == 2
        and all(isinstance(x, str) for x in window)
    ):
        try:
            dtime.fromisoformat(window[0]); dtime.fromisoformat(window[1])
            if window[0] >= window[1]:
                errs.append(f"management.window_et: start {window[0]} >= end {window[1]}")
            win = (window[0], window[1])
        except ValueError:
            errs.append(f"management.window_et: invalid HH:MM values: {window!r}")
    else:
        errs.append(f"management.window_et: expected [HH:MM, HH:MM], got {window!r}")
    pt_pct = num(m, "pt_pct_of_credit", "management", lo=0.0, hi=1.0,
                 lo_excl=True, hi_excl=True)
    stop_mult = num(m, "stop_multiple", "management", lo=1.0, lo_excl=True)
    time_exit_dte = num(m, "time_exit_dte", "management", typ=int, lo=1)
    time_exit_at = hhmm(m, "time_exit_at_et", "management")
    exdiv_sessions = num(m, "exdiv_guard_sessions", "management", typ=int, lo=0)

    x = sect("execution")
    start_off = num(x, "entry_start_offset_usd", "execution", lo=0.0)
    tick = num(x, "entry_tick_usd", "execution", lo=0.0, lo_excl=True)
    entry_wait = num(x, "entry_fill_wait_sec", "execution", typ=int, lo=1)
    entry_attempts = num(x, "entry_max_attempts", "execution", typ=int, lo=1)
    exit_wait = num(x, "exit_fill_wait_sec", "execution", typ=int, lo=1)
    exit_attempts = num(x, "exit_max_attempts", "execution", typ=int, lo=1)
    ceiling = num(x, "exit_hard_ceiling_mult_of_width", "execution", lo=0.0,
                  lo_excl=True)

    b = sect("breakers")
    day_loss = num(b, "day_loss_pct", "breakers", lo=0.0, hi=1.0, lo_excl=True)
    week_loss = num(b, "week_loss_pct", "breakers", lo=0.0, hi=1.0, lo_excl=True)
    hwm_soft = num(b, "hwm_soft_pct", "breakers", lo=0.0, hi=1.0, lo_excl=True)
    hwm_hard = num(b, "hwm_hard_pct", "breakers", lo=0.0, hi=1.0, lo_excl=True)
    if hwm_soft is not None and hwm_hard is not None and hwm_soft <= hwm_hard:
        errs.append(
            f"breakers: hwm_soft_pct {hwm_soft} must be > hwm_hard_pct {hwm_hard}"
        )
    enforcement = b.get("breaker_enforcement")
    if enforcement not in _ENFORCEMENT_MODES:
        errs.append(
            f"breakers.breaker_enforcement: {enforcement!r} not in {_ENFORCEMENT_MODES}"
        )

    d = sect("data")
    ivr_source = d.get("ivr_source")
    if not isinstance(ivr_source, str) or not ivr_source:
        errs.append(f"data.ivr_source: missing/empty: {ivr_source!r}")
    iv_daily = flag(d, "iv_snapshot_daily", "data")
    refresh_wd = d.get("calendar_refresh_weekday")
    if refresh_wd not in _WEEKDAYS:
        errs.append(
            f"data.calendar_refresh_weekday: {refresh_wd!r} not in {_WEEKDAYS}"
        )

    n = sect("notifications")
    summary_t = hhmm(n, "daily_summary_time_et", "notifications")

    # ── symbols ───────────────────────────────────────────────────────
    syms_d = sect("symbols")
    symbols: dict[str, SymbolConfig] = {}
    for key, sv in syms_d.items():
        ctx = f"symbols.{key}"
        if not isinstance(key, str) or key != key.upper():
            errs.append(f"{ctx}: symbol keys must be UPPERCASE")
        if not isinstance(sv, dict):
            errs.append(f"{ctx}: not a mapping")
            continue
        s_enabled = flag(sv, "enabled", ctx)
        width = num(sv, "width_dollars", ctx, lo=0.0, lo_excl=True)
        fallback = None
        if "fallback_width_dollars" in sv:
            fallback = num(sv, "fallback_width_dollars", ctx, lo=0.0, lo_excl=True)
            if fallback is not None and width is not None and fallback >= width:
                errs.append(
                    f"{ctx}: fallback_width_dollars {fallback} must be < "
                    f"width_dollars {width}"
                )
        bo = sv.get("blackout_event_types")
        blackout: tuple[str, ...] = ()
        if isinstance(bo, list):
            blackout = tuple(str(t).upper() for t in bo)
        else:
            errs.append(f"{ctx}.blackout_event_types: expected a list, got {bo!r}")
        exdiv_g = flag(sv, "exdiv_guard", ctx)
        overflow_only = bool(sv.get("overflow_only", False))
        symbols[str(key).upper()] = SymbolConfig(
            enabled=bool(s_enabled),
            width_dollars=float(width or 0.0),
            blackout_event_types=blackout,
            exdiv_guard=bool(exdiv_g),
            fallback_width_dollars=fallback,
            overflow_only=overflow_only,
        )

    for sym in universe:
        sc = symbols.get(sym)
        if sc is None:
            errs.append(f"universe: {sym} has no symbols.{sym} block")
            continue
        if not sc.enabled:
            errs.append(f"universe: {sym} is listed but symbols.{sym}.enabled is false")
        if sc.overflow_only:
            errs.append(
                f"universe: {sym} is overflow_only and can NEVER be a primary "
                f"(OQ-3 ratified — remove it from universe)"
            )

    # Ex-div guard boot gate (Board ruling 2026-08-09, Checkpoint 1): any symbol
    # that is BOTH enabled and exdiv_guard-on must have real ex-div dates in the
    # calendar — else the position-closing guard is silently inert. Applies to
    # every enabled symbol (not just the universe). Sourcing dates for EWZ/FXI is
    # an expansion-runbook prerequisite for enabling them.
    exdiv_syms = _symbols_with_ex_div_data(exdiv_calendar_path)
    for sym, sc in symbols.items():
        if not (sc.enabled and sc.exdiv_guard):
            continue
        if exdiv_syms is None:
            errs.append(
                f"symbols.{sym}: enabled + exdiv_guard but the ex-div calendar "
                f"{exdiv_calendar_path} is unreadable — cannot validate the guard"
            )
        elif sym not in exdiv_syms:
            errs.append(
                f"symbols.{sym}: enabled + exdiv_guard but the ex-div calendar has "
                f"NO entries for {sym} — a zero-HITL engine must not run a "
                f"position-closing guard with no dates; source real ex-div dates "
                f"before enabling (expansion-runbook prerequisite)"
            )

    if errs:
        raise ValueError(
            f"{p} INVALID — fail-fast, fix ALL of:\n  - " + "\n  - ".join(errs)
        )

    return MaceConfig(
        account_number=str(acct),
        acknowledge_foreign_positions=bool(ack_foreign),
        universe=universe,
        max_contracts=int(max_contracts),
        entry=EntryConfig(
            eval_time_et=eval_t, entry_cutoff_et=cutoff_t,
            dte_min=int(dte_min), dte_max=int(dte_max),
            short_delta_target=float(delta_target),
            short_delta_band=delta_band,
            credit_floor_pct_of_width=float(credit_floor),
            risk_band_min_per_width_usd=float(rb_min_per_width),
            risk_band_max_usd=float(rb_max),
            enforce_risk_band=bool(enforce_rb),
            ivr_floor=float(ivr_floor),
            weekly_new_rungs_per_symbol=int(weekly),
            max_rungs_per_symbol=int(max_rungs),
            stop_cooldown_sessions=int(cooldown),
            ibit_overflow_cap=int(ibit_cap),
            overflow_max_per_symbol_session=int(overflow_max),
            strike_band_pct=float(strike_band),
        ),
        sizing=SizingConfig(
            rung_risk_pct=float(rung_risk),
            deployment_target_pct=float(deploy_target),
            equity_snapshot_time_et=snap_t,
        ),
        management=ManagementConfig(
            check_interval_sec=int(check_iv),
            window_et=win,
            pt_pct_of_credit=float(pt_pct),
            stop_multiple=float(stop_mult),
            time_exit_dte=int(time_exit_dte),
            time_exit_at_et=time_exit_at,
            exdiv_guard_sessions=int(exdiv_sessions),
        ),
        execution=ExecutionConfig(
            entry_start_offset_usd=float(start_off),
            entry_tick_usd=float(tick),
            entry_fill_wait_sec=int(entry_wait),
            entry_max_attempts=int(entry_attempts),
            exit_fill_wait_sec=int(exit_wait),
            exit_max_attempts=int(exit_attempts),
            exit_hard_ceiling_mult_of_width=float(ceiling),
        ),
        breakers=BreakersConfig(
            day_loss_pct=float(day_loss),
            week_loss_pct=float(week_loss),
            hwm_soft_pct=float(hwm_soft),
            hwm_hard_pct=float(hwm_hard),
            breaker_enforcement=str(enforcement),
        ),
        data=DataConfig(
            ivr_source=str(ivr_source),
            iv_snapshot_daily=bool(iv_daily),
            calendar_refresh_weekday=str(refresh_wd),
        ),
        notifications=NotificationsConfig(daily_summary_time_et=summary_t),
        symbols=MappingProxyType(symbols),
        config_hash=cfg_hash,
        source_path=str(p),
    )
