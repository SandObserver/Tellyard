import { readFileSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { SITES } from './sites.mjs';

const read = (f) => readFileSync(new URL(f, import.meta.url), 'utf8');
const hashed = (base, ext, content) => `${base}-${createHash('sha256').update(content).digest('hex').slice(0, 8)}.${ext}`;
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);

export const KIT_VERSION = '1';

export function assets() {
  const css = read('./yard-404.css');
  const js = read('./yard-404.js');
  return [
    { path: hashed('yard-404', 'css', css), content: css },
    { path: hashed('yard-404', 'js', js), content: js },
  ];
}

export function render(id, pages = null, year = new Date().getFullYear()) {
  const site = SITES[id];
  if (!site) throw new Error(`yard-404: unknown site "${id}"`);
  const list = pages ?? site.pages;
  if (!Array.isArray(list) || !list.length) throw new Error(`yard-404: no pages for "${id}"`);
  const [css, js] = assets();
  const icons = site.icons.map(([rel, href, type, sizes]) =>
    `<link rel="${rel}" href="${esc(href)}"${type ? ` type="${type}"` : ''}${sizes ? ` sizes="${sizes}"` : ''}>`).join('\n');
  const pills = site.pills.map(([label, href, kind]) => `<a class="y4-pill y4-${kind}" href="${esc(href)}">${esc(label)}</a>`).join('');
  const data = JSON.stringify({ v: KIT_VERSION, pages: list.map(([l, h, k]) => (k ? [l, h, k] : [l, h])) }).replace(/</g, '\\u003c');
  const html = `<!doctype html>
<html lang="en" data-y4="${id}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Page not found · ${esc(site.name)}</title>
<meta name="robots" content="noindex">
<meta name="color-scheme" content="dark">
<meta name="theme-color" content="#000000">
${icons}
<link rel="stylesheet" href="/${css.path}">
<script src="/${js.path}" defer></script>
</head>
<body>
<a class="y4-skip" href="#y4-main">Skip to content</a>
<div class="y4-page">
<header class="y4-nav y4-wrap"><a class="y4-brand" href="/"><span class="y4-mk">${site.mark}</span>${esc(site.name)}</a><nav class="y4-pills" aria-label="Site">${pills}</nav></header>
<main class="y4-main" id="y4-main" tabindex="-1"><div class="y4-nf y4-wrap">
<p class="y4-eyebrow">Error 404</p>
<h1 class="y4-h">Nothing at <span class="y4-path y4-none" id="y4-path">this address</span></h1>
<ul class="y4-go" id="y4-go" aria-label="Where to go"><li><a class="y4-best" id="y4-home" href="/">Home</a></li></ul>
</div></main>
<footer class="y4-foot"><div class="y4-wrap"><span>© ${year} SandObserver</span><a href="${esc(site.repo)}">GitHub</a></div></footer>
</div>
<script type="application/json" id="y4-pages">${data}</script>
</body>
</html>
`;
  return { html, files: [css, js] };
}
