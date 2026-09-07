/* mace_candles.js — Underlyings panel: mini candlestick charts + price poller.
   Mounts LightweightCharts on every [id^="mace-chart-"] container found on
   the /mace page. Polls /mace/partials/prices every 60s. Timeframe toggle
   buttons (data-sym / data-iv) re-fetch candles and call series.setData().
   Guard: silently no-ops if window.LightweightCharts is absent or any
   fetch fails. Double-init is blocked by a per-container sentinel attr.      */

(function () {
  'use strict';

  // ── ET (America/New_York) time formatting — DISPLAY ONLY ───────────────────
  // Chart `time` values are epoch-seconds (UTC); Lightweight Charts renders the
  // axis in UTC by default (this is why bars read one wall-clock ahead of the
  // market). We convert the *display* to Eastern — DST-aware EDT/EST via Intl,
  // never a fixed -4/-5 — WITHOUT mutating the cached bar_time. Only intraday
  // TIME ticks are converted; date ticks stay on the UTC calendar day so the
  // daily (1M) view — whose bars are stamped at the exchange's daily epoch —
  // can never be shifted across a day boundary.
  function _toUnixSec(t) {
    if (typeof t === 'number') return t;                        // epoch-seconds (our feed)
    if (t && typeof t === 'object' && t.year) {                 // LW business-day object
      return Math.floor(Date.UTC(t.year, (t.month || 1) - 1, t.day || 1) / 1000);
    }
    var ms = Date.parse(t);                                      // 'yyyy-mm-dd' string
    return isNaN(ms) ? 0 : Math.floor(ms / 1000);
  }

  function _fields(t, tz) {
    var parts = new Intl.DateTimeFormat('en-US', {
      timeZone: tz,
      hour12: false,
      month: 'short', day: '2-digit', hour: '2-digit', minute: '2-digit',
    }).formatToParts(new Date(_toUnixSec(t) * 1000));
    var o = {};
    parts.forEach(function (p) { o[p.type] = p.value; });
    if (o.hour === '24') o.hour = '00';   // some engines emit '24' for midnight
    return o;
  }

  function etTimeHM(t)  { var f = _fields(t, 'America/New_York'); return f.hour + ':' + f.minute; }   // 09:30
  function utcDateMD(t) { var f = _fields(t, 'UTC');             return f.month + ' ' + f.day; }      // Sep 04 (UTC cal)
  function etStamp(t) {                                                                                // Sep 04  09:30 ET
    var f = _fields(t, 'America/New_York');
    return f.month + ' ' + f.day + '  ' + f.hour + ':' + f.minute + ' ET';
  }

  // Axis tick formatter — intraday time ticks in ET, date ticks on UTC calendar.
  // tickMarkType: 0 Year, 1 Month, 2 DayOfMonth, 3 Time, 4 TimeWithSeconds.
  function maceTickMark(time, tickMarkType) {
    return (tickMarkType >= 3) ? etTimeHM(time) : utcDateMD(time);
  }

  // ── TradingView attribution (license: the logo MUST stay visible) ──────────
  // Lightweight Charts injects a required attribution logo (<a id="tv-attr-logo">)
  // into each chart container. The license requires it remain visible; we only
  // repoint its href to THIS symbol's TradingView page. Never hide/remove it.
  function repointAttribution(container, symbol) {
    var logo = container.querySelector('#tv-attr-logo');
    if (!logo) return false;
    try {
      logo.setAttribute('href', 'https://www.tradingview.com/symbols/' +
                                 encodeURIComponent(symbol) + '/');
      logo.setAttribute('title', symbol + ' on TradingView');
      return true;
    } catch (_) {
      return false;
    }
  }

  // ── chart palette (matches mace_live.html / donchian_chart.js) ────────────
  var CHART_OPTS = {
    layout: {
      background: { type: 'solid', color: 'transparent' },
      textColor: '#94a3b8',
      fontFamily: 'JetBrains Mono, Consolas, monospace',
      fontSize: 9,
    },
    grid: {
      vertLines: { color: 'rgba(31, 41, 55, 0.4)' },
      horzLines: { color: 'rgba(31, 41, 55, 0.4)' },
    },
    rightPriceScale: {
      borderColor: '#1f2937',
      scaleMargins: { top: 0.05, bottom: 0.05 },
    },
    timeScale: {
      borderColor: '#1f2937',
      timeVisible: true,
      secondsVisible: false,
      fixLeftEdge: true,
      fixRightEdge: true,
      tickMarkFormatter: maceTickMark,   // ET wall-clock on intraday axis (display only)
    },
    crosshair: {
      mode: 1, // Magnet
      vertLine: { color: '#475569', width: 1, style: 3 },
      horzLine: { color: '#475569', width: 1, style: 3 },
    },
    handleScroll: false,
    handleScale: false,
  };

  var CANDLE_OPTS = {
    upColor: '#10b981',
    downColor: '#f43f5e',
    wickUpColor: '#10b981',
    wickDownColor: '#f43f5e',
    borderVisible: false,
  };

  // Toggle button active/inactive classes
  var BTN_ACTIVE   = ['border-accent/40', 'bg-accent/15', 'text-accent', 'mace-tf-active'];
  var BTN_INACTIVE = ['border-edge', 'text-muted'];

  // Per-container state map: containerId -> { chart, series }
  var _state = {};

  // ── helpers ───────────────────────────────────────────────────────────────

  function applyBtnStyles(btn, active) {
    if (active) {
      BTN_INACTIVE.forEach(function (c) { btn.classList.remove(c); });
      BTN_ACTIVE.forEach(function (c) { btn.classList.add(c); });
    } else {
      BTN_ACTIVE.forEach(function (c) { btn.classList.remove(c); });
      BTN_INACTIVE.forEach(function (c) { btn.classList.add(c); });
    }
  }

  // Fetch candles for symbol+interval and populate the chart series.
  function loadCandles(containerId, symbol, interval) {
    var entry = _state[containerId];
    if (!entry) return;

    var url = '/mace/partials/candles?symbol=' + encodeURIComponent(symbol) +
              '&interval=' + encodeURIComponent(interval);

    fetch(url)
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (d) {
        var candles = (d && Array.isArray(d.candles)) ? d.candles : [];
        if (candles.length === 0) return;
        try {
          entry.series.setData(candles);
          entry.chart.timeScale().fitContent();
        } catch (e) {
          console.warn('[mace_candles] setData failed for', symbol, e);
        }
      })
      .catch(function (err) {
        console.warn('[mace_candles] candle fetch failed', symbol, interval, err);
      });
  }

  // ── init charts ───────────────────────────────────────────────────────────

  function initCharts() {
    if (!window.LightweightCharts) {
      console.warn('[mace_candles] LightweightCharts not available — skipping chart init');
      return;
    }

    // Find all chart containers; skip any already initialised.
    var containers = document.querySelectorAll('[id^="mace-chart-"]');
    containers.forEach(function (container) {
      if (container.dataset.maceInit === '1') return; // double-init guard
      container.dataset.maceInit = '1';

      var symbol   = container.dataset.symbol;
      var interval = container.dataset.interval || '5m';
      var id       = container.id;

      if (!symbol) return;

      // Create the chart + series.
      var opts = Object.assign({}, CHART_OPTS, {
        width:  container.clientWidth  || 200,
        height: container.clientHeight || 140,
        localization: {
          // crosshair label: intraday -> ET date+time; daily -> UTC calendar date.
          timeFormatter: function (time) {
            var iv = container.dataset.interval || '5m';
            return (iv === '5m') ? etStamp(time) : utcDateMD(time);
          },
        },
      });

      var chart, series;
      try {
        chart  = LightweightCharts.createChart(container, opts);
        series = chart.addCandlestickSeries(CANDLE_OPTS);
      } catch (e) {
        console.warn('[mace_candles] createChart failed for', symbol, e);
        return;
      }

      _state[id] = { chart: chart, series: series };

      // Repoint the TradingView attribution logo to this symbol (logo stays
      // visible; license unchanged). Logo is normally appended synchronously by
      // createChart; retry once next tick if it wasn't there yet.
      if (!repointAttribution(container, symbol)) {
        setTimeout(function () { repointAttribution(container, symbol); }, 0);
      }

      // Resize observer — keeps chart sized to its container.
      if (window.ResizeObserver) {
        var ro = new ResizeObserver(function () {
          try {
            chart.applyOptions({
              width:  container.clientWidth,
              height: container.clientHeight,
            });
          } catch (_) {}
        });
        ro.observe(container);
      }

      // Initial data load.
      loadCandles(id, symbol, interval);
    });
  }

  // ── timeframe toggle wiring ───────────────────────────────────────────────

  function wireToggles() {
    var buttons = document.querySelectorAll('.mace-tf-btn');
    buttons.forEach(function (btn) {
      if (btn.dataset.maceWired === '1') return;
      btn.dataset.maceWired = '1';

      btn.addEventListener('click', function () {
        var sym = btn.dataset.sym;
        var iv  = btn.dataset.iv;
        if (!sym || !iv) return;

        // Update data-interval on the chart container so the active state
        // is always readable from the DOM.
        var containerId = 'mace-chart-' + sym;
        var container   = document.getElementById(containerId);
        if (container) container.dataset.interval = iv;

        // Update active/inactive button styles for this symbol's group.
        var sibs = document.querySelectorAll('[data-mace-toggle="' + sym + '"] .mace-tf-btn');
        sibs.forEach(function (s) {
          applyBtnStyles(s, s === btn);
        });

        // Re-fetch candles for the selected timeframe.
        loadCandles(containerId, sym, iv);
      });
    });
  }

  // ── price poller ─────────────────────────────────────────────────────────

  function pollPrices() {
    fetch('/mace/partials/prices')
      .then(function (r) {
        if (!r.ok) throw new Error('HTTP ' + r.status);
        return r.json();
      })
      .then(function (data) {
        if (!data || typeof data !== 'object') return;
        Object.keys(data).forEach(function (sym) {
          var el = document.getElementById('mace-price-' + sym);
          if (!el) return;
          var val = data[sym];
          if (val === null || val === undefined) {
            el.textContent = '—';
            el.className = el.className.replace(/text-(?:gain|loss|mono)\b/g, '').trim() +
                           ' text-muted';
          } else {
            var num = parseFloat(val);
            el.textContent = isNaN(num) ? '—' : num.toFixed(2);
            // Keep the price element styled as mono data (neutral).
            el.classList.remove('text-muted', 'text-gain', 'text-loss');
            el.classList.add('text-mono');
          }
        });
      })
      .catch(function (err) {
        console.warn('[mace_candles] prices fetch failed', err);
      });
  }

  // ── bootstrap ─────────────────────────────────────────────────────────────

  function bootstrap() {
    initCharts();
    wireToggles();
    pollPrices();
    // Poll prices every 60 seconds.
    setInterval(pollPrices, 60000);
  }

  // Work whether the script is in <head> (DOMContentLoaded not yet fired) or
  // at end of <body> (DOM already ready).
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootstrap);
  } else {
    bootstrap();
  }
})();
