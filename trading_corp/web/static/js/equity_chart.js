/* Renders the equity curve using TradingView Lightweight Charts.
   Polls /partials/equity-curve every 60s for fresh data. */

(function () {
  const container = document.getElementById('equity-chart');
  const empty = document.getElementById('equity-empty');
  if (!container || !window.LightweightCharts) return;

  // Stage 1 paper-mode redeploy landed at 2026-05-31 05:36 UTC
  // (origin/main 7352f8f). Marker rendered as a CSS overlay div positioned
  // via chart.timeScale().timeToCoordinate(deploy_date). The equity series
  // uses daily granularity (YYYY-MM-DD), so the marker lands at the day's
  // x-position; the label notes the exact UTC time.
  const DEPLOY_MARKER_TIME = '2026-05-31';
  const markerEl = document.getElementById('equity-deploy-marker');

  const chart = LightweightCharts.createChart(container, {
    layout: {
      background: { type: 'solid', color: 'transparent' },
      textColor: '#94a3b8',
      fontFamily: 'JetBrains Mono, Consolas, monospace',
      fontSize: 11,
    },
    grid: {
      vertLines: { color: 'rgba(31, 41, 55, 0.6)' },
      horzLines: { color: 'rgba(31, 41, 55, 0.6)' },
    },
    rightPriceScale: { borderColor: '#1f2937' },
    timeScale: {
      borderColor: '#1f2937',
      timeVisible: false,
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

  const series = chart.addAreaSeries({
    lineColor: '#3b82f6',
    topColor: 'rgba(59, 130, 246, 0.35)',
    bottomColor: 'rgba(59, 130, 246, 0.0)',
    lineWidth: 2,
    priceFormat: {
      type: 'price',
      precision: 0,
      minMove: 1,
    },
  });

  function positionDeployMarker() {
    if (!markerEl) return;
    // timeToCoordinate returns null when the time is outside the visible
    // range OR before any data point exists — hide in either case.
    const x = chart.timeScale().timeToCoordinate(DEPLOY_MARKER_TIME);
    if (x === null || x === undefined || x < 0) {
      markerEl.classList.add('hidden');
      return;
    }
    markerEl.style.left = `${x}px`;
    markerEl.classList.remove('hidden');
  }

  function refresh() {
    fetch('/partials/equity-curve')
      .then((r) => r.json())
      .then((data) => {
        const points = (data.points || []).map((p) => ({
          time: p.date, // YYYY-MM-DD format works with Lightweight Charts
          value: p.equity,
        }));
        if (points.length === 0) {
          container.classList.add('hidden');
          empty?.classList.remove('hidden');
          markerEl?.classList.add('hidden');
          return;
        }
        container.classList.remove('hidden');
        empty?.classList.add('hidden');
        series.setData(points);
        chart.timeScale().fitContent();
        positionDeployMarker();
      })
      .catch((err) => console.warn('equity curve fetch failed', err));
  }

  // Initial draw + size observer
  refresh();
  setInterval(refresh, 60_000);

  // Reposition the deploy marker on any visible-range change (zoom, scroll,
  // initial fit). Lightweight Charts fires this synchronously after layout.
  chart.timeScale().subscribeVisibleTimeRangeChange(positionDeployMarker);

  const ro = new ResizeObserver(() => {
    chart.applyOptions({
      width: container.clientWidth,
      height: container.clientHeight,
    });
    positionDeployMarker();
  });
  ro.observe(container);
})();
