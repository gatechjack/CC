"""Kalshi Crypto Arbitrage — live-spot-driven strategy.

Pulls Kalshi Crypto-category markets, fetches live spot from
CoinbaseBroker.quote(), computes a calibrated P(YES) via Gaussian
probability vs threshold using annualized realized vol, and emits
ProposedOrders when divergence ≥ min_divergence_pct.

Replaces the generic LLM probability call (kalshi_llm_arbitrage) on
Crypto markets — the LLM was guessing without live price data; this
uses tonight's actual coinbase spot.

Reuses `_weather_math.evaluate_weather_market` — the same Gaussian
math works for both (temperature/sigma in Fahrenheit ↔ price/sigma
in USD; only the units differ).

Skip rules:
  - Target time > 7 days away (vol model less reliable for long horizons)
  - |spot − threshold| < sigma_total (near-threshold uncertainty)
  - |P(YES) − implied| < min_divergence_pct
  - Asset not supported (e.g., HYPE, BNB — no Coinbase US spot)
  - Time-to-resolution already negative (market expired)

Audit kinds:
  - kalshi_crypto_scan       — per-cycle summary
  - kalshi_crypto_evaluated  — per-market spot + math + verdict
  - kalshi_crypto_skipped_*  — granular skip reasons
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from trading_corp.agents.strategies._weather_math import (
    ForecastPoint,
    apply_bucket_guard,
    evaluate_weather_market,
    forecast_probability,
    kalshi_quote_dollars,
)
from trading_corp.data.crypto_spot_provider import (
    ANNUAL_VOLS,
    CryptoSpotProvider,
    parse_kalshi_asset_prefix,
    parse_kalshi_strike_suffix,
)
from trading_corp.persistence.models import ProposedOrder

log = logging.getLogger(__name__)

# Crypto markets resolve fast; cap horizon at 7 days. Vol model
# uncertainty grows as sqrt(time); past 7d our σ estimate is noisy
# enough that we'd rarely beat the market.
MAX_HORIZON_HOURS_CRYPTO = 7 * 24

# Source-divergence cushion: tiny for crypto (live spot is the
# resolution source on Kalshi too — they reference Coinbase oracle
# prices for most crypto markets). Set to a token 0.1% of spot to
# absorb quote-latency noise.
SOURCE_DIVERGENCE_SIGMA_FRAC = 0.001


def _compute_event_bucket_widths(events: Any) -> dict[str, float]:
    """For each event_ticker, return the median gap between adjacent B-values.

    Kalshi bucket widths vary by asset AND horizon — ETH 1h buckets are ~$20,
    ETH Jan-2027 buckets are ~$500. So a static per-asset width would be
    wrong half the time. Derive from the data in the same discovery batch.

    Falls back gracefully: events with < 2 B-tickers return no entry,
    callers must handle absence.
    """
    by_event: dict[str, list[float]] = {}
    for ev in events:
        et = getattr(ev, "event_ticker", None)
        if not et:
            continue
        for m in getattr(ev, "markets", []) or []:
            parsed = parse_kalshi_strike_suffix(getattr(m, "ticker", "") or "")
            if parsed and parsed[0] == "B":
                by_event.setdefault(et, []).append(parsed[1])
    widths: dict[str, float] = {}
    for et, vals in by_event.items():
        if len(vals) < 2:
            continue
        vals.sort()
        gaps = [vals[i + 1] - vals[i] for i in range(len(vals) - 1)]
        gaps.sort()
        # median gap; positive widths only
        med = gaps[len(gaps) // 2]
        if med > 0:
            widths[et] = med
    return widths


class KalshiCryptoArbAgent:
    """Live-spot-driven Kalshi crypto arbitrage.

    Pure deterministic — no LLM in path. Hot-reloadable via
    `strategies.yaml kalshi_crypto_arb:`.
    """

    name = "kalshi_crypto_arb"

    def __init__(self, *, db_url: str | None = None) -> None:
        self._db_url = db_url
        self._strategies_yaml = Path("config/strategies.yaml")
        self._strat_mtime: float = 0.0
        self._strat_cfg: dict[str, Any] = {}
        self._discovery_cache: Any = None
        self._discovery_ts: datetime | None = None
        self._reload()

    def _reload(self) -> None:
        try:
            sm = self._strategies_yaml.stat().st_mtime
            if sm != self._strat_mtime:
                with self._strategies_yaml.open("r") as f:
                    data = yaml.safe_load(f) or {}
                self._strat_cfg = data.get(self.name) or {}
                self._strat_mtime = sm
        except Exception as e:
            log.warning("kalshi_crypto_arb: yaml reload failed: %s", e)
            self._strat_cfg = {}

    @property
    def enabled(self) -> bool:
        self._reload()
        return bool(self._strat_cfg.get("enabled", False))

    @property
    def auto_execute(self) -> bool:
        self._reload()
        return bool(self._strat_cfg.get("auto_execute", False))

    @property
    def division(self) -> str:
        self._reload()
        return str(self._strat_cfg.get("division", "kalshi_crypto"))

    # ── public scan entry ────────────────────────────────────────────────

    async def run_scan_cycle(
        self, kalshi_broker: Any, coinbase_broker: Any,
        *, logger_agent: Any = None,
    ) -> list[ProposedOrder]:
        self._reload()
        if not self.enabled:
            return []
        if coinbase_broker is None:
            log.debug("kalshi_crypto_arb: no coinbase broker available; no-op")
            return []

        # v2 vol refresh: idempotent and rate-limited inside. First-cycle-
        # after-restart pays the fetch cost (~14 paginated ccxt calls per
        # asset at 14d/5m); subsequent cycles within refresh_interval_minutes
        # are no-ops. All errors fall back to ANNUAL_VOLS constants and
        # CryptoSpotProvider.get_annual_vol() reads the cache transparently.
        from trading_corp.data.crypto_vol_provider import VolConfig
        rv_cfg = self._strat_cfg.get("realized_vol") or {}
        vol_cfg = VolConfig(**{
            k: v for k, v in rv_cfg.items()
            if k in VolConfig.__dataclass_fields__
        })
        rv_status = await CryptoSpotProvider.refresh_realized_vols_if_due(vol_cfg)
        if logger_agent is not None and rv_status:
            non_cached = {k: v for k, v in rv_status.items() if v != "cached"}
            if non_cached:
                logger_agent.log_event(self.name, "kalshi_crypto_vol_refresh", {
                    "strategy": self.name, "division": self.division,
                    "statuses": non_cached,
                })

        disc_cfg = self._strat_cfg.get("discovery") or {}
        max_series = int(disc_cfg.get("max_series_per_category", 30))
        max_markets = int(disc_cfg.get("max_markets_per_series", 50))
        cache_ttl = float(disc_cfg.get("cache_ttl_sec", 600))
        min_div_pct = float(self._strat_cfg.get("min_divergence_pct", 10.0))
        # max_divergence_pct (2026-05-20, paper only): caps high-edge fires
        # where the bleed is tail/oracle disagreement, not vol artifact.
        # 50%+ NO bin did not compress under realized vol per backtester.
        _max_div_raw = self._strat_cfg.get("max_divergence_pct")
        max_div_pct = float(_max_div_raw) if _max_div_raw is not None else None
        max_hours = float(self._strat_cfg.get(
            "max_horizon_hours", MAX_HORIZON_HOURS_CRYPTO,
        ))
        k_per_cycle = int(self._strat_cfg.get("k_markets_per_cycle", 30))
        cooldown_h = float(self._strat_cfg.get("market_cooldown_hours", 1))

        # 1. Discovery — Crypto category only.
        now = datetime.now(timezone.utc)
        need_refresh = (
            self._discovery_cache is None
            or self._discovery_ts is None
            or (now - self._discovery_ts).total_seconds() > cache_ttl
        )
        if need_refresh:
            try:
                self._discovery_cache = await kalshi_broker.list_markets(
                    categories=("Crypto",),
                    max_series_per_category=max_series,
                    max_markets_per_series=max_markets,
                )
                self._discovery_ts = now
            except Exception as e:
                log.warning("kalshi_crypto_arb: discovery failed: %s", e)
                return []
        events = (self._discovery_cache.events
                  if self._discovery_cache is not None else [])

        survivors: list[dict[str, Any]] = []
        n_pre_filter = 0
        n_skipped_not_crypto = 0
        n_skipped_unsupported = 0

        spot_provider = CryptoSpotProvider(coinbase_broker)

        # Bucket-width inference: walk all crypto B-tickers in this
        # discovery and compute the median adjacent-gap per event_ticker.
        # Kalshi's bucket widths vary by asset AND horizon (ETH 1-hour
        # buckets are ~$20, ETH Jan-2027 buckets are ~$500), so we can't
        # use a static per-asset constant — derive from the data.
        event_bucket_widths = _compute_event_bucket_widths(events)

        for event in events:
            for m in event.markets:
                n_pre_filter += 1
                asset = parse_kalshi_asset_prefix(m.ticker or "")
                if asset is None:
                    n_skipped_not_crypto += 1
                    continue
                if not CryptoSpotProvider.is_supported(asset):
                    n_skipped_unsupported += 1
                    continue
                yes_ask_d, no_ask_d, yes_bid_d, no_bid_d = kalshi_quote_dollars(m)
                survivors.append({
                    "ticker": m.ticker,
                    "event_ticker": m.event_ticker,
                    "category": event.category,
                    "asset": asset,
                    "yes_bid": yes_bid_d,
                    "yes_ask": yes_ask_d,
                    "no_bid": no_bid_d,
                    "no_ask": no_ask_d,
                    "expected_expiration_time": m.expected_expiration_time,
                    "bucket_width_hint": event_bucket_widths.get(m.event_ticker),
                    # Strike fields surfaced at discovery (used by Fix B strike-
                    # distance filter below) — avoids a second get_market call.
                    "floor_strike": getattr(m, "floor_strike", None),
                    "cap_strike": getattr(m, "cap_strike", None),
                    "strike_type": (getattr(m, "strike_type", None) or "").lower(),
                })

        # Drop markets without an ASK quote — implied_yes downstream
        # needs yes_ask or no_ask (not bid). Kalshi returns 0.0 (not
        # None) for unquoted sides, so check positive explicitly.
        survivors = [
            d for d in survivors
            if (d.get("yes_ask") or 0) > 0 or (d.get("no_ask") or 0) > 0
        ]
        # Drop markets past the horizon cap before the k_per_cycle cut —
        # otherwise long-dated MAXMON markets (~5,535h out) crowd the budget
        # via tightest-spread sort, starving near-term markets that can
        # actually fire. Cheap: one ISO parse per survivor.
        def _horizon_hours(d: dict[str, Any]) -> float | None:
            iso = d.get("expected_expiration_time")
            if not iso:
                return None
            try:
                t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                return (t - now).total_seconds() / 3600.0
            except (TypeError, ValueError):
                return None
        survivors = [
            d for d in survivors
            if (_horizon_hours(d) or 0) <= max_hours
        ]
        # Strike-distance-from-spot filter (Fix B, 2026-05-15 PM session).
        # Drop markets where |strike - spot| > K × expected_move_over_horizon.
        # Outside this band the Gaussian model returns ~0 or ~1; Kalshi's $0.01
        # pricing floor pins implied to the boundary; we always get sub-threshold
        # edge noise. Pre-fix, XRP T-tail strikes consumed 51% of k_per_cycle.
        # Markets with unknown strike (T-suffix custom without direction info)
        # are kept — let downstream classify them as no_strike.
        k_sigma = float(self._strat_cfg.get("strike_distance_k_sigma", 3.0))
        spot_cache: dict[str, float] = {}
        async def _spot_for(a: str) -> float | None:
            if a not in spot_cache:
                s = await spot_provider.get_spot(a)
                if s is not None and s > 0:
                    spot_cache[a] = s
            return spot_cache.get(a)
        def _strike_point(d: dict[str, Any]) -> float | None:
            # Discovery market objects don't carry strike_type/floor_strike/
            # cap_strike — those come from get_market() in _evaluate_market.
            # For B-/T-suffix tickers (covers most crypto markets) we can
            # parse the strike directly from the ticker. Distance check
            # doesn't need direction — just the strike value.
            # Markets without a parseable ticker suffix (greater_or_equal
            # SOL15M, etc.) are typically near-spot momentum markets where
            # the filter wouldn't reject them anyway — let them through.
            parsed = parse_kalshi_strike_suffix(d.get("ticker") or "")
            if parsed:
                return float(parsed[1])
            return None
        n_skipped_strike_distance = 0
        filtered: list[dict[str, Any]] = []
        for d in survivors:
            sp = _strike_point(d)
            if sp is None:
                filtered.append(d)
                continue
            spot = await _spot_for(d["asset"])
            if spot is None or spot <= 0:
                filtered.append(d)
                continue
            av = spot_provider.get_annual_vol(d["asset"]) or 1.0
            h = _horizon_hours(d) or 0.0
            expected_move = spot * av * math.sqrt(max(h, 0.0) / (24.0 * 365.0))
            expected_move = max(expected_move, spot * 1e-4)
            if abs(sp - spot) > k_sigma * expected_move:
                n_skipped_strike_distance += 1
                continue
            filtered.append(d)
        survivors = filtered
        # Tightest-spread first — most useful to evaluate.
        survivors.sort(
            key=lambda d: abs((d.get("yes_ask") or 1) - (d.get("yes_bid") or 0))
        )
        survivors = survivors[:k_per_cycle]

        if logger_agent is not None:
            logger_agent.log_event(
                self.name, "kalshi_crypto_scan",
                {
                    "strategy": self.name, "division": self.division,
                    "markets_pre_filter": n_pre_filter,
                    "skipped_not_crypto": n_skipped_not_crypto,
                    "skipped_unsupported_asset": n_skipped_unsupported,
                    "skipped_strike_distance": n_skipped_strike_distance,
                    "strike_distance_k_sigma": k_sigma,
                    "candidates": len(survivors),
                    "k_per_cycle": k_per_cycle,
                    "min_divergence_pct": min_div_pct,
                    "max_horizon_hours": max_hours,
                },
            )

        # 2. Per-market: full market fetch → spot → math → emit if fired.
        orders: list[ProposedOrder] = []
        new_cooldowns = self._load_cooldowns(now=now)
        for cand in survivors:
            tkr = cand["ticker"]
            if _is_in_cooldown(tkr, new_cooldowns, now, cooldown_h):
                continue

            try:
                full = await kalshi_broker._client.get_market(tkr)
            except Exception as e:
                log.debug("kalshi_crypto_arb: get_market(%s) failed: %s", tkr, e)
                continue

            order, skip_payload, eval_payload = await self._evaluate_market(
                full=full, cand=cand,
                spot_provider=spot_provider,
                min_div_pct=min_div_pct, max_div_pct=max_div_pct,
                max_hours=max_hours, now=now,
            )

            if logger_agent is not None and eval_payload is not None:
                logger_agent.log_event(
                    self.name, "kalshi_crypto_evaluated", eval_payload,
                )
            if order is not None:
                orders.append(order)
                new_cooldowns[tkr] = (now + timedelta(hours=cooldown_h)).isoformat()
            elif skip_payload and logger_agent is not None:
                logger_agent.log_event(
                    self.name, f"kalshi_crypto_skipped_{skip_payload['code']}",
                    skip_payload,
                )

        self._save_cooldowns(new_cooldowns)
        return orders

    # ── per-market eval ──────────────────────────────────────────────────

    async def _evaluate_market(
        self, *, full: Any, cand: dict[str, Any],
        spot_provider: CryptoSpotProvider,
        min_div_pct: float, max_div_pct: float | None,
        max_hours: float, now: datetime,
    ) -> tuple[ProposedOrder | None, dict[str, Any] | None, dict[str, Any] | None]:
        tkr = cand["ticker"]
        asset = cand["asset"]
        floor_strike = getattr(full, "floor_strike", None)
        cap_strike = getattr(full, "cap_strike", None)
        strike_type = (getattr(full, "strike_type", None) or "").lower()
        title = getattr(full, "title", None) or ""
        yes_ask, no_ask, _, _ = kalshi_quote_dollars(full)

        # Kalshi crypto markets use strike_type='custom' for BOTH bucket
        # markets (B-suffix tickers) AND single-side threshold markets
        # (T-suffix tickers), and leave floor_strike/cap_strike as None
        # for both. The strike spec lives only in the ticker suffix.
        threshold = None
        threshold_high = None
        direction = None
        if strike_type == "greater" and floor_strike is not None:
            threshold = float(floor_strike); direction = "greater"
        elif strike_type == "less":
            threshold = float(cap_strike if cap_strike is not None else floor_strike)
            direction = "less"
        elif strike_type == "greater_or_equal" and floor_strike is not None:
            # KXSOL15M-style momentum markets: floor_strike is a snapshot price
            # from T-15min; YES if avg over the resolution window ≥ that anchor.
            # Drift-free Gaussian centered on current spot fits the same shape
            # as the 'greater' branch.
            threshold = float(floor_strike); direction = "greater"
        elif strike_type == "less_or_equal" and (cap_strike is not None or floor_strike is not None):
            threshold = float(cap_strike if cap_strike is not None else floor_strike)
            direction = "less"
        elif strike_type in ("between", "custom") and floor_strike is not None and cap_strike is not None:
            threshold = float(floor_strike)
            threshold_high = float(cap_strike)
            direction = "between"
        elif strike_type == "custom":
            # Fall back to ticker-suffix dispatch.
            parsed = parse_kalshi_strike_suffix(tkr)
            if parsed and parsed[0] == "B":
                bucket_center = parsed[1]
                # Width hint from neighboring B-tickers in same event.
                width = cand.get("bucket_width_hint")
                if width and width > 0:
                    half = width / 2.0
                    threshold = bucket_center - half
                    threshold_high = bucket_center + half
                    direction = "between"
            # T-suffix tickers are intentionally not handled here —
            # direction ('greater' vs 'less') is ambiguous without
            # parsing rules_primary text. Falls through to no_strike.
        if threshold is None or direction is None:
            payload = {"strategy": self.name, "division": self.division,
                       "ticker": tkr, "title": title, "asset": asset,
                       "skip_code": "no_strike", "strike_type": strike_type}
            return None, {"code": "no_strike", **payload}, payload

        # Target time from market metadata.
        exp_iso = (
            getattr(full, "expected_expiration_time", None)
            or getattr(full, "expiration_time", None)
            or getattr(full, "close_time", None)
        )
        if not exp_iso:
            payload = {"strategy": self.name, "division": self.division,
                       "ticker": tkr, "title": title, "asset": asset,
                       "skip_code": "no_target_time"}
            return None, {"code": "no_target_time", **payload}, payload

        try:
            tgt_dt = datetime.fromisoformat(str(exp_iso).replace("Z", "+00:00"))
            if tgt_dt.tzinfo is None:
                tgt_dt = tgt_dt.replace(tzinfo=timezone.utc)
            horizon_h = (tgt_dt - now).total_seconds() / 3600.0
        except (TypeError, ValueError):
            payload = {"strategy": self.name, "division": self.division,
                       "ticker": tkr, "title": title, "asset": asset,
                       "skip_code": "bad_target_time", "target_iso": str(exp_iso)}
            return None, {"code": "bad_target_time", **payload}, payload

        # Live spot.
        spot = await spot_provider.get_spot(asset)
        if spot is None:
            payload = {"strategy": self.name, "division": self.division,
                       "ticker": tkr, "title": title, "asset": asset,
                       "skip_code": "no_spot"}
            return None, {"code": "no_spot", **payload}, payload

        # σ = spot × annual_vol × sqrt(time_to_resolution_years).
        annual_vol = spot_provider.get_annual_vol(asset) or 1.0
        years = max(horizon_h, 0.0) / (24.0 * 365.0)
        sigma = spot * annual_vol * math.sqrt(years)
        sigma = max(sigma, spot * 1e-6)  # floor — never literally 0

        # Implied YES.
        implied_yes = (
            yes_ask if 0 < yes_ask < 1
            else (1.0 - no_ask if 0 < no_ask < 1 else None)
        )
        if implied_yes is None:
            payload = {"strategy": self.name, "division": self.division,
                       "ticker": tkr, "title": title, "asset": asset,
                       "skip_code": "no_implied"}
            return None, {"code": "no_implied", **payload}, payload

        # Use the existing weather/threshold math (Gaussian). Source-
        # divergence cushion is tiny for crypto (spot-driven oracle).
        forecast = ForecastPoint(
            temp_f=spot, sigma_f=sigma,
            valid_iso=now.isoformat(), source="coinbase",
        )
        source_div_sigma = spot * SOURCE_DIVERGENCE_SIGMA_FRAC
        verdict = evaluate_weather_market(
            forecast=forecast, threshold_f=threshold, direction=direction,
            threshold_high_f=threshold_high,
            implied_yes=implied_yes, horizon_hours=horizon_h,
            min_divergence_pct=min_div_pct,
            max_divergence_pct=max_div_pct,
            max_horizon_hours=max_hours,
            source_divergence_sigma_f=source_div_sigma,
        )

        # Vol-v2 drift watch (2026-05-20, paper). Compute the hardcoded-vol
        # mirror prob/edge so the audit pool carries a per-fire classification
        # of how realized-vol diverges from the hardcoded baseline. The
        # primary path stays governed by `annual_vol` (cache-read, realized
        # when enabled). This block only produces audit telemetry; it does
        # NOT alter the firing decision. Cheap: two CDF evaluations.
        hardcoded_av = ANNUAL_VOLS.get(asset)
        hc_prob_yes: float | None = None
        hc_edge_pct: float | None = None
        if hardcoded_av is not None:
            sigma_hc = spot * hardcoded_av * math.sqrt(years)
            sigma_hc = max(sigma_hc, spot * 1e-6)
            sigma_hc_total = math.sqrt(sigma_hc * sigma_hc + source_div_sigma * source_div_sigma)
            hc_prob_yes = forecast_probability(
                forecast_temp_f=spot, sigma_f=sigma_hc_total,
                threshold_f=threshold, direction=direction,
                threshold_high_f=threshold_high,
            )
            hc_edge_pct = abs(hc_prob_yes - implied_yes) * 100.0
        # `would_have_fired_hardcoded` mirrors the same gates the primary
        # path applies: min divergence + optional max cap. We deliberately
        # do NOT replay the near-threshold gate here — kalshi crypto is
        # ~100% between-direction in production, where near-threshold
        # doesn't apply.
        would_fire_hc = (
            hc_edge_pct is not None
            and hc_edge_pct >= min_div_pct
            and (max_div_pct is None or hc_edge_pct <= max_div_pct)
        )

        eval_payload = {
            "strategy": self.name, "division": self.division,
            "ticker": tkr, "title": title, "category": cand["category"],
            "asset": asset,
            "spot_price": round(spot, 4),
            "spot_sigma_usd": round(sigma, 4),
            "sigma_used_usd": round(verdict.sigma_used_f, 4),
            "annual_vol": annual_vol,
            "horizon_hours": round(horizon_h, 3),
            "threshold_usd": threshold, "threshold_high_usd": threshold_high,
            "direction": direction,
            "delta_usd": round(verdict.delta_f, 4),
            "implied_yes": round(implied_yes, 3),
            "prob_yes": round(verdict.prob_yes, 3),
            "edge_pct": round(verdict.edge_pct, 1),
            "divergence_pct": round(verdict.edge_pct, 1),
            "fired": verdict.fired,
            "skip_reason": verdict.skip_reason,
            # Vol-v2 drift watch fields:
            "hardcoded_av": hardcoded_av,
            "hardcoded_prob_yes": round(hc_prob_yes, 3) if hc_prob_yes is not None else None,
            "hardcoded_edge_pct": round(hc_edge_pct, 1) if hc_edge_pct is not None else None,
        }
        if not verdict.fired:
            code = ("near_threshold" if "near-threshold" in verdict.skip_reason
                    else "horizon" if "horizon" in verdict.skip_reason
                    else "divergence_too_high" if "max_divergence_pct" in verdict.skip_reason
                    else "no_edge")
            # Suppressed-fire flag: realized-vol path skipped but hardcoded
            # would have fired. Lets us track baseline drift on the skip
            # side too (per the user's "drift toward +$2.37" concern).
            if would_fire_hc:
                eval_payload["vol_v2_classification"] = "suppressed_fire"
            else:
                eval_payload["vol_v2_classification"] = "both_skip"
            return None, {"code": code, **eval_payload}, eval_payload

        # Fire path: classify whether the hardcoded baseline would also
        # have fired. "new_fire" is the [5-10%] old-edge pool that realized
        # vol lifts above the 10% floor — the population the backtester
        # could only sample 16 of.
        eval_payload["vol_v2_classification"] = (
            "same_fire" if would_fire_hc else "new_fire"
        )

        # Build ProposedOrder.
        outcome = "yes" if verdict.prob_yes > implied_yes else "no"

        # Bucket-aware bet-side guard. Same σ-smearing failure mode as
        # kalshi_weather (deployed 2026-05-16): when the bucket is narrow
        # relative to σ, prob_yes is structurally low even when spot is
        # inside the bucket — the math says "no bucket high probability"
        # and we sell the modal bucket. Spot-here, threshold-there means
        # we use `spot` (=forecast.temp_f for the underlying weather
        # function) as the "forecast" for guard purposes.
        guard = apply_bucket_guard(
            direction=direction,
            forecast_temp_f=spot,
            threshold_f=threshold,
            threshold_high_f=threshold_high,
            proposed_outcome=outcome,
            implied_yes=implied_yes,
            flip_yes_implied_ceiling=float(
                self._strat_cfg.get("bucket_guard_flip_yes_implied_ceiling", 0.70)
            ),
        )
        bucket_guard_action = guard.action
        if guard.outcome is None:
            eval_payload["bucket_guard"] = guard.action
            eval_payload["skip_reason"] = guard.skip_reason
            eval_payload["fired"] = False
            return None, {"code": "bucket_guard", **eval_payload}, eval_payload
        outcome = guard.outcome
        if bucket_guard_action:
            eval_payload["bucket_guard"] = bucket_guard_action

        share_price = yes_ask if outcome == "yes" else no_ask
        if share_price <= 0 or share_price >= 1:
            eval_payload["skip_reason"] = f"share_price out-of-range ({share_price})"
            eval_payload["fired"] = False
            return None, {"code": "no_edge", **eval_payload}, eval_payload

        sizing = self._strat_cfg.get("sizing") or {}
        fixed_usd = float(sizing.get("fixed_amount", 1.0))
        qty = fixed_usd / share_price
        order = ProposedOrder(
            strategy=self.name,
            symbol=f"{tkr}:{outcome}",
            side="buy",
            qty=qty,
            order_type="limit",
            limit_price=share_price,
            rationale=(
                f"Crypto {asset}: spot=${spot:.2f}±${verdict.sigma_used_f:.2f}, "
                f"threshold=${threshold:.2f} ({direction}), "
                f"P(YES)={verdict.prob_yes:.2f} vs implied {implied_yes:.2f} "
                f"(edge {verdict.edge_pct:.1f}%); buy {outcome.upper()} @ {share_price:.3f}"
            ),
            extra={
                "outcome": outcome,
                "ticker": tkr,
                "event_ticker": cand["event_ticker"],
                "event_title": title,
                "title": title,
                "category": cand["category"],
                "asset": asset,
                "implied_prob_at_entry": implied_yes,
                "spot_price": spot,
                "spot_sigma_usd": sigma,
                "sigma_used_usd": verdict.sigma_used_f,
                "annual_vol": annual_vol,
                "threshold_usd": threshold,
                "threshold_high_usd": threshold_high,
                "direction": direction,
                "horizon_hours": round(horizon_h, 3),
                "delta_usd": round(verdict.delta_f, 4),
                "prob_yes": verdict.prob_yes,
                "divergence_pct": verdict.edge_pct,
                "expires_at": cand["expected_expiration_time"],
                "max_dollar_risk": fixed_usd,
                "tier": "crypto_spot_fixed_usd",
                "source_signal": "coinbase_spot",
                "is_prediction_market": True,
                # Vol-v2 drift watch (2026-05-20, paper). Per-fire mirror
                # of the hardcoded-vol alternative path so a future query
                # can bucket new fires by band x side x outcome and watch
                # for drift toward the strictly-comparable +$2.37 number.
                "hardcoded_av": hardcoded_av,
                "hardcoded_prob_yes": hc_prob_yes,
                "hardcoded_edge_pct": hc_edge_pct,
                "vol_v2_classification": eval_payload["vol_v2_classification"],
                # Bucket-aware bet-side guard outcome (2026-05-16; shared
                # with kalshi_weather). None on natural-path trades.
                "bucket_guard": bucket_guard_action,
            },
        )
        return order, None, eval_payload

    # ── cooldown state ────────────────────────────────────────────────────

    def _cooldown_path(self) -> Path:
        return Path("data") / f"{self.name}_cooldowns.yaml"

    def _load_cooldowns(self, *, now: datetime) -> dict[str, str]:
        p = self._cooldown_path()
        if not p.exists():
            return {}
        try:
            with p.open("r") as f:
                data = yaml.safe_load(f) or {}
        except Exception:
            return {}
        out: dict[str, str] = {}
        for k, v in (data or {}).items():
            try:
                exp = datetime.fromisoformat(v.replace("Z", "+00:00"))
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp > now:
                    out[k] = v
            except Exception:
                continue
        return out

    def _save_cooldowns(self, cooldowns: dict[str, str]) -> None:
        try:
            p = self._cooldown_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            with p.open("w") as f:
                yaml.safe_dump(cooldowns, f)
        except Exception as e:
            log.debug("kalshi_crypto_arb: cooldown save failed: %s", e)


def _is_in_cooldown(
    ticker: str, cooldowns: dict[str, str], now: datetime, cooldown_h: float,
) -> bool:
    if cooldown_h <= 0:
        return False
    exp = cooldowns.get(ticker)
    if not exp:
        return False
    try:
        exp_dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        return now < exp_dt
    except Exception:
        return False
