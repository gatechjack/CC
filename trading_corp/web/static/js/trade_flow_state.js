// Preserve <details open> state on the Live Trade Flow panel across
// htmx's 5s outerHTML refresh. Each tile carries data-tile-id (the
// audit_event row id, see web/data.py:trade_flow). On every htmx swap
// the entire #trade-flow div is rebuilt and all <details> lose their
// open attribute; without this we'd snap closed every 5 seconds and
// the user couldn't read a payload long enough to scan it.
//
// Persists in localStorage so a page reload also restores state.
// No bounded-size cleanup: audit_event ids grow monotonically, the
// JSON encoding is small, and localStorage caps in the 5-10MB range
// per origin — many years of normal trading before this matters.
(function () {
  const STORAGE_KEY = "tradeflow:open-tile-ids";
  const PANEL_ID = "trade-flow";
  const SELECTOR = "#" + PANEL_ID + " details[data-tile-id]";

  function loadOpenSet() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return new Set(raw ? JSON.parse(raw) : []);
    } catch (e) {
      return new Set();
    }
  }

  function saveOpenSet(set) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify([...set]));
    } catch (e) {
      // localStorage unavailable / quota exceeded — fail silently.
    }
  }

  function applyOpenState() {
    const opens = loadOpenSet();
    document.querySelectorAll(SELECTOR).forEach((d) => {
      if (opens.has(d.dataset.tileId)) {
        d.setAttribute("open", "");
      }
    });
  }

  // The `toggle` event does not bubble (per spec), so we listen on
  // document with capture=true to catch toggles on any descendant
  // <details>. document is permanent; it survives partial swaps that
  // rebuild #trade-flow's children every 5s.
  document.addEventListener(
    "toggle",
    (e) => {
      const t = e.target;
      if (!(t instanceof HTMLDetailsElement)) return;
      if (!t.matches(SELECTOR)) return;
      const id = t.dataset.tileId;
      if (!id) return;
      const opens = loadOpenSet();
      if (t.open) opens.add(id);
      else opens.delete(id);
      saveOpenSet(opens);
    },
    true,
  );

  // Preserve the panel's scroll position across the 5s outerHTML swap.
  // #trade-flow IS the scroll container, and htmx rebuilds it whole on every
  // poll — which otherwise snaps the list back to the top mid-scroll. Save
  // scrollTop just before the swap and restore it just after.
  let savedScrollTop = null;
  document.addEventListener("htmx:beforeSwap", (e) => {
    const tgt = (e.detail && e.detail.target) || e.target;
    if (tgt && tgt.id === PANEL_ID) {
      const el = document.getElementById(PANEL_ID);
      savedScrollTop = el ? el.scrollTop : null;
    }
  });

  // Re-apply <details> state + restore scroll after htmx replaces
  // #trade-flow's outerHTML.
  document.addEventListener("htmx:afterSwap", (e) => {
    const tgt = (e.detail && e.detail.target) || e.target;
    if (tgt && tgt.id === PANEL_ID) {
      applyOpenState();
      if (savedScrollTop != null) {
        const el = document.getElementById(PANEL_ID);
        if (el) el.scrollTop = savedScrollTop;
        savedScrollTop = null;
      }
    }
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", applyOpenState);
  } else {
    applyOpenState();
  }
})();
