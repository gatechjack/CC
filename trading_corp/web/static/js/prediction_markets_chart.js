/* Prediction-markets dashboard — equity curve chart.

   Reads inline JSON from #pm-equity-data (rendered server-side in the
   partial template) and draws a line series on the #pm-equity-chart
   container. In All-mode the inline data carries multiple divisions; we
   sum across divisions per timestamp bucket so the curve reflects total
   prediction-market equity.

   Exposes `window.renderPMChart()` so the dashboard wrapper can call it
   on initial load AND after every HTMX swap into #pm-content. The
   container + JSON tag both live inside #pm-content, so after a swap
   they're fresh DOM nodes — we dispose any prior chart instance and
   create a new one. No HTTP fetch — data is already in the DOM. */

(function () {
  // Module-level: hold the last chart instance + ResizeObserver so we can
  // dispose them before creating new ones after each HTMX swap.
  let _chart = null;
  let _ro = null;

  function disposePrior() {
    if (_ro) {
      try { _ro.disconnect(); } catch (e) { /* noop */ }
      _ro = null;
    }
    if (_chart) {
      try { _chart.remove(); } catch (e) { /* noop */ }
      _chart = null;
    }
  }

  function renderPMChart() {
    const container = document.getElementById('pm-equity-chart');
    const empty = document.getElementById('pm-equity-empty');
    const dataNode = document.getElementById('pm-equity-data');
    if (!container || !window.LightweightCharts || !dataNode) return;

    disposePrior();

    let raw;
    try {
      raw = JSON.parse(dataNode.textContent || '[]');
    } catch (err) {
      console.warn('pm-equity: parse failed', err);
      return;
    }

    if (!Array.isArray(raw) || raw.length === 0) {
      container.classList.add('hidden');
      empty?.classList.remove('hidden');
      return;
    }

    // Aggregate across divisions per timestamp bucket. Single-division mode
    // → no-op; All-mode → sum equity per shared ts.
    const byTime = new Map();
    for (const pt of raw) {
      const ts = pt.ts;
      if (typeof ts !== 'string') continue;
      const epoch = Math.floor(Date.parse(ts) / 1000);
      if (!Number.isFinite(epoch)) continue;
      const eq = Number(pt.equity) || 0;
      byTime.set(epoch, (byTime.get(epoch) || 0) + eq);
    }
    const points = Array.from(byTime.entries())
      .sort((a, b) => a[0] - b[0])
      .map(([time, value]) => ({ time, value }));

    if (points.length === 0) {
      container.classList.add('hidden');
      empty?.classList.remove('hidden');
      return;
    }

    container.classList.remove('hidden');
    empty?.classList.add('hidden');

    _chart = LightweightCharts.createChart(container, {
      layout: {
        background: { type: 'solid', color: 'transparent' },
        textColor: '#94a3b8',
        fontFamily: 'JetBrains Mono, Consolas, monospace',
        fontSize: 10,
      },
      grid: {
        vertLines: { color: 'rgba(31, 41, 55, 0.5)' },
        horzLines: { color: 'rgba(31, 41, 55, 0.5)' },
      },
      rightPriceScale: { borderColor: '#1f2937' },
      timeScale: {
        borderColor: '#1f2937',
        timeVisible: true,
        secondsVisible: false,
      },
      crosshair: {
        mode: LightweightCharts.CrosshairMode.Magnet,
        vertLine: { color: '#475569', width: 1, style: 3 },
        horzLine: { color: '#475569', width: 1, style: 3 },
      },
      handleScroll: false,
      handleScale: false,
    });

    const line = _chart.addLineSeries({
      color: '#3b82f6',
      lineWidth: 2,
      priceFormat: { type: 'price', precision: 2, minMove: 0.01 },
      crosshairMarkerVisible: true,
      lastValueVisible: true,
    });
    line.setData(points);
    _chart.timeScale().fitContent();

    _ro = new ResizeObserver(() => {
      if (!_chart) return;
      _chart.applyOptions({
        width: container.clientWidth,
        height: container.clientHeight,
      });
    });
    _ro.observe(container);
  }

  window.renderPMChart = renderPMChart;
})();
