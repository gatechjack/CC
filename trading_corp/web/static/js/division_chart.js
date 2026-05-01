/* Per-division equity curve, fed by /partials/division-equity-curve/{slug}.
   Reads the slug off the chart container's data-division attribute. */

(function () {
  const container = document.getElementById('division-equity-chart');
  const empty = document.getElementById('division-equity-empty');
  if (!container || !window.LightweightCharts) return;

  const slug = container.dataset.division;
  if (!slug) return;

  const chart = LightweightCharts.createChart(container, {
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
    timeScale: { borderColor: '#1f2937', timeVisible: false },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Magnet,
      vertLine: { color: '#475569', width: 1, style: 3 },
      horzLine: { color: '#475569', width: 1, style: 3 },
    },
    handleScroll: false,
    handleScale: false,
  });

  const series = chart.addAreaSeries({
    lineColor: '#10b981',
    topColor: 'rgba(16, 185, 129, 0.30)',
    bottomColor: 'rgba(16, 185, 129, 0.0)',
    lineWidth: 2,
    priceFormat: { type: 'price', precision: 0, minMove: 1 },
  });

  function refresh() {
    fetch(`/partials/division-equity-curve/${slug}`)
      .then((r) => r.json())
      .then((data) => {
        const pts = (data.points || []).map((p) => ({ time: p.date, value: p.equity }));
        if (pts.length === 0) {
          container.classList.add('hidden');
          empty?.classList.remove('hidden');
          return;
        }
        container.classList.remove('hidden');
        empty?.classList.add('hidden');
        series.setData(pts);
        chart.timeScale().fitContent();
      })
      .catch((err) => console.warn('division equity fetch failed', err));
  }

  refresh();
  setInterval(refresh, 60_000);

  const ro = new ResizeObserver(() => {
    chart.applyOptions({ width: container.clientWidth, height: container.clientHeight });
  });
  ro.observe(container);
})();
