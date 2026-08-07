/* PMCC pair-row interaction:
   1. Single-open accordion: when one row opens, close any others.
   2. Immediate loading feedback in the right-rail analysis panel — header
      updates the moment you click, body shows "Loading {symbol}..." until
      HTMX swaps in the real analysis. Together with hx-sync="...:replace"
      on each summary (which aborts any in-flight pair-analysis request),
      this kills the race condition where a slow earlier fetch overwrote
      a fresh new selection. */

(function () {
  const list = document.getElementById('pair-list');
  if (!list) return;

  const panel = document.getElementById('pair-analysis');
  const symEl = document.getElementById('pair-analysis-symbol');

  function showLoading(symbol) {
    if (symEl) symEl.textContent = symbol;
    if (panel) {
      panel.innerHTML = `
        <div class="flex items-center gap-2 text-muted text-sm py-4">
          <span class="inline-block w-2 h-2 rounded-full bg-accent animate-pulse"></span>
          <span>Loading <span class="font-mono text-mono">${symbol}</span> analysis…</span>
        </div>`;
    }
  }

  list.querySelectorAll('details.pmcc-row').forEach((d) => {
    const summary = d.querySelector('summary[data-symbol]');

    // Single-open accordion + panel binding. `toggle` fires on ANY open —
    // pointer click, keyboard (Enter/Space), or a programmatic open — so it
    // is the reliable place to keep the analysis panel bound to the OPEN row.
    // We deliberately do NOT scroll-into-view here — the user wants the tile
    // to expand in place, not jump to the top of the page.
    d.addEventListener('toggle', () => {
      if (!d.open) return;
      list.querySelectorAll('details.pmcc-row').forEach((other) => {
        if (other !== d && other.open) other.removeAttribute('open');
      });
      // FIX 4 (2026-08-07): the EXPERT ANALYSIS panel (symbol header + body) must
      // ALWAYS reflect the open row, no matter HOW it was opened. A pointer click
      // is handled by the summary click listener below (which fires HTMX's
      // declarative hx-get); for every OTHER open path we drive the same load
      // here, so the panel can never show a different asset than the open row.
      if (!summary) return;
      const symbol = summary.getAttribute('data-symbol');
      if (symbol) showLoading(symbol);   // header + body always track the open row
      if (d._pmccClickFetch) {
        d._pmccClickFetch = false;       // the click already fired hx-get — no double-fetch
        return;
      }
      const url = summary.getAttribute('hx-get');
      if (url && window.htmx) {
        window.htmx.ajax('GET', url, {
          target: '#pair-analysis', swap: 'innerHTML', source: summary,
        });
      }
    });

    // Immediate visual feedback: capture click on summary BEFORE HTMX dispatches
    // its request, so the user always sees a responsive UI. The flag lets the
    // toggle handler above know the click path already fetched (avoids a double-fetch).
    if (summary) {
      summary.addEventListener('click', () => {
        const symbol = summary.getAttribute('data-symbol');
        if (symbol) showLoading(symbol);
        d._pmccClickFetch = true;
      });
    }
  });

  // Kill the default disclosure-triangle (we draw our own chevron)
  const style = document.createElement('style');
  style.textContent = `
    details.pmcc-row > summary::-webkit-details-marker { display: none; }
    details.pmcc-row > summary { list-style: none; }
  `;
  document.head.appendChild(style);
})();
