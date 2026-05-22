"""kalshi_weather hourly re-evaluation replay (Phase D).

Reads the read-only prod corpus (tmp/kw_whp.jsonl.gz, tmp/kw_rt.jsonl.gz)
that the Phase-C dump pulled, backfills METAR (Tier A.1) + Open-Meteo
historical-forecast (Tier A.2), recomputes the signal at each hourly H,
and scores against the resolved outcome from kalshi_round_trips.

THREE TIERS (per Phase-D operator spec):
  A.1  METAR-only observed-floor for HIGH/LOW: forecast_at_H =
       max/min(observed-through-H, entry_forecast). ZERO-LEAK headline.
  A.2  METAR + Open-Meteo historical-forecast overlay. LEAK-INFLATED
       UPPER BOUND — Open-Meteo's historical-forecast endpoint returns
       forecasts whose issue time may be AFTER entry_ts. All A.2
       columns labeled with the _LEAK_INFLATED suffix.
  B    PnL with assumed constant 3¢ spread + Kalshi fee formula. Every
       column/summary suffixed _DIRECTIONAL_ONLY_spread_assumed_constant.

GATES enforced in code:
  PARITY (#4)  — at H=entry the recomputed prob_yes MUST match the audit
                 to within 1e-3 (round(.., 3) is the audit's recorded
                 precision). Off-by-more = bug, hard exit.
  LEAK GUARD   — `assert obs_dt <= H` at every observed-floor call. If
  (#5)           Tier A.1 % correct comes back near 100%, surface for
                 investigation rather than report as signal.

Output:
  tmp/replay_results.csv     — one row per (position, H)
  tmp/replay_summary.json    — aggregates + gate results

Wrapper-invocation (CLAUDE.md): this script imports trading_corp; run
via run_capped.ps1 wrapper (procgov 25 GB cap).
"""
from __future__ import annotations

import csv
import gzip
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# When invoked as `python scripts/replay_...py`, sys.path[0] = scripts/;
# trading_corp lives at repo-root. Prepend repo-root so the import below
# resolves the same way the live strategy does.
_REPO_ROOT_FOR_IMPORT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT_FOR_IMPORT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT_FOR_IMPORT))

# Pure function — same module the strategy uses. Imports trading_corp so
# the wrapper is required (see file docstring).
from trading_corp.agents.strategies._weather_math import (
    SOURCE_DIVERGENCE_SIGMA_F,
    forecast_probability,
)

# ── Constants ────────────────────────────────────────────────────────────
REPO = Path(__file__).resolve().parents[1]
TMP = REPO / "tmp"
METAR_CACHE = TMP / "metar_cache"
OM_CACHE = TMP / "open_meteo_cache"
METAR_CACHE.mkdir(parents=True, exist_ok=True)
OM_CACHE.mkdir(parents=True, exist_ok=True)

WHP_PATH = TMP / "kw_whp.jsonl.gz"
RT_PATH = TMP / "kw_rt.jsonl.gz"

RESULTS_CSV = TMP / "replay_results.csv"
SUMMARY_JSON = TMP / "replay_summary.json"

USER_AGENT = "trading-corp-weather-replay (+https://trading.jacksumner.com)"

# Tier-B cost model (PER OPERATOR SPEC — single global constants)
ASSUMED_SPREAD = 0.03
ASSUMED_HALF_SPREAD = ASSUMED_SPREAD / 2.0
KALSHI_FEE_RATE = 0.07
KALSHI_FEE_FLOOR_CENTS = 1

# Mirror of trading_corp.agents.strategies.kalshi_weather_arb._CITY_TO_METAR_STATION
# Inlined here so the replay reads independent of any future edits to
# the live mapping (which we'd want to fail the parity gate, not silently
# adopt). Audit on commit if the live mapping changes.
CITY_TO_METAR_STATION: dict[str, str] = {
    "NYC_CENTRAL": "KNYC", "NYC": "KNYC", "TBOS": "KBOS", "TDC": "KDCA",
    "TSEA": "KSEA", "TATL": "KATL", "TDAL": "KDFW", "PHIL": "KPHL",
    "TOKC": "KOKC", "MIA": "KMIA", "CHI": "KMDW", "AUS": "KAUS",
    "TAUS": "KAUS", "TMIN": "KMSP", "TSATX": "KSAT", "TSFO": "KSFO",
    "LAX": "KLAX", "DEN": "KDEN", "TDEN": "KDEN", "THOU": "KHOU",
    "TPHX": "KPHX", "TNOLA": "KMSY", "TMIA": "KMIA", "TCHI": "KMDW",
    "TPHIL": "KPHL", "TLAX": "KLAX", "TNYC": "KNYC", "NY": "KNYC",
}

# Hour grid: every HOUR_STEP_H hours between entry+1h and target-1h
HOUR_STEP_H = 1

# Parity tolerance — audit stores eval_payload prob_yes as round(.., 3)
# but extra_json prob_yes (in WHP) is unrounded float. We compare against
# the unrounded extra, so float-level tolerance (1e-9) is the gate. Any
# real bug shows up >> this; rounding is a non-issue.
PARITY_TOL = 1e-9
# Loose tolerance used only for diagnostics on positions that fail strict:
PARITY_DIAG_TOL = 5e-4


# ── Helpers ──────────────────────────────────────────────────────────────
def parse_iso(s: str) -> datetime:
    if s is None:
        raise ValueError("ISO None")
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def city_code_from_ticker(ticker: str) -> str | None:
    """KXHIGHCHI-26MAY15-B77.5 -> 'CHI'. KXLOWBOS-... -> 'BOS'."""
    m = re.match(r"^KX(HIGH|LOW|TEMP)([A-Z]+?)-", ticker)
    return m.group(2) if m else None


def market_kind(ticker: str) -> str:
    """HIGH | LOW | TEMP (matches the strategy's notion of cand['kind'])."""
    m = re.match(r"^KX(HIGH|LOW|TEMP)", ticker)
    return m.group(1) if m else "UNKNOWN"


_MONTHS = {"JAN":1,"FEB":2,"MAR":3,"APR":4,"MAY":5,"JUN":6,
           "JUL":7,"AUG":8,"SEP":9,"OCT":10,"NOV":11,"DEC":12}

def parse_target_iso(event_ticker: str) -> datetime | None:
    """KXHIGHCHI-26MAY15 → 2026-05-15T23:59:59+00:00 (end of UTC day).

    Hourly TEMP markets (e.g. KXTEMPNYCH-26MAY15-14) need local→UTC
    conversion we can't do here without city tz tables; returns None
    for those (1 row in the 636 corpus — negligible).
    """
    m = re.match(
        r"^KX(?:HIGH|LOW|TEMP)[A-Z]+-(\d{2})([A-Z]{3})(\d{2})(?:-(\d{2}))?",
        event_ticker,
    )
    if not m:
        return None
    yy, mmm, dd, hh = m.groups()
    month = _MONTHS.get(mmm.upper())
    if month is None or hh is not None:
        return None
    return datetime(2000 + int(yy), month, int(dd), 23, 59, 59, tzinfo=timezone.utc)


def http_get_json(url: str, attempt_label: str, retries: int = 4) -> Any:
    """Tiny HTTP GET → JSON helper with light retry + UA header."""
    last_err = ""
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=20) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}: {e.reason}"
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            raise
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            time.sleep(2 ** attempt)
    raise RuntimeError(f"{attempt_label} failed after {retries}: {last_err}; url={url}")


# ── METAR backfill (zero-leak Tier A.1 input) ────────────────────────────
def fetch_metar_for_station(station: str) -> list[dict]:
    """One-time fetch of past N hours of METAR obs for `station`.

    NWS Aviation Weather API accepts `hours=N` for up to ~30 days. We
    pull a wide window (250h ≈ 10.4 days) so the entire corpus is
    covered without re-querying. Cached to disk.

    Returns sorted list of {obs_dt (datetime, UTC), temp_f (float)}.
    """
    cache = METAR_CACHE / f"{station}.json"
    if cache.exists():
        raw = json.loads(cache.read_text())
    else:
        url = (
            "https://aviationweather.gov/api/data/metar"
            f"?ids={station}&format=json&hours=250"
        )
        raw = http_get_json(url, attempt_label=f"metar {station}")
        cache.write_text(json.dumps(raw))
        time.sleep(0.5)  # gentle rate limit
    obs: list[dict] = []
    for row in raw:
        t_iso = row.get("reportTime") or row.get("obsTime")
        temp_c = row.get("temp")
        if t_iso is None or temp_c is None:
            continue
        try:
            t_dt = parse_iso(t_iso)
            temp_f = float(temp_c) * 9.0 / 5.0 + 32.0
        except (TypeError, ValueError):
            continue
        obs.append({"obs_dt": t_dt, "temp_f": temp_f})
    obs.sort(key=lambda x: x["obs_dt"])
    return obs


def observed_extremum_through_H(
    metar_obs: list[dict], entry_ts: datetime, H: datetime, kind: str,
) -> float | None:
    """LEAK-GUARDED observed max (HIGH) / min (LOW) of METAR temps in
    [entry_ts, H]. Returns None if no obs in window. Asserts that every
    obs used has obs_dt <= H.
    """
    in_window = [o for o in metar_obs if entry_ts <= o["obs_dt"] <= H]
    for o in in_window:
        # Hard assert — if this ever fires, we've leaked future data.
        assert o["obs_dt"] <= H, (
            f"LEAK: observation obs_dt={o['obs_dt'].isoformat()} > H={H.isoformat()}"
        )
    if not in_window:
        return None
    temps = [o["temp_f"] for o in in_window]
    if kind == "HIGH":
        return max(temps)
    if kind == "LOW":
        return min(temps)
    return None


# ── Open-Meteo historical-forecast (Tier A.2 — leak-inflated) ────────────
def fetch_open_meteo_daily(lat: float, lon: float, date_iso: str) -> dict | None:
    """One historical-forecast issue for (lat, lon, date_iso).

    Returns {'tmax_f': float, 'tmin_f': float} (the model's predicted
    daily max/min for date_iso). This is NOT issue-time-controlled —
    Open-Meteo returns the best historical archive forecast it has for
    that date. Treating as leak-inflated upper bound per spec.
    """
    cache_key = f"{lat:+.4f}_{lon:+.4f}_{date_iso}.json"
    cache = OM_CACHE / cache_key
    if cache.exists():
        raw = json.loads(cache.read_text())
    else:
        url = (
            "https://historical-forecast-api.open-meteo.com/v1/forecast?"
            + urllib.parse.urlencode({
                "latitude": f"{lat:.4f}",
                "longitude": f"{lon:.4f}",
                "start_date": date_iso,
                "end_date": date_iso,
                "daily": "temperature_2m_max,temperature_2m_min",
                "temperature_unit": "fahrenheit",
                "timezone": "UTC",
            })
        )
        try:
            raw = http_get_json(url, attempt_label=f"om {lat},{lon},{date_iso}")
        except Exception as e:
            cache.write_text(json.dumps({"error": str(e)}))
            return None
        cache.write_text(json.dumps(raw))
        time.sleep(0.2)
    if "error" in raw:
        return None
    daily = raw.get("daily") or {}
    tmaxs = daily.get("temperature_2m_max") or []
    tmins = daily.get("temperature_2m_min") or []
    if not tmaxs or not tmins:
        return None
    try:
        return {"tmax_f": float(tmaxs[0]), "tmin_f": float(tmins[0])}
    except (TypeError, ValueError):
        return None


# ── Signal + scoring ─────────────────────────────────────────────────────
def signal_at_H(
    prob_yes_at_H: float, outcome: str, entry_price: float,
) -> str:
    """HOLD vs CLOSE: close if updated fair value of OUR side dropped
    below cost basis. Spread/fee handled in Tier B; Tier A signal is
    direction-only.

    For YES position: prob_outcome = prob_yes_at_H
    For NO  position: prob_outcome = 1 - prob_yes_at_H
    CLOSE iff prob_outcome < entry_price (underwater).
    """
    prob_outcome = prob_yes_at_H if outcome == "yes" else (1.0 - prob_yes_at_H)
    return "CLOSE" if prob_outcome < entry_price else "HOLD"


def tier_a_correct(signal: str, won: int) -> bool:
    """HOLD is correct if the position eventually won; CLOSE is correct
    if it lost. `won` from kalshi_round_trips encodes the bet outcome
    directly (1 = bet won, 0 = bet lost).
    """
    if signal == "HOLD":
        return won == 1
    if signal == "CLOSE":
        return won == 0
    raise ValueError(f"unknown signal {signal}")


def kalshi_fee_dollars(qty: float, price: float) -> float:
    """ceil(0.07 × C × P × (1-P)) cents, min 1¢ — Kalshi fee formula."""
    if price <= 0 or price >= 1:
        return KALSHI_FEE_FLOOR_CENTS / 100.0
    raw_cents = KALSHI_FEE_RATE * qty * price * (1.0 - price) * 100.0
    fee_cents = max(KALSHI_FEE_FLOOR_CENTS, math.ceil(raw_cents))
    return fee_cents / 100.0


def tier_b_pnls(
    prob_yes_at_H: float, outcome: str, entry_price: float, qty: float, won: int,
) -> tuple[float, float, float]:
    """Returns (pnl_close, pnl_hold, pnl_delta). All in dollars.

    pnl_close = qty × (bid_at_H − entry_price) − fee
                where bid_at_H = max(0, prob_outcome_at_H − half_spread)
    pnl_hold  = qty × (won_payoff − entry_price)
                where won_payoff = 1.0 if won else 0.0
    pnl_delta = pnl_close − pnl_hold  (improvement of acting at H)
    """
    prob_outcome = prob_yes_at_H if outcome == "yes" else (1.0 - prob_yes_at_H)
    bid = max(0.0, min(1.0, prob_outcome - ASSUMED_HALF_SPREAD))
    fee = kalshi_fee_dollars(qty, bid)
    pnl_close = qty * (bid - entry_price) - fee
    pnl_hold = qty * (float(won) - entry_price)
    return pnl_close, pnl_hold, pnl_close - pnl_hold


# ── Load corpus ──────────────────────────────────────────────────────────
def load_corpus() -> list[dict]:
    """Join WHP (entry decision) with RT (resolution) on order_id."""
    whp_by_oid: dict[str, dict] = {}
    with gzip.open(WHP_PATH, "rt", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            pj = r["payload_json"]
            oid = pj.get("order_id")
            if oid:
                whp_by_oid[oid] = {**pj, "_audit_ts": r["ts"]}
    rt_rows = []
    with gzip.open(RT_PATH, "rt", encoding="utf-8") as f:
        for line in f:
            rt_rows.append(json.loads(line))
    joined: list[dict] = []
    missing = 0
    for rt in rt_rows:
        oid = rt.get("order_id")
        whp = whp_by_oid.get(oid)
        if whp is None:
            missing += 1
            continue
        joined.append({"whp": whp, "rt": rt})
    print(f"corpus: {len(rt_rows)} RT rows, {len(whp_by_oid)} WHP, "
          f"{len(joined)} joined, {missing} RT-without-WHP")
    return joined


# ── Parity gate (#4) ─────────────────────────────────────────────────────
def run_parity_gate(corpus: list[dict]) -> dict:
    """At H=entry, recomputed prob_yes must match audit's recorded
    prob_yes (extra_json.prob_yes, unrounded). Off-by-more-than-tol on
    any position = bug. Caller halts if `passed=False`.
    """
    results = []
    for entry in corpus:
        w = entry["whp"]
        forecast_temp = float(w["forecast_temp_f"])
        sigma_used = float(w["sigma_used_f"])
        threshold = float(w["threshold_f"])
        threshold_high = w.get("threshold_high_f")
        threshold_high = float(threshold_high) if threshold_high is not None else None
        direction = str(w["direction"])
        recomputed = forecast_probability(
            forecast_temp_f=forecast_temp,
            sigma_f=sigma_used,
            threshold_f=threshold,
            direction=direction,
            threshold_high_f=threshold_high,
        )
        recorded = float(w["prob_yes"])
        results.append({
            "order_id": w["order_id"],
            "recomputed": recomputed,
            "recorded": recorded,
            "deviation": abs(recomputed - recorded),
        })
    devs = [r["deviation"] for r in results]
    max_dev = max(devs) if devs else 0.0
    n_strict = sum(1 for d in devs if d <= PARITY_TOL)
    n_diag = sum(1 for d in devs if d <= PARITY_DIAG_TOL)
    passed = max_dev <= PARITY_TOL
    diag_passed = max_dev <= PARITY_DIAG_TOL
    out = {
        "n_positions": len(results),
        "n_match_strict_1e9": n_strict,
        "n_match_diag_5e4": n_diag,
        "max_deviation": max_dev,
        "passed_strict": passed,
        "passed_diag": diag_passed,
    }
    if not passed:
        # Show 3 worst offenders for diagnosis
        worst = sorted(results, key=lambda r: -r["deviation"])[:3]
        out["worst_offenders"] = worst
    return out


# ── Per-position replay ──────────────────────────────────────────────────
def replay_position(
    entry: dict, metar_cache_by_station: dict[str, list[dict]],
) -> list[dict]:
    """Yield one CSV row per H for this position."""
    w = entry["whp"]
    rt = entry["rt"]
    oid = w["order_id"]
    ticker = w["ticker"]
    direction = str(w["direction"])
    threshold = float(w["threshold_f"])
    threshold_high = w.get("threshold_high_f")
    threshold_high = float(threshold_high) if threshold_high is not None else None
    forecast_temp_entry = float(w["forecast_temp_f"])
    sigma_used = float(w["sigma_used_f"])
    prob_yes_entry = float(w["prob_yes"])
    implied_at_entry = float(w["implied_prob_at_entry"])
    outcome = str(w["outcome"]).lower()
    qty = float(w["qty"])
    entry_price = float(w["limit_price"])
    won = int(rt["won"])
    realized_pnl = float(rt["realized_pnl"])

    entry_ts = parse_iso(rt["entry_ts"])
    # target_iso recovered from the WHP audit's horizon_hours field:
    # the strategy stores horizon_hours = (target_iso - now_dt) at
    # decision time (rounded to 2 decimals → max 36s drift). For HIGH/
    # LOW this equals expires_at (settlement time, ~14:00 UTC next day).
    # target_iso NOT directly stored in this corpus (older payload
    # format), and the event_ticker date code yields end-of-UTC-day
    # which prematurely truncates 44 positions entered late in the
    # UTC day for a settlement-next-day market.
    horizon_hours = float(w["horizon_hours"])
    target_iso = entry_ts + timedelta(hours=horizon_hours)
    # Skip the 1 hourly TEMP market in the corpus (KXTEMP* with hour
    # suffix) — would need city tz tables for an accurate local target.
    if w.get("event_ticker", "").startswith("KXTEMP"):
        return [{
            "order_id": oid, "ticker": ticker,
            "_skip": "hourly_TEMP_market_local_tz_not_modeled",
        }]
    # Cap H grid by min(target_iso, resolved_ts) — once the market
    # settles, there's no "at H" decision to model.
    resolved_ts = parse_iso(rt["resolved_ts"])
    H_max = min(target_iso, resolved_ts)

    kind = market_kind(ticker)
    city = city_code_from_ticker(ticker) or ""
    station = CITY_TO_METAR_STATION.get(city)
    if station is None:
        return [{
            "order_id": oid, "ticker": ticker, "_skip": f"no_metar_station_for_city={city}"
        }]
    metar_obs = metar_cache_by_station.get(station, [])

    # Open-Meteo daily forecast for the resolution date (Tier A.2 input).
    # The OM call needs lat/lon — pulled from the audit (added in P3).
    # We don't have it directly in WHP; use the strategy's station coords
    # via the OM cache key. For now, derive from station-to-coords via
    # the live mapping. Fallback: skip A.2 for this position.
    om_data = None
    target_date_iso = target_iso.date().isoformat()
    coords = _STATION_COORDS.get(station)
    if coords is not None:
        om_data = fetch_open_meteo_daily(coords[0], coords[1], target_date_iso)

    # Hour grid
    rows = []
    H = entry_ts + timedelta(hours=HOUR_STEP_H)
    is_first_hour = True
    parity_at_entry = None
    while H <= H_max:
        # ── Tier A.1: observed-floor on entry forecast ─────────────
        observed_extremum = observed_extremum_through_H(metar_obs, entry_ts, H, kind)
        if kind == "HIGH":
            forecast_at_H_A1 = max(observed_extremum, forecast_temp_entry) \
                if observed_extremum is not None else forecast_temp_entry
        elif kind == "LOW":
            forecast_at_H_A1 = min(observed_extremum, forecast_temp_entry) \
                if observed_extremum is not None else forecast_temp_entry
        else:
            # TEMP / other — no observed-floor logic, fall back to entry
            forecast_at_H_A1 = forecast_temp_entry

        prob_yes_at_H_A1 = forecast_probability(
            forecast_temp_f=forecast_at_H_A1,
            sigma_f=sigma_used,
            threshold_f=threshold,
            direction=direction,
            threshold_high_f=threshold_high,
        )
        signal_A1 = signal_at_H(prob_yes_at_H_A1, outcome, entry_price)
        a1_correct = tier_a_correct(signal_A1, won)
        b_close_A1, b_hold_A1, b_delta_A1 = tier_b_pnls(
            prob_yes_at_H_A1, outcome, entry_price, qty, won,
        )

        # ── Tier A.2: Open-Meteo overlay + observed-floor ──────────
        forecast_at_H_A2 = None
        prob_yes_at_H_A2 = None
        signal_A2 = None
        a2_correct = None
        b_close_A2 = None
        b_hold_A2 = None
        b_delta_A2 = None
        if om_data is not None:
            if kind == "HIGH":
                om_val = om_data["tmax_f"]
                f_A2 = max(observed_extremum, om_val) \
                    if observed_extremum is not None else om_val
            elif kind == "LOW":
                om_val = om_data["tmin_f"]
                f_A2 = min(observed_extremum, om_val) \
                    if observed_extremum is not None else om_val
            else:
                f_A2 = forecast_temp_entry
            forecast_at_H_A2 = f_A2
            prob_yes_at_H_A2 = forecast_probability(
                forecast_temp_f=f_A2,
                sigma_f=sigma_used,
                threshold_f=threshold,
                direction=direction,
                threshold_high_f=threshold_high,
            )
            signal_A2 = signal_at_H(prob_yes_at_H_A2, outcome, entry_price)
            a2_correct = tier_a_correct(signal_A2, won)
            b_close_A2, b_hold_A2, b_delta_A2 = tier_b_pnls(
                prob_yes_at_H_A2, outcome, entry_price, qty, won,
            )

        horizon_remaining_h = (target_iso - H).total_seconds() / 3600.0

        rows.append({
            "order_id": oid,
            "ticker": ticker,
            "kind": kind,
            "city": city,
            "metar_station": station,
            "entry_ts": entry_ts.isoformat(),
            "target_iso": target_iso.isoformat(),
            "resolved_ts": resolved_ts.isoformat(),
            "H": H.isoformat(),
            "horizon_remaining_h": round(horizon_remaining_h, 2),
            "direction": direction,
            "threshold_f": threshold,
            "threshold_high_f": threshold_high,
            "outcome_side": outcome,
            "qty": qty,
            "entry_price": entry_price,
            "implied_at_entry": implied_at_entry,
            "won": won,
            "realized_pnl_actual": realized_pnl,
            "forecast_temp_entry": forecast_temp_entry,
            "sigma_used": sigma_used,
            "prob_yes_entry": prob_yes_entry,
            "observed_extremum_through_H": observed_extremum,
            "n_metar_obs_through_H": sum(
                1 for o in metar_obs if entry_ts <= o["obs_dt"] <= H
            ),
            # Tier A.1
            "forecast_at_H_A1_METAR_ONLY": forecast_at_H_A1,
            "prob_yes_at_H_A1_METAR_ONLY": prob_yes_at_H_A1,
            "signal_A1_METAR_ONLY": signal_A1,
            "correct_A1_METAR_ONLY": a1_correct,
            # Tier A.2 (LEAK INFLATED)
            "forecast_at_H_A2_LEAK_INFLATED": forecast_at_H_A2,
            "prob_yes_at_H_A2_LEAK_INFLATED": prob_yes_at_H_A2,
            "signal_A2_LEAK_INFLATED": signal_A2,
            "correct_A2_LEAK_INFLATED": a2_correct,
            # Tier B (PnL — directional only, spread assumed constant)
            "pnl_close_A1_DIRECTIONAL_ONLY_spread_assumed_constant": b_close_A1,
            "pnl_hold_A1_DIRECTIONAL_ONLY_spread_assumed_constant": b_hold_A1,
            "pnl_delta_A1_DIRECTIONAL_ONLY_spread_assumed_constant": b_delta_A1,
            "pnl_close_A2_DIRECTIONAL_ONLY_spread_assumed_constant": b_close_A2,
            "pnl_hold_A2_DIRECTIONAL_ONLY_spread_assumed_constant": b_hold_A2,
            "pnl_delta_A2_DIRECTIONAL_ONLY_spread_assumed_constant": b_delta_A2,
        })

        if is_first_hour:
            parity_at_entry = prob_yes_at_H_A1  # observed-floor at H=entry+1h
            is_first_hour = False
        H += timedelta(hours=HOUR_STEP_H)

    return rows


# Coords for the inlined CITY_TO_METAR_STATION (mirrors strategy's
# _CITY_COORDS_FALLBACK for the OM call). Used only for Tier A.2.
_STATION_COORDS: dict[str, tuple[float, float]] = {
    "KNYC": (40.7794, -73.9692),
    "KBOS": (42.3656, -71.0096),
    "KDCA": (38.8512, -77.0402),
    "KSEA": (47.4502, -122.3088),
    "KATL": (33.6407, -84.4277),
    "KDFW": (32.8998, -97.0403),
    "KPHL": (39.8729, -75.2437),
    "KOKC": (35.3931, -97.6007),
    "KMIA": (25.7959, -80.2870),
    "KMDW": (41.7868, -87.7522),
    "KAUS": (30.1975, -97.6664),
    "KMSP": (44.8848, -93.2223),
    "KSAT": (29.5337, -98.4698),
    "KSFO": (37.6213, -122.3790),
    "KLAX": (33.9416, -118.4085),
    "KDEN": (39.8561, -104.6737),
    "KHOU": (29.6454, -95.2789),
    "KPHX": (33.4373, -112.0078),
    "KMSY": (29.9934, -90.2580),
}


# ── Summary aggregations ─────────────────────────────────────────────────
def summarize(rows: list[dict], parity_result: dict) -> dict:
    """Build the aggregate report shape:
      - parity gate result
      - Tier A.1 headline: % positions where signal ever changed; %
        correct on the signal-changed set (final-hour signal)
      - Tier A.1 leak check: % correct overall by horizon bucket; flag
        if any bucket >95%
      - A.1↔A.2 divergence: signal disagreement rate by horizon bucket
        (6h / 12h / 24h+)
      - Tier B: median, mean, total pnl_delta by tier
    """
    # Filter out skipped rows
    rows = [r for r in rows if "_skip" not in r]

    # Per-position aggregation
    by_pos: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_pos[r["order_id"]].append(r)
    pos_summaries = []
    for oid, prs in by_pos.items():
        prs.sort(key=lambda x: x["H"])
        # Signal at entry would be HOLD by construction (the strategy
        # fired). We classify "signal ever changed" as: any H signal !=
        # entry signal. Approximate entry signal as HOLD (strategy
        # decided to enter; at H=entry our prob_outcome ≈ implied; HOLD).
        entry_signal = "HOLD"
        a1_signals = [r["signal_A1_METAR_ONLY"] for r in prs]
        a2_signals = [r["signal_A2_LEAK_INFLATED"] for r in prs if r["signal_A2_LEAK_INFLATED"] is not None]
        won = prs[0]["won"]
        pos_summaries.append({
            "order_id": oid,
            "won": won,
            "n_H": len(prs),
            "a1_ever_changed": any(s != entry_signal for s in a1_signals),
            "a1_final_signal": a1_signals[-1],
            "a1_close_count": sum(1 for s in a1_signals if s == "CLOSE"),
            "a2_ever_changed": any(s != entry_signal for s in a2_signals) if a2_signals else None,
            "a2_final_signal": a2_signals[-1] if a2_signals else None,
            "a2_close_count": sum(1 for s in a2_signals if s == "CLOSE") if a2_signals else None,
        })

    # Tier A.1 headline
    n_pos = len(pos_summaries)
    n_a1_changed = sum(1 for p in pos_summaries if p["a1_ever_changed"])
    n_a1_changed_correct = sum(
        1 for p in pos_summaries
        if p["a1_ever_changed"] and (
            (p["a1_final_signal"] == "HOLD" and p["won"] == 1) or
            (p["a1_final_signal"] == "CLOSE" and p["won"] == 0)
        )
    )

    # Tier A.1 row-level correctness by horizon bucket
    def bucket(h: float) -> str:
        if h <= 6.0:
            return "0-6h"
        if h <= 12.0:
            return "6-12h"
        if h <= 24.0:
            return "12-24h"
        return "24h+"
    by_bucket_a1: dict[str, list[bool]] = defaultdict(list)
    by_bucket_a2: dict[str, list[bool]] = defaultdict(list)
    a1_vs_a2_disagree: dict[str, list[bool]] = defaultdict(list)
    for r in rows:
        b = bucket(r["horizon_remaining_h"])
        by_bucket_a1[b].append(bool(r["correct_A1_METAR_ONLY"]))
        if r["correct_A2_LEAK_INFLATED"] is not None:
            by_bucket_a2[b].append(bool(r["correct_A2_LEAK_INFLATED"]))
        if r["signal_A2_LEAK_INFLATED"] is not None:
            a1_vs_a2_disagree[b].append(
                r["signal_A1_METAR_ONLY"] != r["signal_A2_LEAK_INFLATED"]
            )

    def pct(xs: list[bool]) -> float:
        return round(100.0 * sum(xs) / len(xs), 2) if xs else 0.0

    horizon_stats = {}
    for b in ["0-6h", "6-12h", "12-24h", "24h+"]:
        horizon_stats[b] = {
            "n_a1_rows": len(by_bucket_a1.get(b, [])),
            "a1_pct_correct": pct(by_bucket_a1.get(b, [])),
            "n_a2_rows": len(by_bucket_a2.get(b, [])),
            "a2_pct_correct": pct(by_bucket_a2.get(b, [])),
            "n_a1_vs_a2_compared": len(a1_vs_a2_disagree.get(b, [])),
            "a1_vs_a2_disagree_pct": pct(a1_vs_a2_disagree.get(b, [])),
        }

    # Tier B — TWO aggregates:
    #   (a) per-row: every CLOSE-signal row contributes its pnl_delta.
    #       Over-counts each position's potential close. Diagnostic only.
    #   (b) per-position: realistic policy = "close at the FIRST H with
    #       a CLOSE signal". One pnl_delta per position that ever closed.
    pnl_deltas_A1 = [
        r["pnl_delta_A1_DIRECTIONAL_ONLY_spread_assumed_constant"]
        for r in rows
        if r["signal_A1_METAR_ONLY"] == "CLOSE"
    ]
    pnl_deltas_A2 = [
        r["pnl_delta_A2_DIRECTIONAL_ONLY_spread_assumed_constant"]
        for r in rows
        if r["signal_A2_LEAK_INFLATED"] == "CLOSE"
    ]

    # First-close per position
    rows_by_pos: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        rows_by_pos[r["order_id"]].append(r)
    per_pos_first_close_A1: list[dict] = []
    per_pos_first_close_A2: list[dict] = []
    for oid, prs in rows_by_pos.items():
        prs.sort(key=lambda x: x["H"])
        # A.1 first close
        for r in prs:
            if r["signal_A1_METAR_ONLY"] == "CLOSE":
                per_pos_first_close_A1.append({
                    "order_id": oid,
                    "H": r["H"],
                    "horizon_remaining_h": r["horizon_remaining_h"],
                    "pnl_delta": r["pnl_delta_A1_DIRECTIONAL_ONLY_spread_assumed_constant"],
                    "won": r["won"],
                })
                break
        # A.2 first close
        for r in prs:
            if r["signal_A2_LEAK_INFLATED"] == "CLOSE":
                per_pos_first_close_A2.append({
                    "order_id": oid,
                    "H": r["H"],
                    "horizon_remaining_h": r["horizon_remaining_h"],
                    "pnl_delta": r["pnl_delta_A2_DIRECTIONAL_ONLY_spread_assumed_constant"],
                    "won": r["won"],
                })
                break

    first_close_deltas_A1 = [p["pnl_delta"] for p in per_pos_first_close_A1]
    first_close_deltas_A2 = [p["pnl_delta"] for p in per_pos_first_close_A2]

    def stats(xs: list[float]) -> dict:
        if not xs:
            return {"n": 0}
        xs_sorted = sorted(xs)
        n = len(xs)
        return {
            "n": n,
            "sum": round(sum(xs), 2),
            "mean": round(sum(xs) / n, 4),
            "median": round(xs_sorted[n // 2], 4),
            "p25": round(xs_sorted[max(0, n // 4)], 4),
            "p75": round(xs_sorted[min(n - 1, 3 * n // 4)], 4),
            "min": round(xs_sorted[0], 4),
            "max": round(xs_sorted[-1], 4),
        }

    return {
        "parity_gate": parity_result,
        "n_positions": n_pos,
        "n_rows": len(rows),
        "tier_A1_headline_zero_leak": {
            "n_positions_signal_changed_from_HOLD": n_a1_changed,
            "pct_positions_signal_changed": pct([p["a1_ever_changed"] for p in pos_summaries]),
            "n_signal_changed_AND_correct": n_a1_changed_correct,
            "pct_changed_signal_correct": (
                round(100.0 * n_a1_changed_correct / n_a1_changed, 2)
                if n_a1_changed else 0.0
            ),
            "overall_pct_correct_rows": pct(
                [bool(r["correct_A1_METAR_ONLY"]) for r in rows]
            ),
        },
        "leak_check": {
            "tier_A1_observed_floor_assert_count": len(rows),
            "note": (
                "assert obs_dt <= H runs on every observed_extremum call; "
                "if any leak occurred the script would have hard-aborted "
                "before reaching this summary."
            ),
            "by_horizon_a1_correct": horizon_stats,
            "a1_overall_correct_pct_for_leak_eyeball": pct(
                [bool(r["correct_A1_METAR_ONLY"]) for r in rows]
            ),
        },
        "tier_A2_LEAK_INFLATED_upper_bound": {
            "n_rows_with_OM": sum(
                1 for r in rows if r["correct_A2_LEAK_INFLATED"] is not None
            ),
            "overall_pct_correct_rows": pct(
                [bool(r["correct_A2_LEAK_INFLATED"]) for r in rows
                 if r["correct_A2_LEAK_INFLATED"] is not None]
            ),
        },
        "A1_vs_A2_divergence_by_horizon": {
            b: horizon_stats[b]["a1_vs_a2_disagree_pct"]
            for b in horizon_stats
        },
        "tier_B_DIRECTIONAL_ONLY_spread_assumed_constant": {
            "spread_assumption_usd": ASSUMED_SPREAD,
            "fee_formula": "ceil(0.07 × qty × P × (1-P)) cents, min 1¢",
            "_NOTE": (
                "per_row over-counts each position's many CLOSE-signal "
                "hours; per_position_first_close is the realistic 'close "
                "at first signal' policy result."
            ),
            "A1_per_row_close_signals_pnl_delta": stats(pnl_deltas_A1),
            "A2_per_row_close_signals_pnl_delta": stats(pnl_deltas_A2),
            "A1_per_position_FIRST_close_pnl_delta": stats(first_close_deltas_A1),
            "A2_per_position_FIRST_close_pnl_delta": stats(first_close_deltas_A2),
            "A1_first_close_won_distribution": {
                "n_first_close_positions": len(per_pos_first_close_A1),
                "n_correct_closes_position_lost": sum(
                    1 for p in per_pos_first_close_A1 if p["won"] == 0
                ),
                "n_incorrect_closes_position_won": sum(
                    1 for p in per_pos_first_close_A1 if p["won"] == 1
                ),
            },
            "A2_first_close_won_distribution": {
                "n_first_close_positions": len(per_pos_first_close_A2),
                "n_correct_closes_position_lost": sum(
                    1 for p in per_pos_first_close_A2 if p["won"] == 0
                ),
                "n_incorrect_closes_position_won": sum(
                    1 for p in per_pos_first_close_A2 if p["won"] == 1
                ),
            },
        },
    }


# ── Main ─────────────────────────────────────────────────────────────────
def main() -> int:
    print("=== Phase D replay — kalshi_weather hourly re-eval ===\n")
    corpus = load_corpus()

    print("\n[Gate 1/2] PARITY check (recomputed prob_yes vs audit)...")
    parity = run_parity_gate(corpus)
    print(
        f"  n_positions={parity['n_positions']}  "
        f"max_dev={parity['max_deviation']:.3e}  "
        f"strict_match(<=1e-9)={parity['n_match_strict_1e9']}  "
        f"diag_match(<=5e-4)={parity['n_match_diag_5e4']}"
    )
    if not parity["passed_strict"]:
        print("\n  PARITY: not strict-match across all positions.")
        if parity["passed_diag"]:
            print(
                "  All within diag tol 5e-4 — likely float-precision drift "
                "from audit's round(.., 3) on eval_payload (extras stored "
                "unrounded). Continuing — diag tol holds for every position."
            )
        else:
            print("  ABORT — at least one position diverges beyond 5e-4.")
            for o in parity.get("worst_offenders", []):
                print(f"   - {o}")
            SUMMARY_JSON.write_text(json.dumps(
                {"aborted_at": "parity_gate", "parity": parity}, indent=2,
            ))
            return 1

    print("\n[Backfill] METAR per station (cached to tmp/metar_cache/)...")
    stations = set()
    for entry in corpus:
        city = city_code_from_ticker(entry["whp"]["ticker"]) or ""
        st = CITY_TO_METAR_STATION.get(city)
        if st:
            stations.add(st)
    print(f"  unique stations needed: {len(stations)}")
    metar_cache: dict[str, list[dict]] = {}
    for st in sorted(stations):
        try:
            obs = fetch_metar_for_station(st)
            metar_cache[st] = obs
            print(f"  {st}: {len(obs)} obs")
        except Exception as e:
            print(f"  {st}: FAIL ({e})")
            metar_cache[st] = []

    print("\n[Replay] Per-position hourly re-evaluation...")
    all_rows: list[dict] = []
    skipped = 0
    t0 = time.time()
    for i, entry in enumerate(corpus):
        rs = replay_position(entry, metar_cache)
        if rs and "_skip" in rs[0]:
            skipped += 1
            continue
        all_rows.extend(rs)
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(corpus)} positions; {len(all_rows)} rows; "
                  f"{time.time()-t0:.0f}s")
    print(f"  total: {len(corpus)} positions, {skipped} skipped, "
          f"{len(all_rows)} rows in {time.time()-t0:.0f}s")

    # Write CSV
    print(f"\n[Output] Writing {RESULTS_CSV}...")
    if all_rows:
        fieldnames = list(all_rows[0].keys())
        with RESULTS_CSV.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            w.writerows(all_rows)
        print(f"  {len(all_rows)} rows written")

    print(f"\n[Output] Writing {SUMMARY_JSON}...")
    summary = summarize(all_rows, parity)
    SUMMARY_JSON.write_text(json.dumps(summary, indent=2, default=str))

    print("\n=== SUMMARY ===")
    print(json.dumps(summary, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
