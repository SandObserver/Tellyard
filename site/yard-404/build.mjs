import { writeFileSync, readdirSync, unlinkSync } from 'node:fs';
import { join } from 'node:path';
import { render } from './render.mjs';

export function writeYard404(id, out, pages = null) {
  const { html, files } = render(id, pages);
  const keep = new Set(files.map((f) => f.path));
  for (const f of readdirSync(out)) if (/^yard-404-[0-9a-f]{8}\.(css|js)$/.test(f) && !keep.has(f)) unlinkSync(join(out, f));
  writeFileSync(join(out, '404.html'), html);
  for (const f of files) writeFileSync(join(out, f.path), f.content);
}

const [id, out] = process.argv.slice(2);
if (id && out) {
  writeYard404(id, out);
  console.log(`yard-404 ${id}: wrote ${out}/404.html`);
}
