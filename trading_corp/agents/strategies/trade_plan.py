"""Adaptive trade-plan builder for BitUnix futures.

Given entry + ATR + recent swings + HTF S/R + fees, produces a 3-leg
TradePlan: structure-preferred SL with ATR fallback, fee-aware TP1,
1R-or-snap TP2, fixed-R TP3. Returns a skip plan when the trade
shouldn't be taken (swing too close, or fees too high for the risk).

This is the strategy-layer translation between "the score+PA+HTF
gates have decided this signal fires" and "here are the concrete
prices to put on the order." Pure function — caller is responsible
for sourcing inputs (swings from swing.py; resistance/support from
levels.py; ATR from LiveBarCache).

The runtime SL lifecycle (move-to-BE after TP1, move-to-TP1 after
TP2, Chandelier trail post-TP2) is handled by a separate stateless
reconciler — see `bitunix_position_reconciler` (trade-plan PR 5).
TradePlan only describes the initial placement.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import isnan
from typing import Literal

__all__ = ["FeeConfig", "StrategyConfig", "TradePlan", "build_trade_plan"]


@dataclass(frozen=True)
class FeeConfig:
    """Fee schedule + slippage assumption. All values are FRACTIONS
    (0.0004 == 0.04%), matching the codebase convention for *_pct fields.

    Defaults are BitUnix Futures VIP3 with Experience Card as of 2026-05-15.
    """
    taker_fee_pct: float = 0.0004
    maker_fee_pct: float = 0.00014
    slippage_pct: float = 0.00005
    entry_is_taker: bool = True
    tp_is_maker: bool = False

    def round_trip_cost_pct(self) -> float:
        """Total round-trip cost as a fraction of price (both fees +
        slippage on both legs of the round-trip)."""
        entry_fee = self.taker_fee_pct if self.entry_is_taker else self.maker_fee_pct
        exit_fee = self.maker_fee_pct if self.tp_is_maker else self.taker_fee_pct
        return entry_fee + exit_fee + 2 * self.slippage_pct

    @classmethod
    def from_dict(cls, fees_block: dict | None) -> "FeeConfig":
        """Parse the bitunix_futures.fees YAML block. Falls back to defaults
        for any missing key. Pass {} or None to get pure defaults.
        """
        f = fees_block or {}
        d = cls()
        return cls(
            taker_fee_pct=float(f.get("taker_pct", d.taker_fee_pct)),
            maker_fee_pct=float(f.get("maker_pct", d.maker_fee_pct)),
            slippage_pct=float(f.get("slippage_pct", d.slippage_pct)),
            entry_is_taker=bool(f.get("entry_is_taker", d.entry_is_taker)),
            tp_is_maker=bool(f.get("tp_is_maker", d.tp_is_maker)),
        )


@dataclass(frozen=True)
class StrategyConfig:
    """All tunable knobs for the trade-plan builder. Defaults track the
    YAML block documented in trading_corp_bitunix_strategy_gaps.md."""
    # SL placement (ATR-multiple bounds for multi-symbol generalization)
    min_stop_atr_mult: float = 0.5
    max_stop_atr_mult: float = 2.5
    atr_multiplier: float = 1.5
    swing_buffer_pct: float = 0.0005
    swing_n: int = 2
    swing_max_lookback: int = 30

    # TP plan
    tp1_r_target: float = 0.5
    tp1_min_profit_multiplier: float = 2.0
    tp1_qty_fraction: float = 0.25
    tp2_r_default: float = 1.0
    tp2_qty_fraction: float = 0.50
    tp3_r_target: float = 2.5
    tp3_qty_fraction: float = 0.25

    # HTF level snap (TP2)
    htf_minutes: int = 15
    htf_lookback_bars: int = 40
    resistance_min_r: float = 0.5
    resistance_max_r: float = 1.3
    resistance_buffer_pct: float = 0.0005

    @classmethod
    def from_dict(cls, tp_block: dict | None) -> "StrategyConfig":
        """Parse the bitunix_futures.trade_plan YAML block. Falls back to
        defaults for any missing key. Pass {} or None to get pure defaults.
        """
        b = tp_block or {}
        d = cls()
        return cls(
            min_stop_atr_mult=float(b.get("min_stop_atr_mult", d.min_stop_atr_mult)),
            max_stop_atr_mult=float(b.get("max_stop_atr_mult", d.max_stop_atr_mult)),
            atr_multiplier=float(b.get("atr_multiplier", d.atr_multiplier)),
            swing_buffer_pct=float(b.get("swing_buffer_pct", d.swing_buffer_pct)),
            swing_n=int(b.get("swing_n", d.swing_n)),
            swing_max_lookback=int(b.get("swing_max_lookback", d.swing_max_lookback)),
            tp1_r_target=float(b.get("tp1_r_target", d.tp1_r_target)),
            tp1_min_profit_multiplier=float(b.get("tp1_min_profit_multiplier", d.tp1_min_profit_multiplier)),
            tp1_qty_fraction=float(b.get("tp1_qty_fraction", d.tp1_qty_fraction)),
            tp2_r_default=float(b.get("tp2_r_default", d.tp2_r_default)),
            tp2_qty_fraction=float(b.get("tp2_qty_fraction", d.tp2_qty_fraction)),
            tp3_r_target=float(b.get("tp3_r_target", d.tp3_r_target)),
            tp3_qty_fraction=float(b.get("tp3_qty_fraction", d.tp3_qty_fraction)),
            htf_minutes=int(b.get("htf_minutes", d.htf_minutes)),
            htf_lookback_bars=int(b.get("htf_lookback_bars", d.htf_lookback_bars)),
            resistance_min_r=float(b.get("resistance_min_r", d.resistance_min_r)),
            resistance_max_r=float(b.get("resistance_max_r", d.resistance_max_r)),
            resistance_buffer_pct=float(b.get("resistance_buffer_pct", d.resistance_buffer_pct)),
        )


@dataclass(frozen=True)
class TradePlan:
    """Concrete trade-plan output. Check `should_trade` before using prices —
    when `skip_reason` is set, the price fields are filled with whatever the
    builder computed up to the skip point but should NOT drive orders.
    """
    entry: float
    stop_loss: float
    tp1: float
    tp2: float
    tp3: float
    sl_method: str          # "swing" | "atr_fallback" | ""
    tp2_method: str         # "default_1r" | "snap_resistance" | "snap_support" | ""
    risk_per_unit: float    # |entry - stop_loss|
    tp1_qty_fraction: float = 0.25
    tp2_qty_fraction: float = 0.50
    tp3_qty_fraction: float = 0.25
    skip_reason: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def should_trade(self) -> bool:
        return self.skip_reason is None


def _skip(entry: float, reason: str, notes: tuple[str, ...] = ()) -> TradePlan:
    return TradePlan(
        entry=entry, stop_loss=0.0, tp1=0.0, tp2=0.0, tp3=0.0,
        sl_method="", tp2_method="", risk_per_unit=0.0,
        skip_reason=reason, notes=notes,
    )


def build_trade_plan(
    entry: float,
    side: Literal["buy", "sell"],
    atr: float,
    swing_low: float | None,
    swing_high: float | None,
    resistance: float | None,
    support: float | None,
    cfg: StrategyConfig,
    fees: FeeConfig,
) -> TradePlan:
    """Produce a TradePlan or a skip plan. Side determines which swing /
    HTF level applies: 'buy' uses swing_low + resistance; 'sell' uses
    swing_high + support."""
    if side not in ("buy", "sell"):
        raise ValueError(f"side must be 'buy' or 'sell', got {side!r}")
    if entry <= 0 or isnan(entry):
        return _skip(entry, "invalid_entry")
    if atr <= 0 or isnan(atr):
        return _skip(entry, "invalid_atr")

    is_buy = side == "buy"

    # ── Stop loss: swing-preferred, ATR fallback ──
    relevant_swing = swing_low if is_buy else swing_high
    if relevant_swing is not None and (
        (is_buy and relevant_swing >= entry)
        or (not is_buy and relevant_swing <= entry)
    ):
        relevant_swing = None  # swing on wrong side of entry — discard

    sl_method = "atr_fallback"
    stop_distance = cfg.atr_multiplier * atr

    if relevant_swing is not None:
        buf = cfg.swing_buffer_pct * entry
        if is_buy:
            swing_sl = relevant_swing - buf
            swing_distance = entry - swing_sl
        else:
            swing_sl = relevant_swing + buf
            swing_distance = swing_sl - entry

        if swing_distance < cfg.min_stop_atr_mult * atr:
            return _skip(entry, "swing_too_close")
        if swing_distance <= cfg.max_stop_atr_mult * atr:
            stop_distance = swing_distance
            sl_method = "swing"

    if stop_distance <= 0:
        return _skip(entry, "zero_risk")

    stop_loss = entry - stop_distance if is_buy else entry + stop_distance
    risk_per_unit = stop_distance

    # ── TP1: max(target * R, fee_floor) ──
    fee_cost_per_unit = fees.round_trip_cost_pct() * entry
    tp1_target_distance = cfg.tp1_r_target * risk_per_unit
    tp1_fee_floor = cfg.tp1_min_profit_multiplier * fee_cost_per_unit
    tp1_distance = max(tp1_target_distance, tp1_fee_floor)

    # ── TP2: default 1R, snap to HTF level if in band ──
    tp2_distance = cfg.tp2_r_default * risk_per_unit
    tp2_method = "default_1r"

    relevant_level = resistance if is_buy else support
    if relevant_level is not None:
        if is_buy:
            level_distance = relevant_level - entry
        else:
            level_distance = entry - relevant_level
        if level_distance > 0:
            level_r = level_distance / risk_per_unit
            if cfg.resistance_min_r <= level_r <= cfg.resistance_max_r:
                snap_buf = cfg.resistance_buffer_pct * entry
                candidate_distance = level_distance - snap_buf
                if candidate_distance > tp1_distance:
                    tp2_distance = candidate_distance
                    tp2_method = "snap_resistance" if is_buy else "snap_support"

    # Skip-trade: fee floor pushed TP1 past TP2 — trade has no edge.
    if tp1_distance >= tp2_distance:
        return _skip(entry, "fees_too_high_for_risk")

    # ── TP3: fixed R target (runner; trail handled by reconciler) ──
    tp3_distance = cfg.tp3_r_target * risk_per_unit

    if is_buy:
        tp1 = entry + tp1_distance
        tp2 = entry + tp2_distance
        tp3 = entry + tp3_distance
    else:
        tp1 = entry - tp1_distance
        tp2 = entry - tp2_distance
        tp3 = entry - tp3_distance

    return TradePlan(
        entry=entry,
        stop_loss=stop_loss,
        tp1=tp1,
        tp2=tp2,
        tp3=tp3,
        sl_method=sl_method,
        tp2_method=tp2_method,
        risk_per_unit=risk_per_unit,
        tp1_qty_fraction=cfg.tp1_qty_fraction,
        tp2_qty_fraction=cfg.tp2_qty_fraction,
        tp3_qty_fraction=cfg.tp3_qty_fraction,
    )
