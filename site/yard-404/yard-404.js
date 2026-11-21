(function () {
  var MAX_INPUT = 300, MAX_TOKENS = 12, MAX_WORD = 24, THRESHOLD = 0.62;
  var STOP = new Set(['docs', 'doc', 'the', 'and', 'a', 'an', 'of', 'to', 'for', 'with', 'index', 'html', 'htm', 'php', 'md', 'www', 'page', 'en', 'com', 'sandobserver']);
  var ASSET = /\.(png|jpe?g|gif|webp|avif|svg|ico|css|js|mjs|map|json|xml|txt|woff2?|ttf|pdf|zip|mp4|webm)$/i;

  function decode(s) {
    return s.split('/').map(function (seg) { try { return decodeURIComponent(seg); } catch (e) { return seg; } }).join('/');
  }

  function displayPath(raw) {
    var p = decode(String(raw || '').split(/[?#]/)[0]);
    if (p.charAt(0) !== '/') p = '/' + p;
    return p.length > 120 ? p.slice(0, 80) + '…' + p.slice(-30) : p;
  }

  function normalize(raw) {
    var p = decode(String(raw || '').slice(0, MAX_INPUT).split(/[?#]/)[0]).toLowerCase();
    p = p.replace(/[)\]}>.,;:!?'"`*\s]+$/u, '');
    p = p.replace(/\/index\.(html?|php|md)$/, '/').replace(/\.(html?|php|md)$/, '');
    p = p.replace(/\/{2,}/g, '/');
    if (p.charAt(0) !== '/') p = '/' + p;
    if (p.charAt(p.length - 1) !== '/') p += '/';
    return p;
  }

  function stem(t) { return t.length > 4 && /[^s]s$/.test(t) ? t.slice(0, -1) : t; }

  function tokens(s) {
    return s.normalize('NFKD').replace(/[̀-ͯ]/g, '').toLowerCase().split(/[^a-z0-9]+/)
      .filter(function (t) { return t.length > 1 && t.length <= MAX_WORD && !STOP.has(t); })
      .map(stem).slice(0, MAX_TOKENS);
  }

  var R0 = new Uint16Array(64), R1 = new Uint16Array(64), R2 = new Uint16Array(64);
  function osa(a, b) {
    var n = b.length, p2 = R0, p1 = R1, cur = R2, i, j, t;
    for (j = 0; j <= n; j++) p1[j] = j;
    for (i = 1; i <= a.length; i++) {
      cur[0] = i;
      for (j = 1; j <= n; j++) {
        var v = Math.min(p1[j] + 1, cur[j - 1] + 1, p1[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
        if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) v = Math.min(v, p2[j - 2] + 1);
        cur[j] = v;
      }
      t = p2; p2 = p1; p1 = cur; cur = t;
    }
    return p1[n];
  }

  function sim(w, t) {
    if (w === t) return 1;
    var short = Math.min(w.length, t.length), long = Math.max(w.length, t.length);
    if (short < 4) return 0;
    if (t.indexOf(w) === 0 || w.indexOf(t) === 0) return 0.85 + 0.1 * short / long;
    if ((long - short) / long > 0.4) return 0;
    var s = 1 - osa(w, t) / long;
    return s >= 0.6 ? s : 0;
  }

  function pathOf(href) {
    var m = /^https?:\/\/[^/]+(\/[^?#]*)?/.exec(href);
    return m ? (m[1] || '/') : href.split(/[?#]/)[0];
  }

  function prepare(pages) {
    return pages.map(function (page) {
      var words = tokens(page[1].replace(/^https?:\/\/[^/]+/, '')).concat(tokens(page[0]), tokens(page[2] || ''));
      return { page: page, words: Array.from(new Set(words)), kw: new Set(tokens(page[2] || '')), len: pathOf(page[1]).length };
    });
  }

  function suggest(raw, pages, prepared) {
    var norm = normalize(raw);
    if (norm === '/' || ASSET.test(norm.replace(/\/$/, ''))) return null;
    var own = pages.filter(function (p) { return !/^https?:/.test(p[1]); });

    var exact = own.find(function (p) { return pathOf(p[1]) !== '/' && normalize(pathOf(p[1])) === norm; });
    if (exact) return { page: exact, how: 'exact' };

    var segs = norm.split('/').filter(Boolean), q = [];
    segs.forEach(function (seg, i) { tokens(seg).forEach(function (t) { q.push([t, i === segs.length - 1 ? 2 : 1]); }); });
    var best = null;
    if (q.some(function (x) { return x[0].length >= 3; })) {
      var wsum = q.reduce(function (a, x) { return a + x[1]; }, 0);
      (prepared || prepare(pages)).forEach(function (c) {
        if (!c.words.length) return;
        var s = 0, strong = false, chosen = false, hit = new Set();
        q.forEach(function (x) {
          var m = 0;
          c.words.forEach(function (w) { var v = sim(x[0], w); if (v > m) m = v; if (v >= 0.8) hit.add(w); });
          s += m * x[1];
          if (m >= 0.8 && x[0].length >= 3) strong = true;
          if (c.kw.has(x[0])) chosen = true;
        });
        s /= wsum;
        if (!strong) return;
        var score = s + (chosen ? 0.06 : 0) + 0.04 * hit.size / c.words.length - 0.001 * c.len;
        if (!best || score > best.score) best = { page: c.page, score: score, s: s };
      });
    }
    if (best && best.s >= THRESHOLD) return { page: best.page, how: 'match' };

    var parent = own.map(function (p) { return [p, normalize(pathOf(p[1]))]; })
      .filter(function (x) { return x[1] !== '/' && x[1] !== '/docs/' && norm.indexOf(x[1]) === 0; })
      .sort(function (a, b) { return b[1].length - a[1].length; })[0];
    return parent ? { page: parent[0], how: 'parent' } : null;
  }

  var api = { suggest: suggest, normalize: normalize, displayPath: displayPath, prepare: prepare };
  if (typeof document === 'undefined') { globalThis.Yard404 = api; return; }

  var data = document.getElementById('y4-pages');
  var pathEl = document.getElementById('y4-path');
  var go = document.getElementById('y4-go');
  var home = document.getElementById('y4-home');
  if (!data || !pathEl || !go || !home) return;
  var pages = JSON.parse(data.textContent).pages || [];

  var shown = displayPath(location.pathname);
  pathEl.textContent = '';
  shown.split('/').forEach(function (seg, i) {
    if (i > 0) { pathEl.appendChild(document.createTextNode('/')); pathEl.appendChild(document.createElement('wbr')); }
    pathEl.appendChild(document.createTextNode(seg.replace(/-/g, '-⁠')));
  });
  pathEl.classList.remove('y4-none');

  var hit = suggest(location.pathname, pages);
  if (hit) {
    var li = document.createElement('li'), a = document.createElement('a');
    a.className = 'y4-best';
    a.href = hit.page[1];
    a.textContent = hit.page[0];
    li.appendChild(a);
    go.insertBefore(li, go.firstChild);
    home.classList.remove('y4-best');
  }

  try {
    if (document.referrer && new URL(document.referrer).origin === location.origin && history.length > 1) {
      var bli = document.createElement('li'), b = document.createElement('button');
      b.type = 'button';
      b.textContent = 'Back';
      b.addEventListener('click', function () { history.back(); });
      bli.appendChild(b);
      go.appendChild(bli);
    }
  } catch (e) { return; }
})();
