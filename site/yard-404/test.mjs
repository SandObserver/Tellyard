import { readFileSync, globSync } from 'node:fs';
import { join, relative } from 'node:path';
import { render } from './render.mjs';
import { SITES } from './sites.mjs';
import { CASES, DISPLAY, SHARED } from './cases.mjs';

new Function(readFileSync(new URL('./yard-404.js', import.meta.url), 'utf8'))();
const { suggest, displayPath } = globalThis.Yard404;
const stackyardDist = process.argv[2];
let failed = 0;
for (const [input, want] of DISPLAY) {
  const got = displayPath(input);
  if (got !== want) { failed++; console.log(`FAIL display ${JSON.stringify(input.slice(0, 40))} got ${JSON.stringify(got)} want ${JSON.stringify(want)}`); }
}
console.log(`display: ${DISPLAY.length} cases`);
for (const id of Object.keys(SITES)) {
  let pages = SITES[id].pages;
  if (id === 'stackyard') {
    if (!stackyardDist) { console.log('stackyard: skipped, pass the stackyard-docs dist directory'); continue; }
    pages = globSync(join(stackyardDist, 'docs/**/index.html')).map((f) => {
      const h1 = /<h1 id="_top">([^<]+)<\/h1>/.exec(readFileSync(f, 'utf8'));
      const href = '/' + relative(stackyardDist, f).replace(/index\.html$/, '');
      return h1 ? [h1[1], href, SITES.stackyard.keywords[href]] : null;
    }).filter(Boolean);
  }
  render(id, pages);
  const times = [];
  for (const [input, want] of [...SHARED, ...CASES[id]]) {
    const t0 = performance.now();
    const r = suggest(input, pages);
    times.push(performance.now() - t0);
    const got = r ? r.page[1] : null;
    if (got !== want) { failed++; console.log(`FAIL ${id} ${JSON.stringify(input.slice(0, 40))} got ${got} want ${want}`); }
  }
  times.sort((a, b) => a - b);
  console.log(`${id}: ${SHARED.length + CASES[id].length} cases, median ${times[times.length >> 1].toFixed(2)} ms, slowest ${times.at(-1).toFixed(2)} ms`);
}
console.log(failed ? `${failed} FAILED` : 'ALL PASS');
process.exit(failed ? 1 : 0);
