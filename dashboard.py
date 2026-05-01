"""AI-Powered Trading Corporation — Operations Dashboard.

Usage:
    streamlit run dashboard.py

Reads from:
  - SQLite audit log (fills, orders, events, briefs, account state)
  - Robinhood API (live option positions, cached 2 min)
  - TradingView MCP (indicator signals, if ENABLE_TRADINGVIEW=1)
  - TradingView Widgets + Lightweight Charts (browser-side charting)

All access is read-only. No orders are placed from this dashboard.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import dotenv_values

# Allow asyncio.run() inside Streamlit's thread
try:
    import nest_asyncio
    nest_asyncio.apply()
except ImportError:
    pass

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

# ---------------------------------------------------------------------------
# Page config  (must be first Streamlit call)
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="Trading Corp",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
  .block-container { padding-top: 1rem; padding-bottom: 1rem; }
  div[data-testid="metric-container"] {
      background: #1a1d27;
      border: 1px solid #2d3139;
      border-radius: 8px;
      padding: 14px 18px;
  }
  .dte-ok   { color: #00d4aa; font-weight: 600; }
  .dte-warn { color: #ffa500; font-weight: 600; }
  .dte-crit { color: #ff4b4b; font-weight: 600; }
  .tag {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 0.78em;
      font-weight: 600;
  }
  .tag-buy  { background:#00d4aa22; color:#00d4aa; }
  .tag-sell { background:#ff4b4b22; color:#ff4b4b; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Environment / DB
# ---------------------------------------------------------------------------

_ENV = dotenv_values(".env")


def _db_url() -> str:
    return _ENV.get("TRADING_CORP_DB_URL") or os.getenv(
        "TRADING_CORP_DB_URL", "sqlite:///data/trading_corp.db"
    )


def _db_path() -> Path:
    url = _db_url()
    if url.startswith("sqlite:///"):
        return Path(url[10:])
    if url.startswith("sqlite://"):
        return Path(url[9:])
    return Path(url)


# ---------------------------------------------------------------------------
# Async helper
# ---------------------------------------------------------------------------

def _run(coro):
    """Run a coroutine from sync Streamlit context."""
    try:
        return asyncio.run(coro)
    except RuntimeError:
        # Event loop already running (some Streamlit environments)
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()


# ---------------------------------------------------------------------------
# DB loaders
# ---------------------------------------------------------------------------

def _conn():
    db = _db_path()
    if not db.exists():
        return None
    return sqlite3.connect(db, check_same_thread=False)


@st.cache_data(ttl=30)
def load_orders() -> pd.DataFrame:
    c = _conn()
    if c is None:
        return pd.DataFrame()
    try:
        df = pd.read_sql(
            "SELECT * FROM proposed_order ORDER BY ts DESC LIMIT 200", c
        )
        return df
    except Exception:
        return pd.DataFrame()
    finally:
        c.close()


@st.cache_data(ttl=30)
def load_events() -> pd.DataFrame:
    c = _conn()
    if c is None:
        return pd.DataFrame()
    try:
        df = pd.read_sql(
            "SELECT * FROM audit_event ORDER BY ts DESC LIMIT 200", c
        )
        return df
    except Exception:
        return pd.DataFrame()
    finally:
        c.close()


@st.cache_data(ttl=60)
def load_account_states() -> pd.DataFrame:
    c = _conn()
    if c is None:
        return pd.DataFrame()
    try:
        return pd.read_sql("SELECT * FROM account_state", c)
    except Exception:
        return pd.DataFrame()
    finally:
        c.close()


@st.cache_data(ttl=60)
def load_strategy_states() -> pd.DataFrame:
    c = _conn()
    if c is None:
        return pd.DataFrame()
    try:
        return pd.read_sql("SELECT * FROM strategy_state", c)
    except Exception:
        return pd.DataFrame()
    finally:
        c.close()


@st.cache_data(ttl=60)
def load_briefs() -> pd.DataFrame:
    c = _conn()
    if c is None:
        return pd.DataFrame()
    try:
        return pd.read_sql(
            "SELECT * FROM daily_brief ORDER BY trading_day DESC LIMIT 10", c
        )
    except Exception:
        return pd.DataFrame()
    finally:
        c.close()


# ---------------------------------------------------------------------------
# Broker data (Robinhood — cached 2 min)
# ---------------------------------------------------------------------------

@st.cache_resource
def _rh_broker():
    user = _ENV.get("ROBINHOOD_USERNAME") or os.getenv("ROBINHOOD_USERNAME")
    pwd  = _ENV.get("ROBINHOOD_PASSWORD") or os.getenv("ROBINHOOD_PASSWORD")
    mfa  = _ENV.get("ROBINHOOD_MFA_SECRET") or os.getenv("ROBINHOOD_MFA_SECRET")
    if not user or not pwd:
        return None
    try:
        from trading_corp.brokers.robinhood import RobinhoodBroker
        b = RobinhoodBroker(username=user, password=pwd, mfa_secret=mfa or None)
        _run(b.connect())
        return b
    except Exception as e:
        st.warning(f"Robinhood login failed: {e}")
        return None


@st.cache_data(ttl=120)
def rh_snapshot() -> dict | None:
    """Return AccountSnapshot as a plain dict so cache_data can pickle it."""
    b = _rh_broker()
    if not b:
        return None
    try:
        snap = _run(b.snapshot())
        return {
            "account":      snap.account,
            "equity":       snap.equity,
            "buying_power": snap.buying_power,
            "cash":         snap.cash,
            "positions": [
                {
                    "account":   p.account,
                    "symbol":    p.symbol,
                    "qty":       p.qty,
                    "avg_price": p.avg_price,
                    "opened_ts": p.opened_ts,
                    "extra":     p.extra,
                }
                for p in snap.positions
            ],
        }
    except Exception:
        return None


@st.cache_data(ttl=120)
def rh_options_detail() -> list[dict]:
    b = _rh_broker()
    if not b:
        return []
    try:
        return _run(b.get_option_positions_detail())
    except Exception:
        return []


@st.cache_data(ttl=120)
def spot_prices(symbols: tuple) -> dict[str, float]:
    """Current last price for each symbol via yfinance."""
    if not symbols:
        return {}
    try:
        import yfinance as yf  # type: ignore
        result: dict[str, float] = {}
        for sym in symbols:
            try:
                info = yf.Ticker(sym).fast_info
                p = getattr(info, "last_price", None) or getattr(info, "previous_close", None)
                result[sym] = float(p) if p else 0.0
            except Exception:
                result[sym] = 0.0
        return result
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# TradingView MCP indicators
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300)
def tv_indicators(symbol: str) -> dict:
    try:
        from trading_corp.data.tradingview import is_available, supplemental_indicators
        if not is_available():
            return {}
        return _run(supplemental_indicators(symbol))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# TradingView Lightweight Charts
# ---------------------------------------------------------------------------

_LW_CDN = (
    "https://unpkg.com/lightweight-charts@4.1.3"
    "/dist/lightweight-charts.standalone.production.js"
)


def _lw_chart(
    series: list[dict],
    height: int = 260,
    color: str = "#00d4aa",
    chart_type: str = "area",   # "area" | "histogram" | "line"
    title: str = "",
) -> str:
    uid = abs(hash(title + str(series[:2]))) % 999_999
    add_fn = {
        "area":      "addAreaSeries",
        "histogram": "addHistogramSeries",
        "line":      "addLineSeries",
    }.get(chart_type, "addAreaSeries")

    series_opts: dict = {}
    if chart_type == "area":
        series_opts = {
            "lineColor": color,
            "topColor": color + "55",
            "bottomColor": color + "00",
            "lineWidth": 2,
        }
    elif chart_type == "histogram":
        series_opts = {"color": color}
    elif chart_type == "line":
        series_opts = {"color": color, "lineWidth": 2}

    return f"""
<div id="lw_{uid}" style="width:100%;height:{height}px;"></div>
<script src="{_LW_CDN}"></script>
<script>
(function(){{
  var el = document.getElementById('lw_{uid}');
  var chart = LightweightCharts.createChart(el, {{
    width: el.clientWidth, height: {height},
    layout: {{ background:{{color:'#0e1117'}}, textColor:'#c9d1d9' }},
    grid:   {{ vertLines:{{color:'#1c2333'}}, horzLines:{{color:'#1c2333'}} }},
    timeScale: {{ borderColor:'#2d3139', timeVisible:true }},
    rightPriceScale: {{ borderColor:'#2d3139' }},
  }});
  var s = chart.{add_fn}({json.dumps(series_opts)});
  s.setData({json.dumps(series)});
  new ResizeObserver(function(e){{
    chart.applyOptions({{width: e[0].contentRect.width}});
  }}).observe(el);
}})();
</script>"""


# ---------------------------------------------------------------------------
# TradingView Widgets
# ---------------------------------------------------------------------------

def _tv_advanced_chart(symbol: str, height: int = 460) -> str:
    uid = symbol.replace("/", "_").replace(":", "_")
    return f"""
<div style="height:{height}px;width:100%;">
  <div id="tvc_{uid}" style="height:100%;width:100%;"></div>
  <script src="https://s3.tradingview.com/tv.js"></script>
  <script>
  new TradingView.widget({{
    autosize:true, symbol:"{symbol}", interval:"D",
    timezone:"America/New_York", theme:"dark", style:"1", locale:"en",
    hide_top_toolbar:false, hide_legend:false, save_image:false,
    studies:["RSI@tv-basicstudies","MACD@tv-basicstudies"],
    container_id:"tvc_{uid}"
  }});
  </script>
</div>"""


def _tv_mini(symbol: str, height: int = 210) -> str:
    cfg = json.dumps({
        "symbol": symbol, "width": "100%", "height": height,
        "locale": "en", "dateRange": "3M", "colorTheme": "dark",
        "trendLineColor": "rgba(0,212,170,1)",
        "underLineColor": "rgba(0,212,170,0.15)",
        "underLineBottomColor": "rgba(0,212,170,0)",
        "isTransparent": True, "autosize": True,
    })
    return f"""
<div style="height:{height}px;">
  <div class="tradingview-widget-container__widget"></div>
  <script src="https://s3.tradingview.com/external-embedding/embed-widget-mini-symbol-overview.js" async>
  {cfg}
  </script>
</div>"""


def _tv_technicals(symbol: str, height: int = 400) -> str:
    cfg = json.dumps({
        "interval": "1D", "width": "100%", "isTransparent": True,
        "height": height, "symbol": symbol, "showIntervalTabs": True,
        "displayMode": "single", "locale": "en", "colorTheme": "dark",
    })
    return f"""
<div style="height:{height}px;">
  <div class="tradingview-widget-container__widget"></div>
  <script src="https://s3.tradingview.com/external-embedding/embed-widget-technical-analysis.js" async>
  {cfg}
  </script>
</div>"""


def _tv_ticker_tape(symbols: list[str]) -> str:
    syms = [{"proName": s, "title": s.split(":")[-1]} for s in symbols]
    cfg = json.dumps({
        "symbols": syms, "showSymbolLogo": True, "isTransparent": True,
        "displayMode": "adaptive", "colorTheme": "dark", "locale": "en",
    })
    return f"""
<div style="height:60px;">
  <div class="tradingview-widget-container__widget"></div>
  <script src="https://s3.tradingview.com/external-embedding/embed-widget-ticker-tape.js" async>
  {cfg}
  </script>
</div>"""


# ---------------------------------------------------------------------------
# Equity curve helper
# ---------------------------------------------------------------------------

def _equity_curve_from_orders(orders: pd.DataFrame, start: float) -> list[dict]:
    """Build a daily equity curve from filled orders."""
    today = datetime.now(timezone.utc).date().isoformat()
    if orders.empty:
        return [{"time": today, "value": round(start, 2)}]

    filled = orders[orders.get("status", pd.Series()) == "filled"].copy()
    if filled.empty:
        return [{"time": today, "value": round(start, 2)}]

    filled["ts"] = pd.to_datetime(filled["ts"], utc=True, errors="coerce")
    filled = filled.dropna(subset=["ts"]).sort_values("ts")
    filled["date"] = filled["ts"].dt.date.astype(str)

    def _pnl(row) -> float:
        qty   = float(row.get("qty", 0) or 0)
        price = float(row.get("fill_price", 0) or row.get("limit_price", 0) or 0)
        sign  = -1 if str(row.get("side", "buy")).lower() == "buy" else 1
        return sign * qty * price

    filled["pnl"] = filled.apply(_pnl, axis=1)
    daily = filled.groupby("date")["pnl"].sum().cumsum()

    curve = []
    for d, cum in daily.items():
        curve.append({"time": str(d), "value": round(start + float(cum), 2)})

    if not curve:
        curve = [{"time": today, "value": round(start, 2)}]
    return curve


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def _sidebar():
    with st.sidebar:
        st.markdown("## 📈 Trading Corp")
        st.caption("Operations Dashboard")
        st.divider()

        mode = _ENV.get("TRADING_MODE", "PAPER")
        st.markdown(f"{'🟢' if mode == 'LIVE' else '🟡'} **{mode} MODE**")

        st.markdown("**Connections**")
        rh_ok  = bool(_ENV.get("ROBINHOOD_USERNAME"))
        fid_ok = bool(_ENV.get("FIDELITY_USERNAME"))
        tv_ok  = _ENV.get("ENABLE_TRADINGVIEW") == "1"
        st.markdown(f"{'✅' if rh_ok  else '❌'} Robinhood")
        st.markdown(f"{'✅' if fid_ok else '❌'} Fidelity")
        st.markdown(f"{'✅' if tv_ok  else '⚪'} TradingView MCP")

        db = _db_path()
        st.markdown("**Database**")
        if db.exists():
            size_kb = db.stat().st_size / 1024
            st.markdown(f"✅ `{db.name}` ({size_kb:.0f} KB)")
        else:
            st.markdown("⚪ No DB yet (start the system first)")

        st.divider()
        if st.button("🔄 Refresh", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

        st.caption(f"Updated: {datetime.now().strftime('%H:%M:%S')}")


# ---------------------------------------------------------------------------
# Tab: Overview
# ---------------------------------------------------------------------------

def _tab_overview():
    snap    = rh_snapshot()
    orders  = load_orders()
    acct_df = load_account_states()
    strat_df = load_strategy_states()

    # --- KPIs ---
    total_equity    = snap["equity"]          if snap else 0.0
    buying_power    = snap["buying_power"]    if snap else 0.0
    n_positions     = len(snap["positions"])  if snap else 0
    filled_orders   = len(orders[orders["status"] == "filled"]) if not orders.empty and "status" in orders.columns else 0

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Total Equity",    f"${total_equity:,.2f}")
    c2.metric("Buying Power",    f"${buying_power:,.2f}")
    c3.metric("Open Positions",  n_positions)
    c4.metric("Lifetime Fills",  filled_orders)

    # --- Equity curve ---
    st.divider()
    st.subheader("Equity Curve")
    curve = _equity_curve_from_orders(orders, start=total_equity or 100_000.0)
    st.components.v1.html(_lw_chart(curve, height=250, color="#00d4aa"), height=270)

    # --- Ticker tape ---
    st.components.v1.html(
        _tv_ticker_tape(["AMEX:SPY", "NASDAQ:QQQ", "AMEX:IWM",
                          "NASDAQ:NVDA", "NASDAQ:AAPL", "NASDAQ:TSLA"]),
        height=70,
    )

    # --- Division cards ---
    st.divider()
    st.subheader("Divisions")
    d1, d2, d3 = st.columns(3)

    with d1:
        st.markdown("##### 🟢 Robinhood — PMCC")
        if snap:
            opts = [p for p in snap["positions"] if " " in p["symbol"]]
            stocks = [p for p in snap["positions"] if " " not in p["symbol"]]
            st.metric("Option Legs", len(opts))
            st.metric("Stock Positions", len(stocks))
            st.metric("Equity", f"${snap['equity']:,.2f}")
        else:
            st.info("Not connected")

    with d2:
        st.markdown("##### 🟡 Fidelity — Options")
        fid_ok = bool(_ENV.get("FIDELITY_USERNAME"))
        if not orders.empty and "strategy" in orders.columns:
            fid_orders = orders[orders["strategy"] == "fidelity_options"]
            st.metric("Proposed Orders", len(fid_orders))
            fills = fid_orders[fid_orders.get("status", pd.Series()) == "filled"]
            st.metric("Fills", len(fills))
        if not fid_ok:
            st.caption("Add FIDELITY_USERNAME to .env")
        else:
            st.caption("Connects on next system start")

    with d3:
        st.markdown("##### ⚪ Crypto — Futures")
        st.info("Phase 3 — coming soon")

    # --- Strategy states ---
    if not strat_df.empty:
        st.divider()
        st.subheader("Strategy Status")
        display = strat_df[["strategy","halted","realized_pnl","halt_reason","updated_ts"]].copy()
        display["halted"] = display["halted"].apply(lambda x: "🛑 HALTED" if x else "✅ Active")
        display["realized_pnl"] = display["realized_pnl"].apply(lambda x: f"${float(x):,.2f}")
        st.dataframe(display, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Tab: Robinhood PMCC
# ---------------------------------------------------------------------------

def _tab_pmcc():
    st.subheader("Robinhood — PMCC Division")
    snap = rh_snapshot()
    opts = rh_options_detail()

    if not snap:
        st.warning("Robinhood not connected. Check ROBINHOOD_USERNAME / ROBINHOOD_PASSWORD in .env.")
        return

    c1, c2, c3 = st.columns(3)
    c1.metric("Account Equity",  f"${snap['equity']:,.2f}")
    c2.metric("Buying Power",    f"${snap['buying_power']:,.2f}")
    c3.metric("Option Legs",     len(opts))

    st.divider()

    if opts:
        st.subheader("Open Option Positions")

        # Fetch spot prices for all underlyings in one batch (cached 2 min)
        underlyings_set = tuple(sorted({op.get("chain_symbol", "") for op in opts if op.get("chain_symbol")}))
        spots = spot_prices(underlyings_set)

        rows = []
        for op in opts:
            dte = op.get("dte")
            qty = float(op.get("quantity") or 0)
            avg = float(op.get("avg_price") or 0)
            mark = op.get("mark_price")
            mark_val = float(mark) if mark is not None else 0.0
            pnl = None
            if mark is not None and avg > 0:
                # avg is per-contract (e.g. $350), mark_val is per-share (e.g. $3.75)
                # convert mark to per-contract, subtract avg, scale by contracts
                pnl = (mark_val * 100 - avg) * (1 if qty > 0 else -1) * abs(qty)

            # Intrinsic / extrinsic decomposition
            sym = op.get("chain_symbol", "")
            strike = float(op.get("strike_price") or 0)
            otype = (op.get("option_type") or "").lower()
            spot = spots.get(sym, 0.0)
            if spot > 0 and strike > 0 and mark_val > 0:
                intrinsic = max(0.0, spot - strike) if otype == "call" else max(0.0, strike - spot)
                extrinsic = max(0.0, mark_val - intrinsic)
                intr_str = f"${intrinsic:.2f}"
                extr_str = f"${extrinsic:.2f}"
            else:
                intr_str = "—"
                extr_str = "—"

            rows.append({
                "Symbol":     sym,
                "Type":       otype.upper(),
                "Strike":     f"${strike:.2f}",
                "Spot":       f"${spot:.2f}" if spot > 0 else "—",
                "Expiry":     op.get("expiration_date", ""),
                "DTE":        int(dte) if dte is not None else None,
                "Qty":        qty,
                "Avg/sh":     f"${avg/100:.2f}" if avg > 0 else "—",
                "Mark/sh":    f"${mark_val:.2f}" if mark is not None else "—",
                "Intrinsic":  intr_str,
                "Extrinsic":  extr_str,
                "Delta":      f"{float(op.get('delta') or 0):.2f}" if op.get("delta") is not None else "—",
                "Unreal P&L": f"${pnl:+,.2f}" if pnl is not None else "—",
            })

        df = pd.DataFrame(rows)

        def _dte_color(val):
            try:
                d = int(val)
                if d <= 7:  return "color: #ff4b4b; font-weight:600"
                if d <= 21: return "color: #ffa500; font-weight:600"
                return "color: #00d4aa; font-weight:600"
            except Exception:
                return ""

        styled = df.style.map(_dte_color, subset=["DTE"])
        st.dataframe(styled, use_container_width=True, hide_index=True)

        # DTE distribution bar chart
        dtes = [r["DTE"] for r in rows if r["DTE"] is not None]
        if dtes:
            st.divider()
            st.subheader("DTE Distribution")
            bars = []
            from datetime import date, timedelta
            for d in sorted(set(dtes)):
                future = (date.today() + timedelta(days=d)).isoformat()
                count  = dtes.count(d)
                bars.append({"time": future, "value": count,
                             "color": "#ff4b4b" if d <= 7 else "#ffa500" if d <= 21 else "#00d4aa"})
            st.components.v1.html(
                _lw_chart(bars, height=160, chart_type="histogram", title="dte"),
                height=180,
            )
    else:
        st.info("No open option positions found.")

    # Mini charts for each underlying
    st.divider()
    st.subheader("Underlying Charts")
    underlyings = list(dict.fromkeys(
        op.get("chain_symbol", "") for op in opts if op.get("chain_symbol")
    ))
    if not underlyings:
        underlyings = ["NVDA", "AAPL", "TSLA", "MSFT", "AMD"]

    for i in range(0, len(underlyings[:6]), 2):
        cols = st.columns(2)
        for j, sym in enumerate(underlyings[i:i+2]):
            with cols[j]:
                st.caption(f"**{sym}**")
                st.components.v1.html(_tv_mini(sym, height=200), height=215)


# ---------------------------------------------------------------------------
# Tab: Fidelity Options
# ---------------------------------------------------------------------------

def _tab_fidelity():
    st.subheader("Fidelity — Options Division")

    fid_user = _ENV.get("FIDELITY_USERNAME") or os.getenv("FIDELITY_USERNAME")
    if not fid_user:
        st.warning("FIDELITY_USERNAME not configured in .env.")
        return

    orders = load_orders()
    fid_orders = pd.DataFrame()
    if not orders.empty and "strategy" in orders.columns:
        fid_orders = orders[orders["strategy"] == "fidelity_options"].copy()

    # KPIs
    total   = len(fid_orders)
    filled  = len(fid_orders[fid_orders["status"] == "filled"]) if total else 0
    pending = len(fid_orders[fid_orders["status"].isin(["proposed","risk_approved","board_approved"])]) if total else 0

    c1, c2, c3 = st.columns(3)
    c1.metric("Total Orders",   total)
    c2.metric("Filled",         filled)
    c3.metric("Pending",        pending)

    st.divider()

    if not fid_orders.empty:
        st.subheader("Order History")

        def _strategy_badge(row):
            try:
                extra = json.loads(row.get("extra_json") or "{}")
                return extra.get("strategy_variant", row.get("strategy", "—"))
            except Exception:
                return "—"

        fid_orders["variant"] = fid_orders.apply(_strategy_badge, axis=1)

        # Strategy breakdown
        if "variant" in fid_orders.columns:
            breakdown = fid_orders["variant"].value_counts().reset_index()
            breakdown.columns = ["Strategy", "Count"]
            c1, c2 = st.columns([1, 2])
            with c1:
                st.dataframe(breakdown, use_container_width=True, hide_index=True)
            with c2:
                if len(fid_orders) >= 2:
                    daily = fid_orders.copy()
                    daily["date"] = pd.to_datetime(daily["ts"], utc=True, errors="coerce").dt.date.astype(str)
                    daily_counts = daily.groupby("date").size().reset_index(name="count")
                    bars = [{"time": r["date"], "value": r["count"]} for _, r in daily_counts.iterrows()]
                    st.components.v1.html(
                        _lw_chart(bars, height=160, color="#a78bfa", chart_type="histogram", title="fid_orders"),
                        height=180,
                    )

        st.divider()
        display_cols = [c for c in ["ts","symbol","variant","side","qty","limit_price","status","rationale"] if c in fid_orders.columns]
        st.dataframe(fid_orders[display_cols].head(30), use_container_width=True, hide_index=True)
    else:
        st.info("No Fidelity orders in the audit log yet. Run /fidelityscan in Telegram to generate proposals.")

    # Watchlist mini charts
    st.divider()
    st.subheader("Fidelity Watchlist")
    cols = st.columns(3)
    for i, sym in enumerate(["SPY", "QQQ", "IWM"]):
        with cols[i]:
            st.caption(f"**{sym}**")
            st.components.v1.html(_tv_mini(sym, height=200), height=215)


# ---------------------------------------------------------------------------
# Tab: Market
# ---------------------------------------------------------------------------

def _tab_market():
    st.subheader("Market Chart")

    sym_options = [
        "AMEX:SPY", "NASDAQ:QQQ", "AMEX:IWM",
        "NASDAQ:NVDA", "NASDAQ:AAPL", "NASDAQ:TSLA",
        "NASDAQ:AMD", "NASDAQ:MSFT", "NASDAQ:RKLB",
    ]
    sym = st.selectbox("Symbol", sym_options, index=0)
    interval = st.select_slider("Interval", ["1", "5", "15", "60", "D", "W"], value="D")

    uid = sym.replace(":", "_")
    chart_html = f"""
<div style="height:500px;width:100%;">
  <div id="tvc_{uid}_m" style="height:100%;width:100%;"></div>
  <script src="https://s3.tradingview.com/tv.js"></script>
  <script>
  new TradingView.widget({{
    autosize:true, symbol:"{sym}", interval:"{interval}",
    timezone:"America/New_York", theme:"dark", style:"1", locale:"en",
    studies:["RSI@tv-basicstudies","MACD@tv-basicstudies","BB@tv-basicstudies"],
    container_id:"tvc_{uid}_m"
  }});
  </script>
</div>"""
    st.components.v1.html(chart_html, height=520)


# ---------------------------------------------------------------------------
# Tab: Signals
# ---------------------------------------------------------------------------

def _tab_signals():
    st.subheader("Market Signals")

    watchlist = ["SPY", "QQQ", "IWM", "NVDA", "AAPL", "TSLA", "AMD", "MSFT"]
    sym = st.selectbox("Symbol", watchlist, key="sig_sym")

    # Exchange prefix for TV widgets
    exchange = "AMEX" if sym in ("SPY", "QQQ", "IWM") else "NASDAQ"
    tv_sym = f"{exchange}:{sym}"

    col_chart, col_ta = st.columns([3, 2])
    with col_chart:
        st.components.v1.html(_tv_advanced_chart(tv_sym, height=380), height=400)
    with col_ta:
        st.components.v1.html(_tv_technicals(sym, height=380), height=400)

    # MCP indicators
    st.divider()
    st.subheader(f"TradingView MCP — {sym}")
    if _ENV.get("ENABLE_TRADINGVIEW") != "1":
        st.info("Set ENABLE_TRADINGVIEW=1 in .env to enable live indicator data from the MCP connection.")
    else:
        with st.spinner("Fetching indicators..."):
            ind = tv_indicators(sym)

        if ind:
            metrics = [
                ("RSI (14)",          ind.get("RSI")),
                ("MACD",              ind.get("MACD.macd")),
                ("MACD Signal",       ind.get("MACD.signal")),
                ("ADX",               ind.get("ADX")),
                ("EMA 20",            ind.get("EMA20")),
                ("EMA 50",            ind.get("EMA50")),
                ("ATR (14)",          ind.get("ATR")),
                ("Bollinger Upper",   ind.get("BB.upper")),
                ("Bollinger Lower",   ind.get("BB.lower")),
                ("TV Recommend",      ind.get("Recommend.All")),
                ("TV MA Recommend",   ind.get("Recommend.MA")),
                ("TV Oscil Recommend",ind.get("Recommend.Other")),
            ]
            cols = st.columns(4)
            for i, (label, val) in enumerate(metrics):
                if val is None:
                    continue
                with cols[i % 4]:
                    try:
                        st.metric(label, f"{float(val):.3f}")
                    except (TypeError, ValueError):
                        st.metric(label, str(val))
        else:
            st.info("No indicator data returned for this symbol.")

    # Trend regime
    st.divider()
    st.subheader("Trend Regime")
    try:
        from trading_corp.agents.trend_regime import TrendAgent
        reading = TrendAgent().read()
        c1, c2 = st.columns(2)
        c1.metric("Regime", reading.regime.upper())
        c2.metric("Confidence", f"{getattr(reading, 'confidence', 0.0):.0%}")
    except Exception as e:
        st.info(f"Regime unavailable: {e}")


# ---------------------------------------------------------------------------
# Tab: Journal
# ---------------------------------------------------------------------------

def _tab_journal():
    st.subheader("Trade Journal")

    orders = load_orders()
    events = load_events()
    briefs = load_briefs()

    # Order pipeline
    if not orders.empty:
        st.markdown("**Order Pipeline**")
        status_counts = orders["status"].value_counts().reset_index()
        status_counts.columns = ["Status", "Count"]
        c1, c2 = st.columns([1, 3])
        with c1:
            st.dataframe(status_counts, use_container_width=True, hide_index=True)
        with c2:
            display_cols = [c for c in ["ts","strategy","symbol","side","qty","limit_price","status","rationale"] if c in orders.columns]
            st.dataframe(orders[display_cols].head(30), use_container_width=True, hide_index=True)
    else:
        st.info("No orders yet. Start the system with `python -m trading_corp` and run a scan.")

    st.divider()

    # Audit events
    if not events.empty:
        st.markdown("**Audit Log**")
        display_cols = [c for c in ["ts","actor","kind","payload_json"] if c in events.columns]
        st.dataframe(events[display_cols].head(50), use_container_width=True, hide_index=True)

    # Morning briefs
    if not briefs.empty:
        st.divider()
        st.markdown("**Morning Briefs**")
        for _, row in briefs.iterrows():
            with st.expander(f"{row.get('trading_day', '')} — {row.get('kind', '')}"):
                st.markdown(row.get("body_md", ""))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

_sidebar()

st.title("📈 AI Trading Corporation")

tabs = st.tabs([
    "Overview",
    "PMCC · Robinhood",
    "Options · Fidelity",
    "Market",
    "Signals",
    "Journal",
])

with tabs[0]: _tab_overview()
with tabs[1]: _tab_pmcc()
with tabs[2]: _tab_fidelity()
with tabs[3]: _tab_market()
with tabs[4]: _tab_signals()
with tabs[5]: _tab_journal()
