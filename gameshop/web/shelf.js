/* The shelf: every console as a full-screen 3D shelf of boxes, the selected one facing you and the
   rest standing spine-out on either side, like a TV launcher.

   Adapted from Boxshelf by sassiestrhihno (https://github.com/sassiestrhihno/boxshelf-source,
   GPL-3.0): the case markup (mount), the windowed carousel that only builds the cases near the
   selection, and where each case sits for the current selection (place) follow its renderer/app.js;
   the look follows its renderer/style.css. Written down to ES2017 here for the PS5's WebKit.
   This file is GPL-3.0, like the rest of Game Shop. */
(function () {
  'use strict';

  // width / height and depth / width of each kind of box (systems.toml's case names)
  var SHAPES = {
    dvd: [0.70, 0.10], bd: [0.80, 0.09], 'mini-dvd': [0.72, 0.09], umd: [0.58, 0.10], switch: [0.62, 0.07],
    jewel: [1.00, 0.08], 'box-tall': [0.72, 0.17], 'box-wide': [1.40, 0.13], 'box-small': [0.82, 0.16],
    clam: [0.72, 0.12], 'ds-box': [0.88, 0.12], generic: [0.75, 0.10]
  };
  var BOX = { 'cart-nes': 'box-tall', 'cart-snes': 'box-wide', 'cart-n64': 'box-wide', 'cart-gb': 'box-small', 'cart-genesis': 'clam',
    'cart-sms': 'clam', 'cart-ds': 'ds-box', 'cart-2600': 'box-tall', 'cart-lynx': 'box-small' };
  var BAND = { switch: 'SWITCH', ps2: 'PS2', ps3: 'PS3', ps4: 'PS4', ps5: 'PS5', psp: 'PSP', psx: 'PS1', xbox: 'XBOX',
    xbox360: '360', wii: 'Wii', gc: 'GCN', n64: 'N64', snes: 'SNES', nes: 'NES', gba: 'GBA', gb: 'GB', gbc: 'GBC', nds: 'DS',
    n3ds: '3DS', genesis: 'MD', dreamcast: 'DC', saturn: 'SAT' };
  var WIN = 11;                  // cases built on each side of the selection

  var G = null;                  // the store's helpers, handed over by app.js
  var root, track, slots = [], sel = 0, flipped = false, shelves = [], cur = 0, bw = 240, bh = 340, bd = 24, gen = 0;

  function $(s) { return root.querySelector(s); }
  function shapeOf(item) {
    var sys = G.sysOf(item.system), c = (item.case || sys.case || 'generic').split(' ')[0];
    if (c.indexOf('cart-') === 0) c = BOX[c] || 'box-tall';
    return SHAPES[c] || SHAPES.generic;
  }
  function artURL(item, w) { return '/art/' + item.id + '?w=' + w + '&v=' + encodeURIComponent(G.version()); }

  // ---- sizes: the box height follows the screen, like Boxshelf's measure() ----
  function measure() {
    var vw = innerWidth, vh = innerHeight, r = shelves[cur] && shelves[cur].ratio || 0.72;
    bh = vw < 760 ? Math.min(vh * 0.32, vw * 0.42 / r) : Math.min(vh * 0.42, vw * 0.24 / r, 560);
    bw = bh * r; bd = bw * 0.12;
    root.style.setProperty('--bh', bh + 'px');
  }

  // ---- one case ----
  // both sides carry the spine: the cases left of the selection show their left side, the ones on the right their right
  function spine(cls, sys, it) {
    return '<div class="face ' + cls + '"><div class="spine-band" style="background:' + G.esc(sys.color || '#555') + '">' +
      G.esc(BAND[sys.id] || sys.name.slice(0, 4).toUpperCase()) + '</div><div class="spine-title">' + G.esc(it.title) + '</div></div>';
  }
  function mount(s) {
    var it = s.item, sys = G.sysOf(it.system), sh = shapeOf(it);
    var w = bh * sh[0], d = w * sh[1];
    var el = document.createElement('div');
    el.className = 'slot';
    el.style.setProperty('--bw', w + 'px'); el.style.setProperty('--bd', d + 'px');
    el.style.setProperty('--glow', sys.color || '#88a');
    el.innerHTML = '<div class="box"><div class="face f-front"><div class="cover' + (it.art ? '' : ' noart') + '"><span>' + G.esc(it.title) + '</span></div>' +
      '<div class="dim"></div><div class="glare"></div><div class="sheen"></div></div>' +
      '<div class="face f-back"><div class="back-in"><b>' + G.esc(it.title) + '</b><span>' + G.esc(sys.name) + '</span></div></div>' +
      spine('f-left', sys, it) + spine('f-right', sys, it) + '</div>';
    el.addEventListener('click', function () { if (s.i === sel) flip(); else go(s.i); });
    var next = null;
    for (var j = s.i + 1; j < slots.length; j++) { if (slots[j].el) { next = slots[j].el; break; } }
    track.insertBefore(el, next);
    s.el = el; s.box = el.firstElementChild; s.glare = el.querySelector('.glare'); s.drawn = false;
    el.style.transition = 'none';
    requestAnimationFrame(function () { requestAnimationFrame(function () { if (s.el === el) el.style.transition = ''; }); });
  }
  function unmount(s) { if (s.el) { s.el.remove(); s.el = s.box = s.glare = null; } }
  function draw(s) {
    if (s.drawn || !s.item.art) return;
    s.drawn = true;
    var img = new Image();
    img.alt = ''; img.draggable = false; img.decoding = 'async';
    img.onload = function () {
      var c = s.el && s.el.querySelector('.cover');
      if (!c) return;
      // a square icon (Switch, PS4 packages) sits on its own colours, not cropped
      var w = s.el.querySelector('.f-front').offsetWidth, h = s.el.querySelector('.f-front').offsetHeight || 1;
      c.classList.toggle('sq', img.naturalWidth > img.naturalHeight * (w / h) * 1.2);
      c.style.setProperty('--art', 'url("' + img.src + '")');
      c.innerHTML = ''; c.appendChild(img);
    };
    img.src = artURL(s.item, 600);
  }

  // ---- where everything sits for the current selection (Boxshelf's layout() and place()) ----
  function layout(instant) {
    slots.forEach(function (s, i) {
      var ad = Math.abs(i - sel);
      if (ad > WIN + 4) { unmount(s); return; }
      if (ad > WIN && !s.el) return;
      if (!s.el) mount(s);
      if (instant) s.el.style.transition = 'none';
      place(s, i);
    });
    if (instant) { void track.offsetWidth; slots.forEach(function (s) { if (s.el) s.el.style.transition = ''; }); }
    var s0 = slots[sel];
    if (s0) s0.box.style.transform = 'rotateY(' + (flipped ? 180 : 0) + 'deg)';
    info(); background(); land();
  }
  function place(s, i) {
    var d = i - sel, ad = Math.abs(d), sg = d > 0 ? 1 : d < 0 ? -1 : 0;
    var x = 0, z = bw * 1.05, ry = 22;
    if (d !== 0) { x = sg * (bw * 1.22 + (ad - 1) * (bw * 0.22 + bd * 0.9)); z = -bw * 0.7 - ad * 6; ry = -sg * 60; }
    s.el.classList.toggle('far', ad > WIN - 2);
    s.el.classList.toggle('sel', d === 0);
    s.el.style.transform = 'translate3d(' + x + 'px,0,' + z + 'px) rotateY(' + ry + 'deg)';
    if (ad <= 3) s.glare.style.transform = 'translateX(' + ((-ry / 110 - x / (bw * 7)) * 83.25).toFixed(1) + '%)';
    if (d !== 0) s.box.style.transform = '';
    draw(s);
  }
  var landT;
  function land() {
    clearTimeout(landT);
    slots.forEach(function (s) { if (s.el) s.el.classList.remove('landed'); });
    var s = slots[sel];
    if (s) landT = setTimeout(function () { if (s.el) s.el.classList.add('landed'); }, 380);
  }

  // ---- the title strip, the counter and the backdrop ----
  function info() {
    var it = slots[sel] && slots[sel].item, sys = it ? G.sysOf(it.system) : null;
    $('.counter').innerHTML = slots.length ? '<b>' + (sel + 1) + '</b> / ' + slots.length : '';
    if (!it) { $('.g-title').textContent = 'Nothing here yet'; $('.g-meta').innerHTML = ''; return; }
    var f = it.facts || {}, st = it.status || {};
    var meta = [cur === 0 ? sys.name : '', f.year || it.region || '', f.publisher || '', G.fmtSize(it.size), G.stateLabel(st)];
    $('.g-title').textContent = it.title;
    $('.g-meta').innerHTML = meta.filter(Boolean).map(function (m) { return '<span>' + G.esc(m) + '</span>'; }).join('');
    var inf = $('.info'); inf.classList.remove('swap'); void inf.offsetWidth; inf.classList.add('swap');
  }
  var bgT, bgFlip = false;
  function background() {
    clearTimeout(bgT);
    bgT = setTimeout(function () {
      var it = slots[sel] && slots[sel].item; if (!it) return;
      var sys = G.sysOf(it.system);
      var a = $(bgFlip ? '.bgA' : '.bgB'), b = $(bgFlip ? '.bgB' : '.bgA'); bgFlip = !bgFlip;
      a.querySelector('.tint').style.background = 'radial-gradient(ellipse 70% 60% at 50% 40%,' + (sys.color || '#446') + '55,transparent 70%),linear-gradient(180deg,#0b0d18,#05060b)';
      a.querySelector('img').src = it.art ? artURL(it, 300) : '';
      a.querySelector('img').hidden = !it.art;
      a.classList.add('on'); b.classList.remove('on');
      root.style.setProperty('--sel-glow', (sys.color || '#7890ff') + '73');
    }, 160);
  }

  // ---- moving ----
  function go(i) {
    if (!slots.length) return;
    i = Math.max(0, Math.min(slots.length - 1, i));
    if (i === sel) return;
    sel = i; flipped = false; layout(false);
    shelves[cur].sel = sel;
  }
  function flip() { flipped = !flipped; var s = slots[sel]; if (s) s.box.style.transform = 'rotateY(' + (flipped ? 180 : 0) + 'deg)'; }
  function open() { var s = slots[sel]; if (s) location.hash = '#/game/' + s.item.id; }

  function tabs() {
    $('.tabs').innerHTML = shelves.map(function (sh, i) {
      return '<button class="tab' + (i === cur ? ' on' : '') + '" data-i="' + i + '" type="button">' + G.esc(sh.short) + '</button>';
    }).join('');
    Array.prototype.forEach.call(root.querySelectorAll('.tab'), function (t) { t.addEventListener('click', function () { shelf(+t.dataset.i); }); });
    var on = root.querySelector('.tab.on'); if (on && on.scrollIntoView) on.scrollIntoView({ block: 'nearest', inline: 'center' });
  }
  function shelf(i) {
    if (!shelves.length) return;
    cur = (i + shelves.length) % shelves.length;
    var sh = shelves[cur], my = ++gen;
    $('.sys-word').textContent = sh.name; $('.sys-count').textContent = sh.sub;
    tabs();
    root.classList.add('swap');
    G.memo('gameshop.shelf', sh.id);
    sh.load().then(function (items) {
      if (my !== gen) return;
      sh.items = items;
      slots.forEach(unmount);
      slots = items.map(function (it, i) { return { i: i, item: it, el: null }; });
      sel = Math.min(sh.sel || 0, Math.max(0, slots.length - 1)); flipped = false;
      measure(); layout(true);
      requestAnimationFrame(function () { root.classList.remove('swap'); });
    }).catch(function (e) { G.toast(e.message); });
  }

  // ---- keys: the PS5 browser's pad arrives as arrows, Enter, Escape and PageUp / PageDown ----
  function key(ev) {
    if (!root || !root.isConnected) { document.removeEventListener('keydown', key, true); return; }
    var k = ev.key, used = true;
    if (k === 'ArrowLeft') go(sel - 1);
    else if (k === 'ArrowRight') go(sel + 1);
    else if (k === 'ArrowUp' || k === 'PageUp' || k === 'q') shelf(cur - 1);
    else if (k === 'ArrowDown' || k === 'PageDown' || k === 'e') shelf(cur + 1);
    else if (k === 'Home') go(0);
    else if (k === 'End') go(slots.length - 1);
    else if (k === 'Enter' || k === ' ') open();
    else if (k === 'f' || k === 'y') flip();
    else if (k === 'Escape' || k === 'Backspace') location.hash = '#/';
    else used = false;
    if (used) { ev.preventDefault(); ev.stopPropagation(); }
  }
  var wheelAt = 0;
  function wheel(ev) {
    var dx = Math.abs(ev.deltaX) > Math.abs(ev.deltaY) ? ev.deltaX : ev.deltaY;
    if (Math.abs(dx) < 4 || ev.timeStamp - wheelAt < 90) return;
    wheelAt = ev.timeStamp; ev.preventDefault(); go(sel + (dx > 0 ? 1 : -1));
  }
  function clock() {
    if (!root || !root.isConnected) return;
    var d = new Date();
    $('.clock').innerHTML = d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric' }) + '<b>' + d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }) + '</b>';
    setTimeout(clock, 15000);
  }

  // ---- the page ----
  window.GameShelf = function (helpers, view, sysId) {
    G = helpers;
    view.innerHTML = '<div class="shelf-app">' +
      '<div class="bg-layer bgA"><div class="tint"></div><img alt=""></div><div class="bg-layer bgB"><div class="tint"></div><img alt=""></div>' +
      '<div class="vignette"></div><div class="floor"></div><div class="sel-halo"></div>' +
      '<div class="scene"><div class="track"></div></div>' +
      '<header class="hud-top"><div class="brand"><div class="sys-word"></div><div class="sys-count"></div></div>' +
      '<nav class="tabs" aria-label="Consoles"></nav>' +
      '<div class="right"><div class="clock"></div><div class="counter"></div><a class="mini" href="#/" data-nav>Store</a></div></header>' +
      '<div class="info"><div class="g-title"></div><div class="g-meta"></div></div>' +
      '<div class="hints">' +
      '<span class="hint"><span class="g">←</span><span class="g">→</span>Browse</span>' +
      '<span class="hint"><span class="g">L1</span><span class="g">R1</span>Console</span>' +
      '<span class="hint"><span class="g">✕</span>Open</span>' +
      '<span class="hint"><span class="g">F</span>Flip</span>' +
      '<span class="hint"><span class="g">○</span>Store</span></div></div>';
    root = view.querySelector('.shelf-app'); track = $('.track');
    document.body.classList.add('shelf-on');
    var systems = G.systems().filter(function (s) { return s.count > 0; });
    shelves = [{ id: 'recent', name: 'Recently added', short: 'New', sub: 'Newest first', ratio: 0.72,
      load: function () { return G.api('/api/discover').then(function (d) { return d.recent; }); } }].concat(systems.map(function (s) {
      var c = (s.case || 'generic').split(' ')[0]; if (c.indexOf('cart-') === 0) c = BOX[c] || 'box-tall';
      return { id: s.id, name: s.name, short: s.name.replace(/^(Nintendo|Sony|Sega|Microsoft) /, ''), sub: s.count + ' game' + (s.count === 1 ? '' : 's'),
        ratio: (SHAPES[c] || SHAPES.generic)[0],
        load: function () { return G.api('/api/items?system=' + encodeURIComponent(s.id) + '&status=1'); } };
    }));
    var want = sysId || G.recall('gameshop.shelf') || 'recent', idx = 0;
    shelves.forEach(function (sh, i) { if (sh.id === want) idx = i; });
    document.addEventListener('keydown', key, true);
    root.addEventListener('wheel', wheel, { passive: false });
    window.addEventListener('resize', function () { if (root && root.isConnected) { measure(); layout(true); } });
    clock();
    shelf(idx);
  };
})();
