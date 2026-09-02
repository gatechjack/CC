/* pm_live.js -- progressive enhancement for the live game-card page. The page is fully server-rendered and
   works with JS OFF (ages are stamped server-side, the Active/Complete toggle + Poll now are links, the drawer
   is a native <details>). This adds: a per-second age ticker, a 60s fetch-and-swap refresh (no white flash),
   expandable trade rows, and a value-change flash. No frameworks, no CDN. */
(function () {
  "use strict";
  var DOT = "·", ARR_R = "▸", ARR_D = "▾";
  var loadT = Date.now();

  function fmtAge(s) {
    s = Math.max(0, Math.round(s));
    if (s < 60) return s + "s ago";
    if (s < 3600) return Math.floor(s / 60) + "m ago";
    var h = Math.floor(s / 3600);
    return h + "h " + Math.floor((s % 3600) / 60) + "m ago";
  }

  function tick() {
    var elapsed = (Date.now() - loadT) / 1000;
    document.querySelectorAll("[data-age0]").forEach(function (el) {
      if (el.dataset.age0 === "" || el.dataset.age0 == null) return;
      var age = parseFloat(el.dataset.age0) + elapsed;
      if (el.id === "pollage") {
        var iv = parseInt(el.dataset.interval || "60", 10);
        var next = Math.max(0, Math.round(iv - age));
        el.textContent = "updated " + fmtAge(age) + " " + DOT + " next poll " + next + "s";
        return;
      }
      var st = el.dataset.stale;
      var stale = st !== "" && st != null && age > parseFloat(st);
      el.textContent = fmtAge(age) + (stale ? " " + DOT + " stale" : "");
      el.className = "chip " + (stale ? "stale" : "");
    });
  }

  function bindRows() {
    document.querySelectorAll("tr.main[data-row]").forEach(function (r) {
      r.style.cursor = "pointer";
      r.onclick = function () {
        var det = document.querySelector('tr.det[data-detrow="' + r.dataset.row + '"]');
        if (!det) return;
        det.hidden = !det.hidden;
        var a = r.querySelector(".arw-cell");
        if (a) a.textContent = det.hidden ? ARR_R : ARR_D;
      };
    });
  }

  function flash() {
    document.querySelectorAll("[data-val]").forEach(function (el) {
      el.classList.add("flash");
      setTimeout(function () { el.classList.remove("flash"); }, 1000);
    });
  }

  function poll() {
    fetch(window.location.href, { headers: { "X-Poll": "1" } })
      .then(function (r) { return r.text(); })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var nm = doc.querySelector("main"), cur = document.querySelector("main");
        if (!nm || !cur) return;
        cur.innerHTML = nm.innerHTML;
        var np = doc.getElementById("pollage"), cp = document.getElementById("pollage");
        if (np && cp) cp.dataset.age0 = np.dataset.age0;   // header sits outside <main>; refresh its as_of too
        loadT = Date.now();
        bindRows();
        flash();
      })
      .catch(function () { /* transient network blip -- keep the last good render */ });
  }

  var refresh = document.getElementById("refresh");
  if (refresh) refresh.addEventListener("click", function (e) { e.preventDefault(); poll(); });

  var pe = document.getElementById("pollage");
  var IV = pe ? parseInt(pe.dataset.interval || "60", 10) : 60;
  setInterval(tick, 1000);
  setInterval(poll, IV * 1000);
  tick();
  bindRows();
})();
