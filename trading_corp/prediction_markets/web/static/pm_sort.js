/* PM prospects column sort -- vanilla, no deps. PROGRESSIVE ENHANCEMENT: the server already sends the
   default cost-ROI-desc order, so with JS off the table is still correctly ordered; this only adds
   click-to-sort on the sortable headers. Re-binds after every htmx swap (a Refresh re-renders the table). */
(function () {
  "use strict";

  function cellVal(row, idx) {
    var cell = row.children[idx];
    if (!cell) return 0;
    var v = cell.getAttribute("data-sort-value");
    if (v === null || v === "") v = cell.textContent.trim();
    var n = parseFloat(v);
    return isNaN(n) ? String(v).toLowerCase() : n;
  }

  function sortBy(table, th) {
    var headers = Array.prototype.slice.call(th.parentNode.children);
    var idx = headers.indexOf(th);
    var tbody = table.tBodies[0];
    if (!tbody || idx < 0) return;
    var asc = th.getAttribute("data-sort-dir") !== "asc"; // first click on a column = descending
    var rows = Array.prototype.slice.call(tbody.rows);
    rows.sort(function (a, b) {
      var va = cellVal(a, idx), vb = cellVal(b, idx);
      if (va < vb) return asc ? -1 : 1;
      if (va > vb) return asc ? 1 : -1;
      return 0;
    });
    rows.forEach(function (r) { tbody.appendChild(r); });
    headers.forEach(function (h) {
      h.removeAttribute("data-sort-dir");
      h.classList.remove("pm-sort-asc", "pm-sort-desc");
    });
    th.setAttribute("data-sort-dir", asc ? "asc" : "desc");
    th.classList.add(asc ? "pm-sort-asc" : "pm-sort-desc");
  }

  function bind(root) {
    var tables = (root && root.querySelectorAll ? root : document).querySelectorAll("table.pm-sortable-table");
    Array.prototype.forEach.call(tables, function (table) {
      Array.prototype.forEach.call(table.querySelectorAll("th.pm-sortable"), function (th) {
        if (th.getAttribute("data-sort-bound")) return;
        th.setAttribute("data-sort-bound", "1");
        th.style.cursor = "pointer";
        th.addEventListener("click", function () { sortBy(table, th); });
      });
    });
  }

  document.addEventListener("DOMContentLoaded", function () { bind(document); });
  // a Refresh swaps #pm-prospects-rows -> re-bind the freshly-rendered table's headers
  document.body.addEventListener("htmx:afterSwap", function (e) { bind(e.target); });
})();
