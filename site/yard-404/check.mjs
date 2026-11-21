import { readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { render, assets } from './render.mjs';
import { CASES, DISPLAY, SHARED } from './cases.mjs';

export function checkYard404(id, dist) {
  const problems = [];
  const fail = (m) => problems.push(m);
  const file = join(dist, '404.html');
  if (!existsSync(file)) return [`${file} is missing`];
  const html = readFileSync(file, 'utf8');

  const data = /<script type="application\/json" id="y4-pages">([\s\S]*?)<\/script>/.exec(html);
  const year = /© (\d{4}) SandObserver/.exec(html);
  if (!data || !year) return ['404.html was not made by yard-404 render()'];
  const pages = JSON.parse(data[1]).pages;

  const expected = render(id, pages, Number(year[1]));
  if (html !== expected.html) fail('404.html differs from render() output. Do not edit the markup, CSS or JS by hand.');
  for (const a of assets()) {
    const f = join(dist, a.path);
    if (!existsSync(f)) fail(`/${a.path} is not in the build output`);
    else if (readFileSync(f, 'utf8') !== a.content) fail(`/${a.path} differs from the kit`);
  }

  if (!/<meta name="robots" content="noindex">/.test(html)) fail('missing noindex');
  for (const [re, what] of [[/rel="canonical"/, 'canonical'], [/og:url/, 'og:url'], [/application\/ld\+json/, 'structured data'], [/<style/, 'inline <style>'], [/ style="/, 'style attribute'], [/<script>/, 'inline script']]) {
    if (re.test(html)) fail(`404.html must not contain ${what}`);
  }
  for (const m of html.matchAll(/(?:href|src)="([^"]+)"/g)) {
    const u = m[1];
    if (!/^(\/|#|https:\/\/)/.test(u)) fail(`relative link breaks at depth: ${u}`);
  }

  for (const [label, href] of pages) {
    if (/^https:/.test(href)) continue;
    const [path, hash] = href.split('#');
    const target = path === '/' || path === '' ? join(dist, 'index.html') : join(dist, path, 'index.html');
    if (!existsSync(target)) fail(`suggested page does not exist: ${label} ${href}`);
    else if (hash && !new RegExp(`id="${hash}"`).test(readFileSync(target, 'utf8'))) fail(`suggested anchor does not exist: ${label} ${href}`);
  }

  new Function(readFileSync(new URL('./yard-404.js', import.meta.url), 'utf8'))();
  const { suggest, displayPath } = globalThis.Yard404;
  for (const [input, want] of DISPLAY) {
    const got = displayPath(input);
    if (got !== want) fail(`display case ${JSON.stringify(input.slice(0, 40))}: got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
  }
  for (const [input, want] of [...SHARED, ...(CASES[id] || [])]) {
    let got;
    try { const r = suggest(input, pages); got = r ? r.page[1] : null; } catch (e) { fail(`case ${input.slice(0, 40)} threw ${e.message}`); continue; }
    if (got !== want) fail(`case ${JSON.stringify(input.slice(0, 40))}: got ${got}, want ${want}`);
  }
  return problems;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const [id, dist] = process.argv.slice(2);
  const problems = checkYard404(id, dist);
  if (problems.length) { console.error(`yard-404 ${id}: ${problems.length} problem(s)\n- ${problems.join('\n- ')}`); process.exit(1); }
  console.log(`yard-404 ${id}: ok`);
}
