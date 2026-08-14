/**
 * mace_payoff.js — MACE iron-condor payoff canvas.
 *
 * Ported from mock_logic.js draw() with these changes:
 *   - Reads data from a per-rung <script type="application/json"> island.
 *   - Palette mapped to app tokens (gain / loss / accent / muted / mono).
 *   - T+0 curve is ONLY drawn when p.iv, p.spot, and p.t are all present;
 *     otherwise a caption is drawn instead (honest — never fabricated).
 *   - DPR-aware canvas sizing.
 *   - Re-init on htmx:afterSettle (30s poll re-draws).
 *   - All math (ncdf / bs / condorT0 / condorExp) copied verbatim from mock.
 */

'use strict';

/* ── palette (maps to app Tailwind tokens) ─────────────────────────────── */
var MP = {
  gain:   '#10b981',   // gain  (emerald-500)
  loss:   '#f43f5e',   // loss  (rose-500)
  accent: '#3b82f6',   // accent (blue-500)
  muted:  '#94a3b8',   // muted  (slate-400)
  mono:   '#e2e8f0',   // mono   (slate-200)
  grid:   'rgba(148,163,184,0.10)',
  zero:   'rgba(148,163,184,0.32)',
  profitFill: 'rgba(16,185,129,0.07)',
  expFill:    'rgba(16,185,129,0.07)',
  tooltipBg:  'rgba(7,11,20,0.92)',
  tooltipBorder: 'rgba(148,163,184,0.28)',
};

/* ── math (Abramowitz-Stegun, verbatim from mock_logic.js) ─────────────── */
function ncdf(x) {
  var t = 1 / (1 + 0.2316419 * Math.abs(x));
  var d = 0.3989423 * Math.exp(-x * x / 2);
  var p = 1 - d * t * (1.330274 * Math.pow(t, 4) - 1.821256 * Math.pow(t, 3)
        + 1.781478 * t * t - 0.356538 * t + 0.3193815);
  return x >= 0 ? p : 1 - p;
}

function bs(S, K, iv, t, isCall) {
  if (t <= 0) return isCall ? Math.max(0, S - K) : Math.max(0, K - S);
  var v  = iv * Math.sqrt(t);
  var d1 = (Math.log(S / K) + 0.5 * iv * iv * t) / v;
  var d2 = d1 - v;
  return isCall
    ? S * ncdf(d1)  - K * ncdf(d2)
    : K * ncdf(-d2) - S * ncdf(-d1);
}

function condorT0(S, p, iv, t) {
  return bs(S, p.sp, iv, t, false) - bs(S, p.lp, iv, t, false)
       + bs(S, p.sc, iv, t, true)  - bs(S, p.lc, iv, t, true);
}

function condorExp(S, p) {
  return Math.max(0, p.sp - S) - Math.max(0, p.lp - S)
       + Math.max(0, S - p.sc) - Math.max(0, S - p.lc);
}

/* ── hover state ────────────────────────────────────────────────────────── */
var _hover = {};   // keyed by canvas id

/* ── draw ───────────────────────────────────────────────────────────────── */
function draw(canvas, p) {
  if (!canvas || !canvas.getContext) return;
  var dpr = window.devicePixelRatio || 1;
  var W   = canvas.clientWidth;
  var H   = canvas.clientHeight;
  if (!W || !H) return;

  canvas.width  = W * dpr;
  canvas.height = H * dpr;

  var g = canvas.getContext('2d');
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);

  /* margins */
  var L = 52, R = 14, T = 18, B = 26;

  /* x-range */
  var lo = (p.lo != null) ? p.lo : ((p.lp != null) ? p.lp - 18 : 0);
  var hi = (p.hi != null) ? p.hi : ((p.lc != null) ? p.lc + 18 : 100);

  /* y-range */
  var maxP  = (p.credit != null) ? p.credit * 100 : 50;
  var maxL;
  if (p.lc != null && p.sc != null && p.credit != null) {
    maxL = -((p.lc - p.sc) - p.credit) * 100;
  } else {
    maxL = -(maxP * 3);
  }
  var yTop = maxP * 1.8;
  var yBot = maxL * 1.12;

  var px = function(v) { return L + ((v - lo) / (hi - lo)) * (W - L - R); };
  var py = function(v) { return T + ((yTop - v) / (yTop - yBot)) * (H - T - B); };

  var f2 = function(n) { return n.toFixed(2); };

  /* ── grid ──────────────────────────────────────────────────────────────── */
  g.font      = "9px 'JetBrains Mono', Consolas, monospace";
  g.textAlign = 'right';

  [maxP, 0, maxL / 2, maxL].forEach(function(v) {
    var y = py(v);
    g.strokeStyle = (v === 0) ? MP.zero : MP.grid;
    g.lineWidth   = (v === 0) ? 1.2 : 1;
    g.beginPath(); g.moveTo(L, y); g.lineTo(W - R, y); g.stroke();
    g.fillStyle = MP.muted;
    g.fillText((v >= 0 ? '+' : '') + Math.round(v), L - 6, y + 3);
  });

  /* vertical x-axis guides */
  g.strokeStyle = MP.grid;
  g.lineWidth   = 1;
  g.textAlign   = 'center';
  g.fillStyle   = MP.muted;
  var step = Math.max(5, Math.round((hi - lo) / 8 / 5) * 5);
  for (var k = Math.ceil(lo / step) * step; k <= hi; k += step) {
    var xk = px(k);
    g.beginPath(); g.moveTo(xk, T); g.lineTo(xk, H - B); g.stroke();
    g.fillText(String(k), xk, H - B + 13);
  }

  /* ── strike guides ─────────────────────────────────────────────────────── */
  var strikes = [];
  if (p.lp != null) strikes.push([p.lp, MP.loss]);
  if (p.sp != null) strikes.push([p.sp, MP.gain]);
  if (p.sc != null) strikes.push([p.sc, MP.gain]);
  if (p.lc != null) strikes.push([p.lc, MP.loss]);

  strikes.forEach(function(sk) {
    g.strokeStyle    = sk[1];
    g.globalAlpha    = 0.28;
    g.lineWidth      = 1;
    g.setLineDash([3, 4]);
    g.beginPath(); g.moveTo(px(sk[0]), T); g.lineTo(px(sk[0]), H - B); g.stroke();
    g.setLineDash([]);
    g.globalAlpha = 1;
  });

  /* ── profit-zone shade ─────────────────────────────────────────────────── */
  if (p.sp != null && p.sc != null) {
    g.fillStyle = MP.profitFill;
    g.fillRect(px(p.sp), T, px(p.sc) - px(p.sp), H - T - B);
  }

  /* ── expiry payoff (always drawn when strikes + credit present) ─────────── */
  if (p.sp != null && p.lp != null && p.sc != null && p.lc != null && p.credit != null) {
    var expPts = [];
    for (var i = 0; i <= 240; i++) {
      var S = lo + (hi - lo) * i / 240;
      expPts.push([px(S), py((p.credit - condorExp(S, p)) * 100)]);
    }

    /* filled area */
    g.beginPath();
    expPts.forEach(function(q, idx) { if (idx) g.lineTo(q[0], q[1]); else g.moveTo(q[0], q[1]); });
    g.lineTo(px(hi), py(0));
    g.lineTo(px(lo), py(0));
    g.closePath();
    g.fillStyle = MP.expFill;
    g.fill();

    /* curve */
    g.beginPath();
    expPts.forEach(function(q, idx) { if (idx) g.lineTo(q[0], q[1]); else g.moveTo(q[0], q[1]); });
    g.strokeStyle = MP.gain;
    g.lineWidth   = 1.8;
    g.globalAlpha = 1;
    g.stroke();
  }

  /* ── T+0 curve ─────────────────────────────────────────────────────────── */
  var hasT0 = (p.iv != null && p.spot != null && p.t != null
               && p.sp != null && p.lp != null && p.sc != null && p.lc != null
               && p.credit != null);
  if (hasT0) {
    g.beginPath();
    for (var j = 0; j <= 240; j++) {
      var Sj = lo + (hi - lo) * j / 240;
      var yj = py((p.credit - condorT0(Sj, p, p.iv, p.t)) * 100);
      if (j) g.lineTo(px(Sj), yj); else g.moveTo(px(Sj), yj);
    }
    g.strokeStyle   = MP.accent;
    g.lineWidth     = 1.6;
    g.shadowColor   = 'rgba(59,130,246,0.45)';
    g.shadowBlur    = 6;
    g.stroke();
    g.shadowBlur = 0;
  } else {
    /* honest caption when IV/spot unavailable */
    g.textAlign  = 'center';
    g.fillStyle  = MP.muted;
    g.font       = "10px 'JetBrains Mono', Consolas, monospace";
    g.globalAlpha = 0.6;
    g.fillText('T+0 needs live IV + spot', (L + W - R) / 2, T + (H - T - B) / 2);
    g.globalAlpha = 1;
  }

  /* ── breakevens ────────────────────────────────────────────────────────── */
  if (p.sp != null && p.sc != null && p.credit != null) {
    var beLow  = p.sp - p.credit;
    var beHigh = p.sc + p.credit;
    [beLow, beHigh].forEach(function(be) {
      g.fillStyle = MP.mono;
      g.beginPath(); g.arc(px(be), py(0), 2.6, 0, Math.PI * 2); g.fill();
      g.fillStyle  = MP.muted;
      g.textAlign  = 'center';
      g.font       = "9px 'JetBrains Mono', Consolas, monospace";
      g.fillText(f2(be), px(be), py(0) - 7);
    });
  }

  /* ── spot line ─────────────────────────────────────────────────────────── */
  if (p.spot != null) {
    var xs = px(p.spot);
    g.strokeStyle = 'rgba(226,232,240,0.65)';
    g.lineWidth   = 1;
    g.setLineDash([2, 3]);
    g.beginPath(); g.moveTo(xs, T); g.lineTo(xs, H - B); g.stroke();
    g.setLineDash([]);
    g.fillStyle = MP.mono;
    g.textAlign = 'center';
    g.font      = "600 9.5px 'JetBrains Mono', Consolas, monospace";
    g.fillText('SPOT ' + f2(p.spot), xs, T + 10);
  }

  /* ── legend labels ─────────────────────────────────────────────────────── */
  g.textAlign  = 'left';
  g.font       = "9px 'JetBrains Mono', Consolas, monospace";
  var lx = L + 6, ly = T + 12;
  g.fillStyle = MP.gain;  g.fillText('— EXP', lx, ly);
  if (hasT0) { g.fillStyle = MP.accent; g.fillText('— T+0 (' + (p.iv * 100).toFixed(1) + '% IV)', lx + 44, ly); }

  /* ── hover ─────────────────────────────────────────────────────────────── */
  var hv = _hover[canvas.id];
  if (hv && hv.x != null) {
    var S = lo + (hi - lo) * ((hv.x - L) / (W - L - R));
    if (S > lo && S < hi) {
      var xc  = px(S);
      var pe  = p.credit != null ? (p.credit - condorExp(S, p))  * 100 : null;
      var p0  = (hasT0)          ? (p.credit - condorT0(S, p, p.iv, p.t)) * 100 : null;

      /* vertical cursor */
      g.strokeStyle = 'rgba(255,255,255,0.30)';
      g.lineWidth   = 1;
      g.setLineDash([]);
      g.beginPath(); g.moveTo(xc, T); g.lineTo(xc, H - B); g.stroke();

      /* dots */
      if (p0 != null) {
        g.fillStyle = MP.accent;
        g.beginPath(); g.arc(xc, py(p0), 3, 0, Math.PI * 2); g.fill();
      }
      if (pe != null) {
        g.fillStyle = MP.gain;
        g.beginPath(); g.arc(xc, py(pe), 3, 0, Math.PI * 2); g.fill();
      }

      /* tooltip box */
      var lines = [f2(S)];
      if (p0 != null) lines.push('T+0 ' + (p0 >= 0 ? '+' : '') + f2(p0));
      if (pe != null) lines.push('EXP ' + (pe >= 0 ? '+' : '') + f2(pe));
      var bw = 106, bh = 14 + lines.length * 13;
      var bx = Math.min(W - R - bw - 4, Math.max(L, xc + 10));
      var by = T + 6;
      g.fillStyle   = MP.tooltipBg;
      g.strokeStyle = MP.tooltipBorder;
      g.lineWidth   = 1;
      g.beginPath(); g.rect(bx, by, bw, bh); g.fill(); g.stroke();
      g.textAlign = 'left';
      g.font = "10px 'JetBrains Mono', Consolas, monospace";
      g.fillStyle = MP.mono;    g.fillText(lines[0], bx + 7, by + 13);
      if (p0 != null) { g.fillStyle = MP.accent; g.fillText(lines[1], bx + 7, by + 26); }
      if (pe != null) { g.fillStyle = MP.gain;   g.fillText(lines[2], bx + 7, by + (p0 != null ? 39 : 26)); }
    }
  }
}

/* ── attach hover to a single canvas ────────────────────────────────────── */
function attachHover(canvas, p) {
  var id = canvas.id;
  canvas.addEventListener('mousemove', function(e) {
    var r  = canvas.getBoundingClientRect();
    _hover[id] = { x: e.clientX - r.left };
    draw(canvas, p);
  });
  canvas.addEventListener('mouseleave', function() {
    _hover[id] = null;
    draw(canvas, p);
  });
}

/* ── init: find all payoff canvases and wire them up ─────────────────────── */
function initAll() {
  var canvases = document.querySelectorAll('canvas.mace-payoff');
  canvases.forEach(function(canvas) {
    var islandId = canvas.getAttribute('data-island');
    if (!islandId) return;
    var island = document.getElementById(islandId);
    if (!island) return;
    var p;
    try {
      p = JSON.parse(island.textContent || island.innerText || '');
    } catch (e) {
      return;  // malformed island — skip silently
    }
    if (!p || typeof p !== 'object') return;

    draw(canvas, p);
    attachHover(canvas, p);
  });
}

/* ── re-draw on window resize ────────────────────────────────────────────── */
var _resizeTimer;
window.addEventListener('resize', function() {
  clearTimeout(_resizeTimer);
  _resizeTimer = setTimeout(function() {
    var canvases = document.querySelectorAll('canvas.mace-payoff');
    canvases.forEach(function(canvas) {
      var islandId = canvas.getAttribute('data-island');
      if (!islandId) return;
      var island = document.getElementById(islandId);
      if (!island) return;
      try {
        var p = JSON.parse(island.textContent || island.innerText || '');
        if (p && typeof p === 'object') draw(canvas, p);
      } catch (e) { /* ignore */ }
    });
  }, 80);
});

/* ── re-init after htmx:afterSettle (30s poll replaces #mace-rungs) ──────── */
document.addEventListener('htmx:afterSettle', function() {
  initAll();
});

/* ── boot ────────────────────────────────────────────────────────────────── */
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initAll);
} else {
  initAll();
}
