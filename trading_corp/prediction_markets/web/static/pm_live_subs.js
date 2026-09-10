/* pm_live_subs.js -- progressive enhancement for the redesigned Live Sub-divisions tile page. The page is fully
   server-rendered and works with JS OFF (account tabs are ?account= links, ages are stamped server-side). This
   adds, with no framework/CDN: a per-second age ticker; a 60s fetch-and-swap refresh (no white flash); and the
   TRADE-EVENTS PULSE -- each cycle it asks /live/events for orders/closes newer than the max id it last saw and
   lights the matching tile (placed = cyan glow, silent; closed = green/red wash + a short two-note tone if sound
   is on). A close is picked up seconds to a few minutes after it settles -- never claimed instant. */
(function () {
  "use strict";
  var DOT = "·";
  var loadT = Date.now();
  var since = null, account = "", sfxOn = false, actx = null, busy = false;

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
      var base = el.className.replace(/\s*stale\s*/g, " ").trim();
      el.textContent = fmtAge(age) + (stale ? " " + DOT + " stale" : "");
      if (el.classList.contains("chip")) el.className = base + (stale ? " stale" : "");
    });
  }

  /* two short tones, generated, no assets: rising major sixth = win, falling fourth = loss (design spec) */
  function tone(kind) {
    if (!sfxOn) return;
    try {
      actx = actx || new (window.AudioContext || window.webkitAudioContext)();
      var seq = kind === "won" ? [659.25, 987.77] : [392, 293.66];
      seq.forEach(function (f, i) {
        var o = actx.createOscillator(), g = actx.createGain();
        o.type = "triangle"; o.frequency.value = f;
        var t = actx.currentTime + i * 0.11;
        g.gain.setValueAtTime(0.0001, t);
        g.gain.exponentialRampToValueAtTime(0.16, t + 0.012);
        g.gain.exponentialRampToValueAtTime(0.0001, t + 0.17);
        o.connect(g); g.connect(actx.destination); o.start(t); o.stop(t + 0.19);
      });
    } catch (e) { /* audio unavailable -- the visual alert never depends on sound */ }
  }

  function tileFor(ev) {
    return document.querySelector('.t[data-sub="' + ev.account + "/" + ev.code.toLowerCase() + '"]');
  }

  function pulse(el, cls, banner) {
    if (!el) return;
    el.classList.remove(cls); void el.offsetWidth; el.classList.add(cls);   // restart the animation
    if (banner) {
      var old = el.querySelector(".ev.pulse-banner");
      if (old) old.remove();
      var d = document.createElement("div");
      d.className = "ev pulse-banner " + banner.cls;
      d.innerHTML = banner.html;
      var money = el.querySelector(".mny");
      if (money) money.parentNode.insertBefore(d, money); else el.appendChild(d);
    }
  }

  function applyEvents(data) {
    if (!data) return;
    (data.placed || []).forEach(function (p) {
      pulse(tileFor(p), "fx-placed", {cls: "placed", html:
        '<div class="t1">JUST PLACED</div><div class="t2">' + esc(p.name) + '</div>' +
        '<div class="t3">detected on this poll</div>'});
    });
    (data.closed || []).forEach(function (c) {
      var won = c.result === "won";
      pulse(tileFor(c), won ? "fx-closed-won" : "fx-closed-lost", {cls: "closed" + (won ? "" : " lost"), html:
        '<div class="t1">JUST CLOSED' + (c.result ? " " + DOT + " " + c.result.toUpperCase() : "") + '</div>' +
        '<div class="t2">' + (c.realized != null ? money(c.realized) + " realized" : "not booked") + '</div>' +
        '<div class="t3">' + esc(c.name) + ' ' + DOT + ' settled -- picked up on this poll, not instantly</div>'});
      tone(c.result);
    });
    if (typeof data.max_id === "number") since = data.max_id;
  }
  function money(v) { return (v < 0 ? "-$" : "$") + Math.abs(v).toFixed(2); }
  function esc(s) { var d = document.createElement("div"); d.textContent = s == null ? "" : s; return d.innerHTML; }

  function fetchEvents() {
    if (since == null) return Promise.resolve();
    var url = "/live/events?since=" + encodeURIComponent(since) + (account ? "&account=" + encodeURIComponent(account) : "");
    return fetch(url, {headers: {"X-Poll": "1"}}).then(function (r) { return r.json(); })
      .then(applyEvents).catch(function () {});
  }

  function swap() {
    return fetch(window.location.href, {headers: {"X-Poll": "1"}}).then(function (r) { return r.text(); })
      .then(function (html) {
        var doc = new DOMParser().parseFromString(html, "text/html");
        var nm = doc.querySelector("main"), cur = document.querySelector("main");
        if (!nm || !cur) return;
        cur.innerHTML = nm.innerHTML;
        loadT = Date.now();
        var np = doc.getElementById("pollage"), cp = document.getElementById("pollage");
        if (np && cp) { cp.dataset.age0 = np.dataset.age0; }       // header sits outside <main>
      }).catch(function () { /* transient blip -- keep the last good render */ });
  }

  function cycle() {
    if (busy) return; busy = true;
    swap().then(fetchEvents).then(function () { busy = false; }).catch(function () { busy = false; });
  }

  var pe = document.getElementById("pollage");
  if (pe) { since = pe.dataset.since != null && pe.dataset.since !== "" ? parseInt(pe.dataset.since, 10) : null; account = pe.dataset.account || ""; }
  var IV = pe ? parseInt(pe.dataset.interval || "60", 10) : 60;

  var refresh = document.getElementById("refresh");
  if (refresh) refresh.addEventListener("click", function (e) { e.preventDefault(); cycle(); });
  var sfx = document.getElementById("sfx");
  if (sfx) sfx.addEventListener("click", function (e) {
    sfxOn = !sfxOn;
    e.currentTarget.setAttribute("aria-pressed", sfxOn ? "true" : "false");
    e.currentTarget.textContent = sfxOn ? "Sound on" : "Sound off";
    if (sfxOn) tone("won");     // a confirmation blip on enable
  });

  setInterval(tick, 1000);
  setInterval(cycle, IV * 1000);
  tick();
})();
