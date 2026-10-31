/* Game Shop storefront. Plain ES2017 so the PS5's WebKit runs it. */
(function () {
  'use strict';
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var view = $('#view');
  var state = { app: null, systems: {}, jobs: [], tv: false, route: '' };

  // ---------- helpers ----------
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function fmtSize(n) { if (!n) return '0 B'; var u = ['B', 'KB', 'MB', 'GB', 'TB'], i = 0; while (n >= 1024 && i < 4) { n /= 1024; i++; } return (i ? n.toFixed(n < 10 ? 2 : n < 100 ? 1 : 0) : n) + ' ' + u[i]; }
  function api(path, opts) {
    opts = opts || {};
    var o = { method: opts.method || 'GET', headers: {} };
    if (opts.body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(opts.body); }
    return fetch(path, o).then(function (r) {
      return r.text().then(function (t) {
        var j = null; try { j = t ? JSON.parse(t) : null; } catch (e) { j = { detail: t }; }
        if (!r.ok) { var err = new Error((j && j.detail) || r.statusText); err.status = r.status; throw err; }
        return j;
      });
    });
  }
  var toastTimer;
  function toast(msg, ms) { var t = $('#toast'); t.textContent = msg; t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.hidden = true; }, ms || 3200); }
  function sysOf(id) { return state.systems[id] || { id: id, name: id, case: 'generic', color: '#888', route: 'none' }; }
  function isCart(c) { return c.indexOf('cart-') === 0; }

  // ---------- 3D case ----------
  function caseHTML(item, opts) {
    opts = opts || {};
    var sys = sysOf(item.system);
    var c = item.case || sys.case || 'generic';
    var cls = 'case ' + c + (isCart(c) ? ' cart' : '') + (opts.spin ? ' spin' : '');
    var band = c === 'bd' || c === 'dvd' || c === 'switch';
    var gen = '<div class="gen">' + (band ? '' : '<div class="g-sys">' + esc(sys.name) + '</div>') + '<div class="g-title">' + esc(item.title) + '</div></div>';
    var img = item.art ? '<img src="/art/' + item.id + '" alt="" onerror="this.parentNode.innerHTML=this.nextSibling.innerHTML" loading="lazy"><template>' + gen + '</template>' : gen;
    var front = isCart(c) ? '<div class="label">' + img + '</div>' : img + (band ? '<div class="band">' + esc(sys.name.toUpperCase()) + '</div>' : '');
    return '<div class="' + cls + '" style="--sys:' + esc(sys.color) + '">' +
      '<div class="face front">' + front + '</div>' +
      '<div class="face spine"><span>' + esc(item.title) + '</span></div>' +
      '<div class="face back"></div></div>';
  }

  // ---------- tiles ----------
  function stateLabel(st) {
    if (!st) return '';
    var m = { onps5: 'On your PS5', running: 'Copying', queued: 'Queued', verifying: 'Checking', unsupported: 'No PS5 target', failed: 'Failed', nodest: 'Set folder', sent: 'Sent' };
    return m[st.state] || '';
  }
  function tileHTML(item) {
    var sys = sysOf(item.system);
    var st = item.status, lab = stateLabel(st);
    return '<a class="tile" href="#/game/' + item.id + '" data-nav style="--sys:' + esc(sys.color) + '">' +
      (lab ? '<span class="state ' + esc(st.state) + '">' + lab + '</span>' : '') +
      (item.peer ? '<span class="peer-badge" title="On another PC">' + esc(item.peer_name) + '</span>' : '') +
      '<div class="art-wrap">' + caseHTML(item) + '</div>' +
      '<div class="t-title">' + esc(item.title) + '</div>' +
      '<div class="t-sub"><span>' + esc(sys.name) + '</span>' + (item.facts && item.facts.year ? '<span>· ' + esc(item.facts.year) + '</span>' : item.region ? '<span>· ' + esc(item.region) + '</span>' : '') + '<span>· ' + fmtSize(item.size) + '</span></div></a>';
  }
  function shelfHTML(items) { return '<div class="shelf">' + items.map(tileHTML).join('') + '</div>'; }
  function scaleTiles(root) { $$('.tile .case', root).forEach(function (c) { c.style.setProperty('--scale', c.classList.contains('cart') ? '0.95' : '0.85'); }); }

  // ---------- pages ----------
  var pages = {};

  pages.discover = function () {
    return api('/api/discover').then(function (d) {
      if (!d.shelves.length) {
        view.innerHTML = '<div class="empty"><h1>The shelves are empty</h1><p>Add a folder of your backups under Settings → Sources, pick its console, and the store fills itself.</p><p><a class="btn gold" href="#/settings" data-nav>Open Settings</a></p></div>';
        return;
      }
      var hero = d.recent.filter(function (x) { return sysOf(x.system).route !== 'none'; })[0] || d.recent[0];
      var sys = sysOf(hero.system);
      var html = '<section class="hero" style="--sys:' + esc(sys.color) + '"><div class="hero-text">' +
        '<div class="eyebrow">New in ' + esc(sys.name) + '</div><h1>' + esc(hero.title) + '</h1>' +
        '<div class="meta">' + (hero.facts && hero.facts.publisher ? esc(hero.facts.publisher) + ' · ' : '') + esc(sys.name) + (hero.facts && hero.facts.year ? ' · ' + esc(hero.facts.year) : hero.region ? ' · ' + esc(hero.region) : '') + ' · ' + fmtSize(hero.size) + (hero.peer ? ' · on <b>' + esc(hero.peer_name) + '</b>' : '') + '</div>' +
        '<div class="desc">' + esc((hero.facts && hero.facts.description) || sys.note || ((hero.facts && hero.facts.genre ? hero.facts.genre + '. ' : '') + 'Ready to copy to your PS5 with one button. It lands in ' + (hero.dest || 'the folder you choose') + '.')) + '</div>' +
        '<a class="btn" href="#/game/' + hero.id + '" data-nav>⊙ &nbsp;View details</a>' +
        '<div class="hero-dots">' + d.recent.slice(0, 5).map(function (_, i) { return '<span class="' + (i === 0 ? 'on' : '') + '"></span>'; }).join('') + '</div>' +
        '</div><div class="hero-art"><div style="--scale:1.6">' + caseHTML(hero, { spin: true }) + '</div></div></section>';
      html += '<h2>Recently added <span class="count">' + d.recent.length + '</span></h2>' + shelfHTML(d.recent);
      d.shelves.forEach(function (s) {
        html += '<h2><a href="#/dept/' + s.system + '" data-nav>' + esc(s.name) + '</a> <span class="count">' + s.count + '</span></h2>' + shelfHTML(s.items);
      });
      view.innerHTML = html;
      $$('.hero-art .case').forEach(function (c) { c.style.setProperty('--scale', c.classList.contains('cart') ? '1.7' : '1.45'); });
      scaleTiles(view);
    });
  };

  pages.departments = function () {
    var groups = {};
    state.app.systems.forEach(function (s) { (groups[s.maker] = groups[s.maker] || []).push(s); });
    var html = '<h1>Consoles</h1><p class="muted">One department per console. Greyed ones have backups but no PS5 emulator yet.</p>';
    Object.keys(groups).forEach(function (maker) {
      var list = groups[maker].filter(function (s) { return s.count > 0; });
      if (!list.length) return;
      html += '<div class="maker-group"><h2>' + esc(maker) + '</h2><div class="dept-cards">' + list.map(function (s) {
        return '<a class="dept-card" href="#/dept/' + s.id + '" data-nav style="--sys:' + esc(s.color) + '">' + caseHTML({ id: '', system: s.id, title: s.name, case: s.case }) +
          '<div><div class="n">' + esc(s.name) + '</div><div class="c">' + s.count + ' game' + (s.count === 1 ? '' : 's') + (s.route === 'none' ? ' · no PS5 target yet' : s.route === 'ftp' && !s.dest ? ' · set folder' : '') + '</div></div></a>';
      }).join('') + '</div></div>';
    });
    view.innerHTML = html;
  };

  pages.dept = function (id, q) {
    var sys = sysOf(id);
    var sort = q.sort || 'az';
    return api('/api/items?system=' + encodeURIComponent(id) + '&status=1' + (q.q ? '&q=' + encodeURIComponent(q.q) : '')).then(function (items) {
      if (sort === 'size') items.sort(function (a, b) { return b.size - a.size; });
      if (sort === 'new') items.sort(function (a, b) { return b.added - a.added; });
      var html = '<div class="dept-head">' + caseHTML({ id: '', system: id, title: sys.name, case: sys.case }) + '<div><div class="eyebrow">' + esc(sys.maker) + '</div><h1>' + esc(sys.name) + '</h1>' +
        '<div class="meta">' + items.length + ' games · ' + (sys.route === 'none' ? 'downloads off' : sys.route === 'dpi' ? 'installs via Direct Package Installer' : sys.dest ? 'downloads go to <b>' + esc(sys.dest) + '</b>' : '<b>no PS5 folder set</b>') + '</div></div></div>';
      if (sys.note) html += '<div class="note">' + esc(sys.note) + '</div>';
      html += '<div class="toolbar"><input id="dept-q" placeholder="Filter this console" value="' + esc(q.q || '') + '" data-nav>' +
        '<select id="dept-sort" data-nav><option value="az"' + (sort === 'az' ? ' selected' : '') + '>A to Z</option><option value="new"' + (sort === 'new' ? ' selected' : '') + '>Recently added</option><option value="size"' + (sort === 'size' ? ' selected' : '') + '>Largest first</option></select></div>';
      html += items.length ? '<div class="tiles">' + items.map(tileHTML).join('') + '</div>' : '<div class="empty">Nothing here matches.</div>';
      view.innerHTML = html;
      scaleTiles(view);
      $('#dept-q').addEventListener('change', function () { location.hash = '#/dept/' + id + '?q=' + encodeURIComponent(this.value) + '&sort=' + sort; });
      $('#dept-sort').addEventListener('change', function () { location.hash = '#/dept/' + id + '?q=' + encodeURIComponent(q.q || '') + '&sort=' + this.value; });
    });
  };

  pages.game = function (id) {
    return api('/api/items/' + id).then(function (it) {
      var sys = sysOf(it.system);
      var st = it.status || { state: 'ready' };
      var files = it.files.map(function (f) { return '<div>' + esc(f[0].split('/').pop()) + ' <span class="dim">' + fmtSize(f[1]) + '</span></div>'; }).join('');
      var meta = it.meta || {}, facts = it.facts || {};
      var chips = [sys.name, it.region, facts.genre, facts.players ? facts.players + (facts.players === 1 ? ' player' : ' players') : '', facts.rating, (meta.ext || '').toUpperCase(), meta.category, meta.title_id, meta.disc ? 'Disc ' + meta.disc : ''].concat(it.tags || []).filter(Boolean);
      var html = '<a class="muted" href="#/dept/' + it.system + '" data-nav>‹ ' + esc(sys.name) + '</a>' +
        '<div class="product" style="--sys:' + esc(sys.color) + '">' +
        '<div class="art-stage" id="stage">' + caseHTML(it, { spin: !state.tv }) + '</div>' +
        '<div><div class="eyebrow">' + esc(sys.maker) + '</div><h1>' + esc(it.title) + '</h1>' +
        '<div class="meta">' + (facts.publisher ? esc(facts.publisher) + ' · ' : '') + esc(sys.name) + (facts.year ? ' · ' + esc(facts.year) : it.region ? ' · ' + esc(it.region) : '') + ' · ' + fmtSize(it.size) + (it.files.length > 1 ? ' · ' + it.files.length + ' files' : '') + '</div>' +
        (facts.description ? '<p class="desc">' + esc(facts.description) + '</p>' : '') +
        '<div class="chips">' + chips.map(function (c) { return '<span class="pill">' + esc(c) + '</span>'; }).join('') + '</div>' +
        (it.system_note ? '<div class="note">' + esc(it.system_note) + '</div>' : '') +
        '<dl class="facts">' + (facts.year ? '<dt>Released</dt><dd>' + esc(facts.year) + (facts.month ? '-' + ('0' + facts.month).slice(-2) : '') + '</dd>' : '') + (facts.developer ? '<dt>Developer</dt><dd>' + esc(facts.developer) + '</dd>' : '') + (facts.publisher ? '<dt>Publisher</dt><dd>' + esc(facts.publisher) + '</dd>' : '') + (facts.franchise ? '<dt>Series</dt><dd>' + esc(facts.franchise) + '</dd>' : '') + (facts.serial ? '<dt>Serial</dt><dd>' + esc(facts.serial) + '</dd>' : '') +
        '<dt>Files</dt><dd>' + files + '</dd><dt>' + (it.peer ? 'On the PC' : 'On this PC') + '</dt><dd>' + (it.peer ? '<b>' + esc(it.peer_name) + '</b> · ' : '') + esc(it.source) + '</dd>' +
        (it.also_on && it.also_on.length ? '<dt>Also on</dt><dd>' + esc(it.also_on.join(', ')) + '</dd>' : '') + '<dt>Primary file</dt><dd>' + esc(it.rel) + '</dd>' +
        (meta.pkg_title ? '<dt>Package title</dt><dd>' + esc(meta.pkg_title) + '</dd>' : '') + (meta.content_id ? '<dt>Content ID</dt><dd>' + esc(meta.content_id) + '</dd>' : '') +
        (meta.app_version ? '<dt>Version</dt><dd>' + esc(meta.app_version) + '</dd>' : '') +
        '<dt>Goes to</dt><dd>' + (it.route === 'dpi' ? 'Installed by the PS5 (Direct Package Installer)' : it.route === 'elf' ? 'ELF loader, port 9021' : esc(it.dest || 'no folder set')) + '</dd></dl>' +
        '<p style="margin-top:22px"><button class="btn small" id="flip" data-nav>Flip the case</button> ' +
        (it.tile_possible ? '<button class="btn small" id="tile" data-nav title="Make a home-screen tile in RetroArch (needs its WebUI running)">Add to PS5 home screen</button>' : '') + '</p>' +
        (it.art ? '<div class="shots" id="shots"><img src="/art/' + it.id + '?kind=snap" alt="" onerror="this.remove()"><img src="/art/' + it.id + '?kind=title" alt="" onerror="this.remove()"></div>' : '') + '</div>' +
        '<div class="card get" id="get"></div></div>';
      view.innerHTML = html;
      $('#stage .case').style.setProperty('--scale', isCart(it.case) ? '2.2' : '2.3');
      $('#flip').addEventListener('click', function () { var c = $('#stage .case'); c.classList.remove('spin'); c.classList.toggle('flip'); });
      var tileBtn = $('#tile');
      if (tileBtn) tileBtn.addEventListener('click', function () { tileBtn.disabled = true; api('/api/items/' + it.id + '/tile', { method: 'POST' }).then(function () { toast('Tile created. It appears on the home screen once RetroArch closes.', 5000); }).catch(function (e) { toast(e.message, 6000); }).then(function () { tileBtn.disabled = false; }); });
      renderGet(it, st);
      watchItem(it.id);
    });
  };

  function renderGet(it, st) {
    var box = $('#get'); if (!box) return;
    var sys = sysOf(it.system);
    var word, sub = '', pct = 0, icon = '↓', btn = true, btnText = 'Download';
    var s = st.state;
    if (s === 'ready') { word = 'Ready'; sub = it.route === 'dpi' ? 'The PS5 downloads it from this PC and installs it' : it.route === 'elf' ? 'Sent to the ELF loader' : 'Copies over the network to your PS5'; }
    else if (s === 'queued') { word = 'Queued'; sub = 'Waiting for the current download'; btn = false; icon = '…'; }
    else if (s === 'running' || s === 'verifying') { word = s === 'verifying' ? 'Checking' : 'Copying'; pct = st.total ? Math.round(st.done * 100 / st.total) : 0; sub = (st.step || '') + (st.total ? ' · ' + pct + '%' : ''); btn = false; icon = pct + '%'; }
    else if (s === 'onps5') { word = 'On your PS5'; sub = 'Already in ' + (it.dest || ''); icon = '✓'; btnText = 'Copy again'; }
    else if (s === 'sent') { word = 'Sent'; sub = st.step || ''; icon = '✓'; btnText = it.route === 'dpi' ? 'Install again' : 'Send again'; }
    else if (s === 'failed') { word = 'Failed'; sub = st.error || ''; icon = '!'; btnText = 'Try again'; }
    else if (s === 'unsupported') { word = 'Not yet'; sub = st.reason || 'No PS5 emulator for this console yet'; btn = false; icon = '–'; }
    else if (s === 'nodest') { word = 'Set a folder'; sub = st.reason; btn = false; icon = '?'; }
    var offline = state.app && !state.app.ps5.online;
    var dests = (it.destinations || []).filter(function (d) { return d.dest !== it.dest; });
    box.innerHTML = '<div class="eyebrow">Get this game</div><div class="ring" style="--p:' + pct + '"><span>' + icon + '</span></div>' +
      '<div class="state-word">' + esc(word) + '</div><div class="muted" style="font-size:13px">' + esc(sub) + '</div>' +
      '<button class="btn gold" id="dl" data-nav' + (btn && !offline ? '' : ' disabled') + '>⤓ &nbsp;' + btnText + '</button>' +
      (offline ? '<div class="dest-line bad">PS5 is offline. Check Settings.</div>' : '') +
      (it.route === 'ftp' && btn && dests.length ? '<select id="dl-to" data-nav><option value="">Download to… (another folder)</option>' + dests.map(function (d) { return '<option value="' + esc(d.dest) + '">' + esc(d.name) + ' — ' + esc(d.dest) + '</option>'; }).join('') + '</select>' : '') +
      (it.dest ? '<div class="dest-line">→ ' + esc(it.dest) + '</div>' : '') +
      (s === 'running' || s === 'queued' ? '<button class="btn small danger" id="cancel" data-nav style="margin-top:10px">Cancel</button>' : '');
    var dl = $('#dl');
    if (dl) dl.addEventListener('click', function () { startDownload(it.id); });
    var sel = $('#dl-to');
    if (sel) sel.addEventListener('change', function () { if (this.value) startDownload(it.id, this.value); });
    var cancel = $('#cancel');
    if (cancel) cancel.addEventListener('click', function () { api('/api/jobs/' + st.job + '/cancel', { method: 'POST' }).then(function () { toast('Cancelled'); }); });
  }
  function startDownload(id, dest) {
    api('/api/items/' + id + '/download', { method: 'POST', body: dest ? { dest: dest } : {} }).then(function (job) {
      toast(job.route === 'dpi' ? 'Asked the PS5 to install it' : 'Added to Downloads');
      refreshJobs();
    }).catch(function (e) { toast(e.message); });
  }
  var itemWatch, lastStatusKey;
  function watchItem(id) {
    clearInterval(itemWatch); lastStatusKey = null;
    itemWatch = setInterval(function () {
      if (state.route.indexOf('#/game/' + id) !== 0) { clearInterval(itemWatch); return; }
      api('/api/items/' + id).then(function (it) {
        var key = JSON.stringify(it.status) + (state.app && state.app.ps5.online);
        if (key === lastStatusKey) return;
        lastStatusKey = key;
        var focusedId = document.activeElement && document.activeElement.id; renderGet(it, it.status); if (focusedId) { var el = document.getElementById(focusedId); if (el) el.focus(); }
      });
    }, 1500);
  }

  pages.downloads = function () {
    return api('/api/jobs').then(function (jobs) {
      state.jobs = jobs;
      var html = '<h1>Downloads</h1><div class="toolbar"><span class="muted">' + jobs.filter(function (j) { return j.state === 'queued' || j.state === 'running'; }).length + ' active</span>' +
        '<button class="btn small" id="clear" data-nav>Clear finished</button></div>';
      if (!jobs.length) html += '<div class="empty">Nothing downloaded yet. Open a game and press Download.</div>';
      html += jobs.map(function (j) {
        var pct = j.total ? Math.min(100, Math.round(j.done * 100 / j.total)) : (j.state === 'done' ? 100 : 0);
        var speed = '';
        if (j.state === 'running' && j.started && j.done) { var s = j.done / Math.max(1, Date.now() / 1000 - j.started); speed = ' · ' + fmtSize(s) + '/s'; if (j.total > j.done) speed += ' · ' + Math.round((j.total - j.done) / Math.max(1, s)) + 's left'; }
        var cls = j.state === 'done' ? 'done' : (j.state === 'failed' || j.state === 'cancelled') ? 'failed' : '';
        return '<div class="row"><div class="grow"><div class="title">' + esc(j.title) + ' <span class="dim">' + esc(sysOf(j.system).name || '') + '</span></div>' +
          '<div class="sub">' + esc(j.state === 'failed' ? 'Failed: ' + j.error : j.step || j.state) + (j.total ? ' · ' + fmtSize(j.done) + ' of ' + fmtSize(j.total) : '') + speed + (j.dest ? ' · → ' + esc(j.dest) : '') + '</div>' +
          '<div class="bar ' + cls + '"><i style="width:' + pct + '%"></i></div></div>' +
          (j.state === 'queued' || j.state === 'running' || j.state === 'verifying' ? '<button class="btn small danger" data-cancel="' + j.id + '" data-nav>Cancel</button>' : '') +
          (j.state === 'failed' || j.state === 'cancelled' ? '<button class="btn small" data-retry="' + j.id + '" data-nav>Retry</button>' : '') + '</div>';
      }).join('');
      view.innerHTML = html;
      $('#clear').addEventListener('click', function () { api('/api/jobs/clear', { method: 'POST' }).then(render); });
      $$('[data-cancel]').forEach(function (b) { b.addEventListener('click', function () { api('/api/jobs/' + b.dataset.cancel + '/cancel', { method: 'POST' }).then(render); }); });
      $$('[data-retry]').forEach(function (b) { b.addEventListener('click', function () { api('/api/jobs/' + b.dataset.retry + '/retry', { method: 'POST' }).then(render); }); });
    });
  };

  pages.ps5 = function (_, q) {
    var path = q.path || '/data/homebrew';
    var html = '<h1>On your PS5</h1><p class="muted">Browse the console over etaHEN\'s FTP. Downloads land in the folders below.</p>';
    html += '<div class="toolbar">' + state.app.systems.filter(function (s) { return s.dest && s.count; }).map(function (s) { return '<a class="pill" href="#/ps5?path=' + encodeURIComponent(s.dest) + '" data-nav>' + esc(s.name) + '</a>'; }).join('') + '</div>';
    html += '<div class="pane" id="ps5-pane"><div class="path">' + esc(path) + '</div><ul><li class="dim">Loading…</li></ul></div>';
    view.innerHTML = html;
    loadPs5Pane($('#ps5-pane'), path, function (p) { location.hash = '#/ps5?path=' + encodeURIComponent(p); }, true);
  };
  function loadPs5Pane(pane, path, onNav, allowDelete) {
    var list = $('ul', pane); $('.path', pane).textContent = path;
    api('/api/ps5/ls?path=' + encodeURIComponent(path)).then(function (entries) {
      var parent = path.replace(/\/+$/, '').split('/').slice(0, -1).join('/') || '/';
      var items = [];
      if (path !== '/') items.push('<li data-dir="' + esc(parent) + '" data-nav tabindex="0">‹ ..</li>');
      entries.forEach(function (e) {
        items.push('<li ' + (e.is_dir ? 'data-dir="' + esc((path.replace(/\/+$/, '') + '/' + e.name)) + '"' : 'data-file="' + esc(path.replace(/\/+$/, '') + '/' + e.name) + '"') + ' data-nav tabindex="0">' + (e.is_dir ? '📁' : '📄') + ' ' + esc(e.name) + '<span class="sz">' + (e.is_dir ? '' : fmtSize(e.size)) + '</span>' +
          (allowDelete ? '<button class="btn small danger" data-del="' + esc(path.replace(/\/+$/, '') + '/' + e.name) + '" data-isdir="' + (e.is_dir ? 1 : 0) + '" data-nav>Delete</button>' : '') + '</li>');
      });
      list.innerHTML = items.join('') || '<li class="dim">Empty</li>';
      $$('li[data-dir]', list).forEach(function (li) { li.addEventListener('click', function (ev) { if (ev.target.dataset.del) return; onNav(li.dataset.dir); }); li.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') onNav(li.dataset.dir); }); });
      $$('[data-del]', list).forEach(function (b) { b.addEventListener('click', function (ev) { ev.stopPropagation(); if (!confirm('Delete ' + b.dataset.del + ' from the PS5?')) return; api('/api/ps5/delete', { method: 'POST', body: { path: b.dataset.del, is_dir: b.dataset.isdir === '1' } }).then(function () { loadPs5Pane(pane, path, onNav, allowDelete); }).catch(function (e) { toast(e.message); }); }); });
    }).catch(function (e) { list.innerHTML = '<li class="bad">' + esc(e.message) + '</li>'; });
  }

  pages.settings = function () {
    var cfg = state.app.config, h = state.app.ps5;
    function hl(ok, label) { return '<li><span class="' + (ok ? 'ok' : 'bad') + '">' + (ok ? '●' : '○') + '</span> ' + label + '</li>'; }
    var html = '<h1>Settings</h1><div class="grid-2">' +
      '<div class="card form"><h2 style="margin-top:0">PS5</h2><label>Address</label><div class="inline"><input id="ps5-host" value="' + esc(cfg.ps5_host) + '" placeholder="192.168.1.50"><button class="btn small" id="ps5-save" data-nav>Save</button><button class="btn small" id="ps5-scan" data-nav>Scan network</button></div>' +
      '<div class="health"><ul>' + hl(h.ftp, 'FTP (1337) — copies files') + hl(h.dpi2 || h.dpi1, 'Direct Package Installer (' + (h.dpi2 ? '12800' : h.dpi1 ? '9090' : 'off') + ') — PS4 packages') + hl(h.elfldr, 'ELF loader (9021) — payloads') + hl(h.retroarch, 'RetroArch WebUI (6769)') + '</ul>' +
      (h.hints || []).map(function (x) { return '<div class="note">' + esc(x) + '</div>'; }).join('') + '</div>' +
      '<label>RetroArch title folder on the PS5</label><input id="ra-root" value="' + esc(cfg.ra_root) + '">' +
      '<label><input type="checkbox" id="skip" ' + (cfg.skip_existing ? 'checked' : '') + ' style="width:auto"> Skip files already on the PS5 with the same size</label>' +
      '<label><input type="checkbox" id="art" ' + (cfg.art_enabled ? 'checked' : '') + ' style="width:auto"> Fetch cover art from libretro-thumbnails (this PC needs internet)</label>' +
      '<p><button class="btn" id="opts-save" data-nav>Save options</button></p></div>' +
      '<div class="card form"><h2 style="margin-top:0">Sources <span class="count">folders on this PC</span></h2>' +
      (cfg.sources.length ? cfg.sources.map(function (s) { return '<div class="row"><div class="grow"><div class="title">' + esc(s.path) + '</div><div class="sub">' + esc(sysOf(s.system).name || s.system) + '</div></div><button class="btn small" data-smb="' + esc(s.path) + '" data-nav title="Play this folder in place from RetroArch over SMB">Share</button><button class="btn small danger" data-rm="' + esc(s.path) + '" data-nav>Remove</button></div>'; }).join('') : '<p class="muted">No folders yet.</p>') +
      '<label>Add a folder</label><div class="inline"><input id="src-path" placeholder="D:\\Games\\PS1"><button class="btn small" id="src-browse" data-nav>Browse…</button></div>' +
      '<label>Which console is in it?</label><select id="src-sys"><option value="mixed">Mixed (guess by file type, unambiguous only)</option>' + state.app.systems.filter(function (s) { return s.route !== 'elf'; }).map(function (s) { return '<option value="' + s.id + '">' + esc(s.name) + '</option>'; }).join('') + '</select>' +
      '<p><button class="btn gold" id="src-add" data-nav>Add source</button> <button class="btn" id="rescan" data-nav>Rescan all</button></p></div></div>';
    html += '<div class="card" style="margin-top:20px"><h2 style="margin-top:0">Destinations <span class="count">where each console\'s games go on the PS5</span></h2><p class="muted">Blank uses the default. {ra} is the RetroArch folder above.</p><table class="dest-table">' +
      state.app.systems.filter(function (s) { return s.route === 'ftp'; }).map(function (s) {
        return '<tr><td style="width:220px"><b>' + esc(s.name) + '</b><div class="dim" style="font-size:12px">' + esc(s.default_dest || 'no default — enter one') + '</div></td><td><input data-dest="' + s.id + '" value="' + esc(cfg.destinations[s.id] || '') + '" placeholder="' + esc(s.default_dest || '/data/...') + '"></td></tr>';
      }).join('') + '</table><p><button class="btn" id="dest-save" data-nav>Save destinations</button></p></div>';
    // peers
    var peers = state.app.peers || [];
    html += '<div class="grid-2" style="margin-top:20px"><div class="card form"><h2 style="margin-top:0">Other PCs <span class="count">peers</span></h2>' +
      '<p class="muted">Agents on the network are found by themselves. Add one by name or address for another LAN or Tailscale (<code>basement-pc</code>, <code>100.101.102.103:8750</code>). Their games appear in the store and copy to the PS5 through this PC.</p>' +
      (peers.length ? peers.map(function (p) { return '<div class="row"><span class="dot ' + (p.online ? 'on' : '') + '" style="background:' + (p.online ? 'var(--green)' : 'var(--red)') + '"></span><div class="grow"><div class="title">' + esc(p.name) + ' <span class="dim">' + esc(p.url) + '</span></div><div class="sub">' + (p.online ? p.count + ' games' : 'offline' + (p.error ? ' · ' + esc(p.error) : '')) + ' · ' + esc(p.source) + '</div></div>' + (p.source === 'static' ? '<button class="btn small danger" data-rmpeer="' + esc(p.key) + '" data-nav>Remove</button>' : '') + '</div>'; }).join('') : '<p class="muted">No other PCs seen yet.</p>') +
      '<label>Add a PC</label><div class="inline"><input id="peer-add" placeholder="hostname, Tailscale name or ip[:port]"><button class="btn small" id="peer-add-btn" data-nav>Add</button><button class="btn small" id="peer-refresh" data-nav>Refresh</button></div>' +
      '<label><input type="checkbox" id="discovery" ' + (cfg.discovery ? 'checked' : '') + ' style="width:auto"> Announce this PC and find others on the LAN (mDNS)</label>' +
      '<p class="dim" style="font-size:12px">This PC: ' + esc(cfg.name) + ' · ' + esc(state.app.lan_ip) + ':' + cfg.port + ' · id ' + esc(cfg.agent_id) + '</p></div>';
    // payloads
    var pl = cfg.payloads || [];
    html += '<div class="card form"><h2 style="margin-top:0">Payloads <span class="count">after every reboot</span></h2>' +
      '<p class="muted">The chain is sent in order to the ELF loader (9021), waiting the given seconds before each one. Usual order: kstuff, ShadowMount+, etaHEN.</p>' +
      (pl.length ? pl.map(function (x, i) { return '<div class="row"><div class="grow"><div class="title">' + (i + 1) + '. ' + esc(x.name || x.path.split(/[\\/]/).pop()) + '</div><div class="sub">' + esc(x.path) + (i ? ' · wait ' + (x.delay || 0) + 's' : '') + '</div></div>' +
        '<button class="btn small" data-plup="' + i + '" data-nav>↑</button><button class="btn small" data-plsend="' + i + '" data-nav>Send</button><button class="btn small danger" data-plrm="' + i + '" data-nav>Remove</button></div>'; }).join('') : '<p class="muted">No payloads yet.</p>') +
      '<label>Add a payload</label><div class="inline"><input id="pl-path" placeholder="C:\\payloads\\kstuff.elf"><button class="btn small" id="pl-browse" data-nav>Browse…</button></div>' +
      '<div class="inline" style="margin-top:8px"><input id="pl-name" placeholder="Name (optional)"><input id="pl-delay" type="number" min="0" value="8" style="width:110px" title="seconds to wait before this one"><span class="dim">s wait</span><button class="btn small" id="pl-add" data-nav>Add</button></div>' +
      '<p><button class="btn gold" id="pl-chain" data-nav' + (pl.length ? '' : ' disabled') + '>⚡ Load the chain</button></p></div></div>';
    html += '<div class="card" style="margin-top:20px"><h2 style="margin-top:0">Advanced: copy anything</h2><p class="muted">Pick files or a folder on this PC (left), a folder on the PS5 (right), then Copy.</p>' +
      '<div class="two-pane"><div class="pane" id="lp"><div class="path"></div><ul></ul></div><div class="pane" id="rp"><div class="path"></div><ul></ul></div></div>' +
      '<p class="toolbar"><button class="btn gold" id="copy-manual" data-nav>Copy selected → PS5 folder</button><span class="muted" id="sel-info"></span><span style="flex:1"></span>' +
      '<input id="elf-path" placeholder="C:\\payloads\\etaHEN.elf" style="width:320px"><button class="btn" id="elf-send" data-nav>Send payload (9021)</button></p></div>';
    view.innerHTML = html;

    $('#ps5-save').addEventListener('click', function () { api('/api/config', { method: 'PUT', body: { ps5_host: $('#ps5-host').value.trim() } }).then(function () { toast('Saved'); return loadState(true); }).then(render); });
    $('#ps5-scan').addEventListener('click', function () { toast('Scanning the network…', 8000); api('/api/ps5/scan', { method: 'POST', body: {} }).then(function (r) { if (!r.found.length) return toast('No PS5 answered on ' + r.prefix + '.x'); $('#ps5-host').value = r.found[0]; toast('Found ' + r.found.join(', ')); }); });
    $('#opts-save').addEventListener('click', function () { api('/api/config', { method: 'PUT', body: { ra_root: $('#ra-root').value.trim(), skip_existing: $('#skip').checked, art_enabled: $('#art').checked } }).then(function () { toast('Saved'); return loadState(); }).then(render); });
    $('#dest-save').addEventListener('click', function () { var d = {}; $$('[data-dest]').forEach(function (i) { if (i.value.trim()) d[i.dataset.dest] = i.value.trim(); }); api('/api/config', { method: 'PUT', body: { destinations: d } }).then(function () { toast('Saved'); return loadState(); }).then(render); });
    $('#src-add').addEventListener('click', function () { api('/api/sources', { method: 'POST', body: { path: $('#src-path').value.trim(), system: $('#src-sys').value } }).then(function () { toast('Added. Scanning…'); return loadState(); }).then(render).catch(function (e) { toast(e.message); }); });
    $('#rescan').addEventListener('click', function () { api('/api/rescan', { method: 'POST' }).then(function () { toast('Rescanning'); }); });
    $$('[data-rm]').forEach(function (b) { b.addEventListener('click', function () { api('/api/sources?path=' + encodeURIComponent(b.dataset.rm), { method: 'DELETE' }).then(loadState).then(render); }); });
    $('#src-browse').addEventListener('click', function () { pickLocal(function (p) { $('#src-path').value = p; }, false); });
    $$('[data-smb]').forEach(function (b) { b.addEventListener('click', function () { api('/api/smb/info?path=' + encodeURIComponent(b.dataset.smb)).then(showSmb).catch(function (e) { toast(e.message); }); }); });
    // peers
    function savePeers(list) { return api('/api/config', { method: 'PUT', body: { peers: list } }).then(function () { return loadState(); }).then(render); }
    $('#peer-add-btn').addEventListener('click', function () { var v = $('#peer-add').value.trim(); if (!v) return; savePeers((cfg.peers || []).concat([v])); });
    $('#peer-refresh').addEventListener('click', function () { api('/api/peers/refresh', { method: 'POST' }).then(function () { toast('Asking the other PCs…'); setTimeout(function () { loadState().then(render); }, 2500); }); });
    $('#discovery').addEventListener('change', function () { api('/api/config', { method: 'PUT', body: { discovery: this.checked } }).then(function () { toast('Saved. Restart Game Shop to apply.'); }); });
    $$('[data-rmpeer]').forEach(function (b) { b.addEventListener('click', function () { var key = b.dataset.rmpeer; savePeers((cfg.peers || []).filter(function (x) { var hp = x.replace(/^https?:\/\//, '').replace(/\/$/, ''); return hp !== key && hp + ':8750' !== key && hp.split(':')[0] !== key.split(':')[0]; })); }); });
    // payloads
    function savePl(list) { return api('/api/config', { method: 'PUT', body: { payloads: list } }).then(function () { return loadState(); }).then(render); }
    $('#pl-browse').addEventListener('click', function () { pickLocal(function (p) { $('#pl-path').value = p; }, true); });
    $('#pl-add').addEventListener('click', function () { var path = $('#pl-path').value.trim(); if (!path) return; savePl(pl.concat([{ name: $('#pl-name').value.trim(), path: path, delay: parseInt($('#pl-delay').value, 10) || 0 }])); });
    $$('[data-plrm]').forEach(function (b) { b.addEventListener('click', function () { var i = +b.dataset.plrm; savePl(pl.filter(function (_, j) { return j !== i; })); }); });
    $$('[data-plup]').forEach(function (b) { b.addEventListener('click', function () { var i = +b.dataset.plup; if (!i) return; var l = pl.slice(); var t = l[i - 1]; l[i - 1] = l[i]; l[i] = t; savePl(l); }); });
    $$('[data-plsend]').forEach(function (b) { b.addEventListener('click', function () { var x = pl[+b.dataset.plsend]; api('/api/ps5/payload', { method: 'POST', body: { path: x.path, name: x.name } }).then(function () { toast('Sending ' + (x.name || x.path)); refreshJobs(); }).catch(function (e) { toast(e.message); }); }); });
    $('#pl-chain').addEventListener('click', function () { api('/api/ps5/chain', { method: 'POST', body: {} }).then(function (jobs) { toast('Chain queued: ' + jobs.length + ' payloads. Watch Downloads.'); refreshJobs(); }).catch(function (e) { toast(e.message); }); });
    $('#elf-send').addEventListener('click', function () { api('/api/ps5/payload', { method: 'POST', body: { path: $('#elf-path').value.trim() } }).then(function () { toast('Payload queued'); }).catch(function (e) { toast(e.message); }); });

    // two-pane
    var sel = {};
    var lp = $('#lp'), rp = $('#rp'), rpath = { v: '/data' };
    function loadLocal(path) {
      api('/api/local/ls?path=' + encodeURIComponent(path || '')).then(function (d) {
        $('.path', lp).textContent = d.path || 'This PC';
        var items = [];
        if (d.parent !== undefined) items.push('<li data-dir="' + esc(d.parent) + '" data-nav tabindex="0">‹ ..</li>');
        d.entries.forEach(function (e) { items.push('<li data-p="' + esc(e.path) + '" ' + (e.is_dir ? 'data-dir="' + esc(e.path) + '"' : '') + ' data-nav tabindex="0">' + (e.is_dir ? '📁' : '📄') + ' ' + esc(e.name) + '<span class="sz">' + (e.is_dir ? '' : fmtSize(e.size)) + '</span></li>'); });
        $('ul', lp).innerHTML = items.join('');
        $$('li', lp).forEach(function (li) {
          li.addEventListener('click', function (ev) { if (li.dataset.p) { if (sel[li.dataset.p]) delete sel[li.dataset.p]; else sel[li.dataset.p] = 1; li.classList.toggle('sel'); $('#sel-info').textContent = Object.keys(sel).length + ' selected'; } });
          li.addEventListener('dblclick', function () { if (li.dataset.dir !== undefined) loadLocal(li.dataset.dir); });
          li.addEventListener('keydown', function (ev) { if (ev.key === 'Enter' && li.dataset.dir !== undefined) loadLocal(li.dataset.dir); });
        });
      });
    }
    loadLocal('');
    function navRight(p) { rpath.v = p; loadPs5Pane(rp, p, navRight, false); }
    loadPs5Pane(rp, rpath.v, navRight, false);
    $('#copy-manual').addEventListener('click', function () { var paths = Object.keys(sel); if (!paths.length) return toast('Select something on the left first'); api('/api/jobs/manual', { method: 'POST', body: { paths: paths, dest: rpath.v } }).then(function () { toast('Copying to ' + rpath.v); sel = {}; $$('li.sel', lp).forEach(function (l) { l.classList.remove('sel'); }); refreshJobs(); }).catch(function (e) { toast(e.message); }); });
  };

  function pickLocal(done, wantFile) {
    var m = $('#modal'); m.hidden = false;
    m.innerHTML = '<div class="box"><h2 style="margin-top:0">' + (wantFile ? 'Choose a file' : 'Choose a folder') + '</h2><div class="pane" id="pick"><div class="path"></div><ul></ul></div><p class="toolbar">' + (wantFile ? '' : '<button class="btn gold" id="pick-ok" data-nav>Use this folder</button>') + '<button class="btn" id="pick-cancel" data-nav>Cancel</button></p></div>';
    var cur = { v: '' };
    function load(path) {
      api('/api/local/ls?path=' + encodeURIComponent(path || '')).then(function (d) {
        cur.v = d.path; $('#pick .path').textContent = d.path || 'This PC';
        var items = d.parent !== undefined ? ['<li data-dir="' + esc(d.parent) + '" data-nav tabindex="0">‹ ..</li>'] : [];
        d.entries.forEach(function (e) {
          if (e.is_dir) items.push('<li data-dir="' + esc(e.path) + '" data-nav tabindex="0">📁 ' + esc(e.name) + '</li>');
          else if (wantFile) items.push('<li data-file="' + esc(e.path) + '" data-nav tabindex="0">📄 ' + esc(e.name) + '<span class="sz">' + fmtSize(e.size) + '</span></li>');
        });
        $('#pick ul').innerHTML = items.join('');
        $$('#pick li').forEach(function (li) {
          function go() { if (li.dataset.file) { m.hidden = true; done(li.dataset.file); } else load(li.dataset.dir); }
          li.addEventListener('click', go); li.addEventListener('keydown', function (ev) { if (ev.key === 'Enter') go(); });
        });
      });
    }
    load('');
    var ok = $('#pick-ok'); if (ok) ok.addEventListener('click', function () { m.hidden = true; if (cur.v) done(cur.v); });
    $('#pick-cancel').addEventListener('click', function () { m.hidden = true; });
  }
  function showSmb(info) {
    var m = $('#modal'); m.hidden = false;
    m.innerHTML = '<div class="box"><h2 style="margin-top:0">Play in place over SMB</h2><p class="muted">RetroArch on the PS5 can browse one shared folder. Share this one read-only, then point RetroArch at it (its WebUI → SMB). Small ROMs play straight from this PC; big disc images are better copied.</p>' +
      '<label class="dim">1. Run once in an administrator PowerShell</label><pre class="code">' + esc(info.command) + '</pre>' +
      '<label class="dim">2. In RetroArch\'s WebUI → SMB</label><dl class="facts"><dt>Server</dt><dd>' + esc(info.server) + '</dd><dt>Share</dt><dd>' + esc(info.share) + '</dd><dt>User</dt><dd>(guest, leave empty)</dd><dt>Workgroup</dt><dd>' + esc(info.workgroup) + '</dd></dl>' +
      '<p class="dim" style="font-size:13px">' + esc(info.note) + '</p><p class="dim" style="font-size:12px">To stop sharing: <code>' + esc(info.remove) + '</code></p>' +
      '<p class="toolbar"><button class="btn" id="smb-close" data-nav>Close</button></p></div>';
    $('#smb-close').addEventListener('click', function () { m.hidden = true; });
  }

  // ---------- router ----------
  function parseHash() {
    var h = location.hash || '#/';
    var qi = h.indexOf('?'), q = {};
    if (qi >= 0) { h.slice(qi + 1).split('&').forEach(function (kv) { var p = kv.split('='); q[decodeURIComponent(p[0])] = decodeURIComponent(p[1] || ''); }); h = h.slice(0, qi); }
    var parts = h.replace(/^#\//, '').split('/');
    return { page: parts[0] || 'discover', arg: parts[1] ? decodeURIComponent(parts[1]) : '', q: q };
  }
  function render() {
    var r = parseHash();
    state.route = location.hash;
    $$('#tabs a').forEach(function (a) { a.classList.toggle('active', a.dataset.tab === (r.page === 'dept' || r.page === 'game' ? 'departments' : r.page)); });
    var fn = pages[r.page] || pages.discover;
    view.innerHTML = '<div class="empty dim">Loading…</div>';
    Promise.resolve(fn(r.arg, r.q)).then(function () {
      window.scrollTo(0, 0);
      if (state.tv) focusFirst();
    }).catch(function (e) { view.innerHTML = '<div class="empty"><h1>Something went wrong</h1><p>' + esc(e.message) + '</p></div>'; });
  }
  function loadState(refresh) {
    return api('/api/state' + (refresh ? '?refresh=1' : '')).then(function (s) {
      state.app = s; state.systems = {};
      s.systems.forEach(function (x) { state.systems[x.id] = x; });
      $('#agent-name').textContent = s.name;
      var chip = $('#ps5-chip'); chip.classList.toggle('online', !!s.ps5.online);
      $('#ps5-chip-text').textContent = s.ps5.host ? (s.ps5.online ? 'PS5 ' + s.ps5.host : 'PS5 offline') : 'No PS5 set';
      return s;
    });
  }
  function refreshJobs() {
    return api('/api/jobs').then(function (jobs) {
      state.jobs = jobs;
      var active = jobs.filter(function (j) { return j.state === 'queued' || j.state === 'running' || j.state === 'verifying'; }).length;
      var b = $('#dl-badge'); b.hidden = !active; b.textContent = active;
      if (parseHash().page === 'downloads' && active) pages.downloads();
    });
  }

  // ---------- TV mode: spatial navigation ----------
  function focusables() { return $$('[data-nav], a.tile, button, input, select, li[tabindex]').filter(function (el) { if (el.disabled || el.offsetParent === null) return false; var r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; }); }
  var focusMemory = {};
  function focusFirst() {
    var remembered = focusMemory[location.hash];
    if (remembered) { var el = document.querySelector('[href="' + remembered + '"]'); if (el) { setFocus(el); return; } }
    var els = focusables().filter(function (e) { return e.closest('#view'); });
    var pref = els.filter(function (e) { return e.classList.contains('tile') || e.classList.contains('dept-card') || e.id === 'dl'; });
    if (pref.length) setFocus(pref[0]); else if (els.length) setFocus(els[0]);
  }
  function setFocus(el) { $$('.focused').forEach(function (e) { e.classList.remove('focused'); }); el.classList.add('focused'); el.focus({ preventScroll: true }); el.scrollIntoView({ block: 'nearest', inline: 'nearest', behavior: 'smooth' }); if (el.getAttribute('href')) focusMemory[location.hash] = el.getAttribute('href'); }
  function move(dir) {
    var cur = document.activeElement && document.activeElement.closest('[data-nav], li[tabindex]') ? document.activeElement : $('.focused');
    var els = focusables();
    if (!cur || els.indexOf(cur) < 0) { if (els.length) setFocus(els[0]); return; }
    var cr = cur.getBoundingClientRect(), cx = cr.left + cr.width / 2, cy = cr.top + cr.height / 2;
    var best = null, bestScore = Infinity;
    els.forEach(function (el) {
      if (el === cur) return;
      var r = el.getBoundingClientRect(), x = r.left + r.width / 2, y = r.top + r.height / 2, dx = x - cx, dy = y - cy;
      var ok = dir === 'left' ? dx < -8 : dir === 'right' ? dx > 8 : dir === 'up' ? dy < -8 : dy > 8;
      if (!ok) return;
      var along = Math.abs(dir === 'left' || dir === 'right' ? dx : dy), across = Math.abs(dir === 'left' || dir === 'right' ? dy : dx);
      var score = along + across * 2.5;
      if (score < bestScore) { bestScore = score; best = el; }
    });
    if (best) setFocus(best);
  }
  function setTV(on) {
    state.tv = on; document.body.classList.toggle('tv', on); $('#tv-toggle').classList.toggle('on', on);
    try { localStorage.setItem('gameshop.tv', on ? '1' : ''); } catch (e) { }
    if (on) focusFirst();
  }
  document.addEventListener('keydown', function (ev) {
    var tag = (ev.target.tagName || '').toLowerCase();
    var typing = tag === 'input' || tag === 'select' || tag === 'textarea';
    if (ev.key === 'ArrowLeft' || ev.key === 'ArrowRight' || ev.key === 'ArrowUp' || ev.key === 'ArrowDown') {
      if (typing && (ev.key === 'ArrowLeft' || ev.key === 'ArrowRight')) return;
      if (!state.tv && !document.activeElement.classList.contains('focused') && tag !== 'body') return;
      ev.preventDefault(); move(ev.key.replace('Arrow', '').toLowerCase());
    } else if (ev.key === 'Enter' && !typing) {
      var f = $('.focused'); if (f && f !== document.activeElement) { ev.preventDefault(); f.click(); }
    } else if ((ev.key === 'Escape' || ev.key === 'Backspace') && !typing) {
      ev.preventDefault(); if (!$('#modal').hidden) { $('#modal').hidden = true; } else history.back();
    } else if (state.tv && !typing) {
      // L1/R1 on the PS5 browser arrive as PageUp/PageDown; Triangle/Square as nothing standard, so also accept s / d.
      var tabs = $$('#tabs a'), idx = tabs.findIndex(function (a) { return a.classList.contains('active'); });
      if (ev.key === 'PageUp' || ev.key === 'q') { ev.preventDefault(); tabs[(idx + tabs.length - 1) % tabs.length].click(); }
      if (ev.key === 'PageDown' || ev.key === 'e') { ev.preventDefault(); tabs[(idx + 1) % tabs.length].click(); }
      if (ev.key === 'd') { location.hash = '#/downloads'; }
      if (ev.key === 's') { setTV(false); $('#search').focus(); }
    }
  });
  document.addEventListener('focusin', function (ev) { var el = ev.target.closest('[data-nav], li[tabindex]'); if (el) { $$('.focused').forEach(function (e) { if (e !== el) e.classList.remove('focused'); }); el.classList.add('focused'); } });
  $('#tv-toggle').addEventListener('click', function () { setTV(!state.tv); });
  $('#ps5-chip').addEventListener('click', function () { location.hash = '#/settings'; });
  $('#search-form').addEventListener('submit', function (ev) { ev.preventDefault(); var q = $('#search').value.trim(); if (!q) return; api('/api/items?q=' + encodeURIComponent(q) + '&limit=60').then(function (items) { view.innerHTML = '<h1>Search: ' + esc(q) + '</h1>' + (items.length ? '<div class="tiles">' + items.map(tileHTML).join('') + '</div>' : '<div class="empty">Nothing found.</div>'); scaleTiles(view); }); });
  window.addEventListener('hashchange', render);

  // ---------- boot ----------
  var tv = false; try { tv = !!localStorage.getItem('gameshop.tv'); } catch (e) { }
  if (/PlayStation/i.test(navigator.userAgent)) tv = true;
  loadState(true).then(function () { setTV(tv); render(); refreshJobs(); });
  setInterval(function () { refreshJobs(); }, 2500);
  setInterval(function () { loadState(); }, 20000);
})();
