/* Coinbase BTC Donchian 6h price chart, fed by
   /partials/donchian-chart/{slug}. Reads slug off the chart
   container's data-division attribute. Renders candles + 20-bar
   Donchian high (entry channel ceiling) + 6-bar Donchian low
   (exit channel floor) + SMA(168) trend filter, plus markers for
   past BUY/SELL fills and a "now" marker on the current bar. */

(function () {
  const container = document.getElementById('donchian-chart');
  const empty = document.getElementById('donchian-chart-empty');
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

  const candleSeries = chart.addCandlestickSeries({
    upColor: '#10b981',
    downColor: '#f43f5e',
    wickUpColor: '#10b981',
    wickDownColor: '#f43f5e',
    borderVisible: false,
    priceFormat: { type: 'price', precision: 0, minMove: 1 },
  });

  // Donchian entry-channel ceiling (rolling 20-bar high). Dashed
  // loss-tinted line — a close above this with trend OK is the
  // BUY trigger, so visually it's the "ceiling I'm trying to break."
  const highSeries = chart.addLineSeries({
    color: '#f43f5e',
    lineWidth: 1,
    lineStyle: 2,    // dashed
    priceFormat: { type: 'price', precision: 0, minMove: 1 },
    crosshairMarkerVisible: false,
    lastValueVisible: false,
    priceLineVisible: false,
  });

  // Donchian exit-channel floor (rolling 6-bar low). Dashed gain-
  // tinted line — close below this while in BTC = SELL trigger.
  const lowSeries = chart.addLineSeries({
    color: '#10b981',
    lineWidth: 1,
    lineStyle: 2,    // dashed
    priceFormat: { type: 'price', precision: 0, minMove: 1 },
    crosshairMarkerVisible: false,
    lastValueVisible: false,
    priceLineVisible: false,
  });

  // SMA(168) trend filter — close > sma to allow new BUYs.
  const smaSeries = chart.addLineSeries({
    color: 'rgba(59, 130, 246, 0.7)',
    lineWidth: 1,
    priceFormat: { type: 'price', precision: 0, minMove: 1 },
    crosshairMarkerVisible: false,
    lastValueVisible: false,
    priceLineVisible: false,
  });

  let currentPriceLine = null;

  function refresh() {
    fetch(`/partials/donchian-chart/${slug}`)
      .then((r) => r.json())
      .then((d) => {
        if (d.empty || !Array.isArray(d.candles) || d.candles.length === 0) {
          container.classList.add('hidden');
          empty?.classList.remove('hidden');
          return;
        }
        container.classList.remove('hidden');
        empty?.classList.add('hidden');

        candleSeries.setData(d.candles);
        highSeries.setData(d.donchian_high || []);
        lowSeries.setData(d.donchian_low || []);
        smaSeries.setData(d.sma || []);

        const markers = (d.markers || []).map((m) => ({
          time: m.time,
          position: m.side === 'buy' ? 'belowBar' : 'aboveBar',
          color: m.side === 'buy' ? '#10b981' : '#f43f5e',
          shape: m.side === 'buy' ? 'arrowUp' : 'arrowDown',
          text: m.side.toUpperCase(),
        }));
        // Highlight the most recent fully-closed bar — this is the
        // bar the strategy just evaluated (or is about to). Renders
        // a small accent-blue circle inside the bar.
        if (d.current_bar_ts != null) {
          markers.push({
            time: d.current_bar_ts,
            position: 'inBar',
            color: '#3b82f6',
            shape: 'circle',
            text: 'now',
          });
        }
        candleSeries.setMarkers(markers);

        // Horizontal price line at the most-recent close — gives an
        // at-a-glance read of where price sits relative to channels.
        const lastCandle = d.candles[d.candles.length - 1];
        if (currentPriceLine) candleSeries.removePriceLine(currentPriceLine);
        if (lastCandle && lastCandle.close != null) {
          currentPriceLine = candleSeries.createPriceLine({
            price: lastCandle.close,
            color: '#3b82f6',
            lineWidth: 1,
            lineStyle: 0,
            axisLabelVisible: true,
            title: 'last',
          });
        }

        chart.timeScale().fitContent();
      })
      .catch((err) => console.warn('donchian chart fetch failed', err));
  }

  refresh();
  // Refresh once a minute. The 6h bars only roll over four times a
  // day, but the markers + last-close line want to feel live.
  setInterval(refresh, 60_000);

  const ro = new ResizeObserver(() => {
    chart.applyOptions({
      width: container.clientWidth,
      height: container.clientHeight,
    });
  });
  ro.observe(container);
})();
