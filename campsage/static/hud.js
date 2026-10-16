/* CampSage HUD: canvas "SAGE" orb, a browser port of the Agent55 reactor core.
 *
 * API (exactly this, on window.CampHUD):
 *   mount(containerEl)   create the canvas inside containerEl and start drawing
 *   setState(name)       'idle' | 'listening' | 'speaking'
 *   setLevel(0..1)       live loudness; drives the radiating lines
 *   unmount()            stop everything and remove the canvas
 *
 * States
 *   idle       calm mint orb, slow drift, faint breathing
 *   listening  cool blue, lines converge INWARD (sound coming in), slow ripple inward
 *   speaking   mint + amber, lines radiate OUTWARD, fast arcs, ripple outward
 *
 * Cost: one small canvas; idle ~30 fps, active up to display rate. Drawing
 * pauses when the tab is hidden or the orb is scrolled out of view, and with
 * prefers-reduced-motion it stops rotating and only redraws when something changes.
 * Style hooks live in hud.css. */
(function (global) {
  'use strict';

  var TAU = Math.PI * 2;
  var DOC = global.document;
  var NAME = 'SAGE';

  var PRESETS = {
    idle:      { act: 0, rate: 0.55, inw: 0 },
    listening: { act: 1, rate: 0.85, inw: 1 },
    speaking:  { act: 1, rate: 2.30, inw: 0 }
  };
  var DEFAULT_COLORS = {
    idle:      [[105, 211, 176], [47, 143, 120]],
    listening: [[92, 200, 255], [180, 236, 255]],
    speaking:  [[105, 211, 176], [255, 180, 84]]
  };
  var CSS_VARS = {
    idle:      ['--hud-idle', '--hud-idle-accent'],
    listening: ['--hud-listen', '--hud-listen-accent'],
    speaking:  ['--hud-speak', '--hud-speak-accent']
  };
  // fraction of radius, span deg, segments, direction, accent?, alpha, width
  var ARCS = [
    [0.955, 118, 2, +1, true,  0.70, 2.0],
    [0.845,  82, 3, -1, false, 0.34, 1.3],
    [0.700, 150, 1, +1, true,  0.35, 1.5]
  ];
  var RAYS = 72;

  var el = null, canvas = null, ctx = null, probe = null;
  var W = 0, H = 0, dpr = 1;
  var raf = 0, last = 0;
  var inView = true, reduced = false;
  var ro = null, io = null, mq = null, addedClass = false;
  var palette = null, textColor = [230, 255, 246];

  var state = 'idle';
  var live = 0, disp = 0;          // raw peak-held level, smoothed level
  var phase = 0, clk = 0, rip = 0; // rate-scaled phase, plain clock, ripple
  var cur = { act: 0, rate: PRESETS.idle.rate, inw: 0,
              main: DEFAULT_COLORS.idle[0].slice(), acc: DEFAULT_COLORS.idle[1].slice() };

  function clamp01(v) { return v < 0 ? 0 : (v > 1 ? 1 : v); }
  function lerp(a, b, k) { return a + (b - a) * k; }
  function rgba(c, a) {
    return 'rgba(' + (c[0] | 0) + ',' + (c[1] | 0) + ',' + (c[2] | 0) + ',' + clamp01(a).toFixed(3) + ')';
  }

  function parseColor(str, fallback) {
    str = (str || '').trim();
    if (!str) return fallback;
    try {
      if (!probe) { probe = DOC.createElement('canvas'); probe.width = probe.height = 1; }
      var x = probe.getContext('2d', { willReadFrequently: true });
      x.clearRect(0, 0, 1, 1);
      x.fillStyle = '#000';
      x.fillStyle = str;
      x.fillRect(0, 0, 1, 1);
      var d = x.getImageData(0, 0, 1, 1).data;
      return [d[0], d[1], d[2]];
    } catch (e) {
      return fallback;
    }
  }

  function readPalette() {
    var cs = global.getComputedStyle(el);
    palette = {};
    Object.keys(CSS_VARS).forEach(function (name) {
      var v = CSS_VARS[name], d = DEFAULT_COLORS[name];
      palette[name] = [
        parseColor(cs.getPropertyValue(v[0]), d[0]),
        parseColor(cs.getPropertyValue(v[1]), d[1])
      ];
    });
    textColor = parseColor(cs.getPropertyValue('--hud-text'), [230, 255, 246]);
  }

  function snapToState() {
    var p = PRESETS[state], c = palette ? palette[state] : DEFAULT_COLORS[state];
    cur.act = p.act; cur.rate = p.rate; cur.inw = p.inw;
    cur.main = c[0].slice(); cur.acc = c[1].slice();
  }

  function canRun() {
    return !!(el && ctx && W > 0 && H > 0 && inView && !DOC.hidden);
  }
  function kick() {
    if (!raf && canRun()) raf = global.requestAnimationFrame(frame);
  }
  function stopLoop() {
    if (raf) { global.cancelAnimationFrame(raf); raf = 0; }
    last = 0;
  }

  function update(dt) {
    var p = PRESETS[state], c = palette[state];
    var k = 1 - Math.exp(-dt * 6);
    cur.act = lerp(cur.act, p.act, k);
    cur.rate = lerp(cur.rate, p.rate, k);
    cur.inw = lerp(cur.inw, p.inw, k);
    for (var i = 0; i < 3; i++) {
      cur.main[i] = lerp(cur.main[i], c[0][i], k);
      cur.acc[i] = lerp(cur.acc[i], c[1][i], k);
    }
    // Level: peak-hold from setLevel(), decay toward silence, then smooth.
    live *= Math.pow(0.86, dt * 60);
    disp += (live - disp) * (1 - Math.pow(0.55, dt * 60));
    if (!reduced) {
      clk += dt;
      phase += dt * cur.rate;
      rip += dt * (0.45 + 0.5 * disp);
    }
  }

  function isSettled() {
    var p = PRESETS[state], c = palette[state];
    if (live > 0.004 || disp > 0.004) return false;
    if (Math.abs(cur.act - p.act) > 0.003 || Math.abs(cur.inw - p.inw) > 0.003) return false;
    for (var i = 0; i < 3; i++) {
      if (Math.abs(cur.main[i] - c[0][i]) > 1.2 || Math.abs(cur.acc[i] - c[1][i]) > 1.2) return false;
    }
    return true;
  }

  function frame(ts) {
    raf = 0;
    if (!canRun()) return;
    var minGap = reduced ? 66 : ((cur.act > 0.05 || disp > 0.02) ? 0 : 33);
    if (last && ts - last < minGap - 2) { raf = global.requestAnimationFrame(frame); return; }
    var dt = last ? Math.min(0.1, (ts - last) / 1000) : 1 / 60;
    last = ts;
    update(dt);
    draw();
    if (!reduced || !isSettled()) raf = global.requestAnimationFrame(frame);
  }

  function glow(cx, cy, rad, a, main) {
    var g = ctx.createRadialGradient(cx, cy, 0, cx, cy, rad);
    g.addColorStop(0.00, rgba(main, Math.min(0.9, a)));
    g.addColorStop(0.45, rgba(main, Math.min(0.9, a * 0.52)));
    g.addColorStop(0.78, rgba(main, Math.min(0.9, a * 0.16)));
    g.addColorStop(1.00, rgba(main, 0));
    ctx.fillStyle = g;
    ctx.beginPath();
    ctx.arc(cx, cy, rad, 0, TAU);
    ctx.fill();
  }

  function draw() {
    if (!ctx || !W || !H) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);

    var cx = W / 2, cy = H / 2;
    var amp = disp, act = cur.act, main = cur.main, acc = cur.acc;
    var breathe = reduced ? 0 : Math.sin(clk * 1.6) * 0.012;
    var R = Math.min(W, H) / 2 * 0.94 * (1 + breathe + (reduced ? 0 : amp * 0.03));
    var i, a, ca, sa;

    // 1. Atmosphere: a soft body of light that brightens with the voice.
    var lift = 1 + 0.55 * amp + 0.18 * act;
    glow(cx, cy, R * 0.78, 0.26 * lift, main);
    glow(cx, cy, R * 0.36, 0.30 * lift, main);

    // 2. Two thin outer circles.
    ctx.lineCap = 'butt';
    ctx.lineWidth = 1;
    ctx.strokeStyle = rgba(main, 0.34);
    ctx.beginPath(); ctx.arc(cx, cy, R, 0, TAU); ctx.stroke();
    ctx.strokeStyle = rgba(main, 0.16);
    ctx.beginPath(); ctx.arc(cx, cy, R * 0.93, 0, TAU); ctx.stroke();

    // 3. Graduations: 24 long majors, shorter minors between.
    ctx.beginPath();
    for (i = 0; i < 72; i += 3) {
      a = i * TAU / 72; ca = Math.cos(a); sa = Math.sin(a);
      ctx.moveTo(cx + ca * R * 0.885, cy + sa * R * 0.885);
      ctx.lineTo(cx + ca * R * 0.985, cy + sa * R * 0.985);
    }
    ctx.lineWidth = 1.3; ctx.strokeStyle = rgba(main, 0.42); ctx.stroke();
    ctx.beginPath();
    for (i = 0; i < 72; i++) {
      if (i % 3 === 0) continue;
      a = i * TAU / 72; ca = Math.cos(a); sa = Math.sin(a);
      ctx.moveTo(cx + ca * R * 0.945, cy + sa * R * 0.945);
      ctx.lineTo(cx + ca * R * 0.985, cy + sa * R * 0.985);
    }
    ctx.lineWidth = 1; ctx.strokeStyle = rgba(main, 0.18); ctx.stroke();

    // 4. Sweeping arcs. Speed is the state; phase is integrated so it never jumps.
    var arcLive = 0.6 + 0.4 * act;
    ctx.lineCap = 'round';
    for (i = 0; i < ARCS.length; i++) {
      var A = ARCS[i], rad = R * A[0], span = A[1] * Math.PI / 180;
      var base = phase * (0.05 + i * 0.025) * A[3] * TAU;
      ctx.lineWidth = A[6];
      ctx.strokeStyle = rgba(A[4] ? acc : main, A[5] * arcLive);
      for (var s = 0; s < A[2]; s++) {
        var st = base + s * (TAU / A[2]);
        ctx.beginPath(); ctx.arc(cx, cy, rad, st, st + span); ctx.stroke();
      }
    }

    // 5. Ripple travelling with the voice: inward while listening, outward while speaking.
    if (!reduced && act > 0.02) {
      var rp = rip % 1;
      var rr = lerp(lerp(0.45, 0.95, rp), lerp(0.92, 0.46, rp), cur.inw);
      ctx.lineWidth = 1.4;
      ctx.strokeStyle = rgba(acc, (1 - rp) * 0.32 * act * (0.5 + 0.5 * Math.min(1, amp * 2 + 0.3)));
      ctx.beginPath(); ctx.arc(cx, cy, R * rr, 0, TAU); ctx.stroke();
    }

    // 6. The voice: radiating lines, 0..1 level. Outward from the name ring when
    //    speaking, converging inward from the arcs when listening.
    var baseR = R * lerp(0.43, 0.82, cur.inw);
    var dir = lerp(1, -1, cur.inw);
    var wt = phase * 2.0;
    ctx.lineWidth = Math.max(1.2, R * 0.011);
    ctx.strokeStyle = rgba(act > 0.4 ? acc : main, 0.28 + 0.6 * amp + 0.12 * act);
    ctx.beginPath();
    for (i = 0; i < RAYS; i++) {
      a = i * TAU / RAYS - Math.PI / 2; ca = Math.cos(a); sa = Math.sin(a);
      var wob = 0.5 + 0.5 * Math.sin(wt + i * 0.42);
      var wob2 = 0.5 + 0.5 * Math.sin(wt * 0.63 + i * 1.13);
      var h = R * (0.018 + 0.012 * Math.sin(clk * 1.2 + i * 0.7) * (reduced ? 0 : 1)
                   + amp * 0.40 * (0.3 + 0.45 * wob + 0.25 * wob2));
      var r0 = baseR, r1 = baseR + dir * h;
      ctx.moveTo(cx + ca * r0, cy + sa * r0);
      ctx.lineTo(cx + ca * r1, cy + sa * r1);
    }
    ctx.stroke();

    // 7. Inner ring the name sits in.
    var inner = R * 0.355 * (1 + (reduced ? 0 : amp * 0.04));
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = rgba(acc, 0.30 + 0.45 * amp + 0.15 * act);
    ctx.beginPath(); ctx.arc(cx, cy, inner, 0, TAU); ctx.stroke();

    // 8. The name, letter-spaced by hand and sized to fit inside the ring.
    var fsz = Math.max(9, R * 0.15);
    var space = fsz * 0.28;
    ctx.font = '700 ' + fsz.toFixed(1) + 'px ui-monospace,"SFMono-Regular",Menlo,Consolas,monospace';
    ctx.textBaseline = 'middle';
    ctx.textAlign = 'left';
    var total = -space, widths = [];
    for (i = 0; i < NAME.length; i++) {
      var w = ctx.measureText(NAME.charAt(i)).width;
      widths.push(w); total += w + space;
    }
    var maxW = inner * 1.5;
    if (total > maxW) {
      fsz *= maxW / total;
      space *= maxW / total;
      ctx.font = '700 ' + fsz.toFixed(1) + 'px ui-monospace,"SFMono-Regular",Menlo,Consolas,monospace';
      total = -space;
      for (i = 0; i < NAME.length; i++) {
        widths[i] = ctx.measureText(NAME.charAt(i)).width;
        total += widths[i] + space;
      }
    }
    ctx.fillStyle = rgba(textColor, 0.78 + 0.22 * Math.min(1, amp * 2));
    var x = cx - total / 2;
    for (i = 0; i < NAME.length; i++) {
      ctx.fillText(NAME.charAt(i), x, cy + fsz * 0.04);
      x += widths[i] + space;
    }
  }

  // ---- lifecycle -----------------------------------------------------------

  function resize() {
    if (!el || !canvas) return;
    var w = el.clientWidth, h = el.clientHeight;
    dpr = Math.min(2, global.devicePixelRatio || 1);
    if (!w || !h) { W = H = 0; stopLoop(); return; }
    W = w; H = h;
    var pw = Math.round(w * dpr), ph = Math.round(h * dpr);
    if (canvas.width !== pw || canvas.height !== ph) { canvas.width = pw; canvas.height = ph; }
    draw();     // draw at once so a resize never shows a blank frame
    kick();
  }

  function onVisibility() {
    if (DOC.hidden) stopLoop(); else { last = 0; kick(); }
  }
  function onMotionChange() {
    reduced = !!(mq && mq.matches);
    last = 0;
    kick();
  }

  function setAria() {
    if (canvas) canvas.setAttribute('aria-label', 'CampSage voice indicator: ' + state);
    if (el) el.setAttribute('data-state', state);
  }

  function mount(container) {
    if (typeof container === 'string') container = DOC.querySelector(container);
    if (!container || container.nodeType !== 1) {
      throw new TypeError('CampHUD.mount: a container element is required');
    }
    if (el) unmount();
    el = container;
    if (!el.classList.contains('camphud')) { el.classList.add('camphud'); addedClass = true; }

    canvas = DOC.createElement('canvas');
    canvas.setAttribute('role', 'img');
    ctx = canvas.getContext('2d');
    if (!ctx) { unmount(); return; }
    el.appendChild(canvas);

    readPalette();
    snapToState();
    setAria();

    mq = global.matchMedia ? global.matchMedia('(prefers-reduced-motion: reduce)') : null;
    reduced = !!(mq && mq.matches);
    if (mq) {
      if (mq.addEventListener) mq.addEventListener('change', onMotionChange);
      else if (mq.addListener) mq.addListener(onMotionChange);
    }
    DOC.addEventListener('visibilitychange', onVisibility);

    inView = true;
    if (global.IntersectionObserver) {
      io = new global.IntersectionObserver(function (entries) {
        var e = entries[entries.length - 1];
        inView = !!(e && e.isIntersecting);
        if (inView) { last = 0; kick(); } else { stopLoop(); }
      });
      io.observe(el);
    }
    if (global.ResizeObserver) {
      ro = new global.ResizeObserver(resize);
      ro.observe(el);
    } else {
      global.addEventListener('resize', resize);
    }
    resize();
  }

  function unmount() {
    stopLoop();
    if (ro) { ro.disconnect(); ro = null; } else { global.removeEventListener('resize', resize); }
    if (io) { io.disconnect(); io = null; }
    if (mq) {
      if (mq.removeEventListener) mq.removeEventListener('change', onMotionChange);
      else if (mq.removeListener) mq.removeListener(onMotionChange);
      mq = null;
    }
    DOC.removeEventListener('visibilitychange', onVisibility);
    if (canvas && canvas.parentNode) canvas.parentNode.removeChild(canvas);
    if (el) {
      el.removeAttribute('data-state');
      if (addedClass) el.classList.remove('camphud');
    }
    el = canvas = ctx = null;
    addedClass = false;
    W = H = 0;
    live = disp = 0;
  }

  function setState(name) {
    if (!Object.prototype.hasOwnProperty.call(PRESETS, name)) return;
    if (name === state) return;
    state = name;
    setAria();
    kick();
  }

  function setLevel(v) {
    v = +v;
    if (!isFinite(v)) return;
    v = clamp01(v);
    if (v > live) live = v;
    if (v > 0) kick();
  }

  global.CampHUD = Object.freeze({
    mount: mount,
    setState: setState,
    setLevel: setLevel,
    unmount: unmount
  });
})(window);
