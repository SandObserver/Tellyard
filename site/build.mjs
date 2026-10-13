import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { cpSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Marked } from 'marked';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const dist = join(here, 'dist');
const site = 'https://tellyard.sandobserver.com';
const repo = 'https://github.com/SandObserver/Tellyard';

const readme = readFileSync(join(root, 'README.md'), 'utf8');
const changelog = readFileSync(join(root, 'CHANGELOG.md'), 'utf8');

function fail(message) {
  console.error(`site: ${message}`);
  process.exit(1);
}

function section(heading) {
  const match = readme.match(new RegExp(`^#{2,3} ${heading}\\n([\\s\\S]*?)(?=^#{2,3} |(?![\\s\\S]))`, 'm'));
  if (!match) fail(`README.md has no "${heading}" section`);
  return match[1].trim();
}

function escape(text) {
  return text.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c]);
}

const marked = new Marked({ gfm: true });

function codeBlock(heading, lang) {
  const block = marked.lexer(section(heading)).find((token) => token.type === 'code' && token.lang === lang);
  if (!block) fail(`README.md "${heading}" has no ${lang} code block`);
  return block.text;
}

const plain = (html) =>
  html.replace(/<[^>]+>/g, '').replace(/&(amp|lt|gt|quot);/g, (_, e) => ({ amp: '&', lt: '<', gt: '>', quot: '"' })[e]);

function highlight(code) {
  return code
    .split('\n')
    .map((line) => {
      const safe = escape(line);
      return line.trimStart().startsWith('#') ? `<span class="c">${safe}</span>` : safe;
    })
    .join('\n');
}

const tagline = readme.match(/<b>(.+?)<\/b>/)?.[1] ?? fail('README.md has no bold tagline');
const version =
  changelog.match(/^## \[(\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?)\] - \d{4}-\d{2}-\d{2}$/m)?.[1] ??
  fail('CHANGELOG.md has no released version');

const description =
  'A small live TV tuner for Plex and Jellyfin. Watch free broadcaster streams in the guide, ' +
  'from one container and one file. Self-hosted in Docker.';

const card = readFileSync(join(root, 'docs', 'social-card.png'));
const socialCard = `${site}/img/social-card.png?v=${createHash('sha256').update(card).digest('hex').slice(0, 8)}`;

const template = readFileSync(join(here, 'template.html'), 'utf8');
const features = [...template.matchAll(/<span class="saq">(.+?)<\/span> <span class="saa">(.+?)<\/span><\/h3><p>(.+?)<\/p>/g)].map(
  ([, q, a, text]) => `${plain(q)} ${plain(a)} ${plain(text)}`,
);
if (features.length === 0) fail('template.html has no feature cards');

const schema = {
  '@context': 'https://schema.org',
  '@graph': [
    { '@type': 'WebSite', name: 'Tellyard', url: `${site}/` },
    {
      '@type': 'SoftwareApplication',
      name: 'Tellyard',
      description,
      url: `${site}/`,
      image: socialCard,
      applicationCategory: 'MultimediaApplication',
      operatingSystem: 'Docker',
      softwareVersion: version,
      featureList: features,
      isAccessibleForFree: true,
      license: 'https://www.apache.org/licenses/LICENSE-2.0',
      downloadUrl: `${repo}/pkgs/container/tellyard`,
      softwareHelp: { '@type': 'CreativeWork', url: `${repo}/tree/main/docs` },
      offers: { '@type': 'Offer', price: '0', priceCurrency: 'USD' },
      sameAs: [repo],
      author: { '@type': 'Person', name: 'SandObserver', url: 'https://github.com/SandObserver' },
    },
  ],
};

function lastChange() {
  const git = (...args) => execFileSync('git', args, { cwd: root, encoding: 'utf8', timeout: 60_000 }).trim();
  try {
    if (git('rev-parse', '--is-shallow-repository') !== 'false') git('fetch', '--quiet', '--unshallow');
    if (git('rev-parse', '--is-shallow-repository') !== 'false') return '';
    return git('log', '-1', '--format=%cs', '--', 'site', 'README.md', 'CHANGELOG.md', 'docs');
  } catch {
    return '';
  }
}

const GUIDE_MINUTES = 120;
const NOW = 40;
const channels = [
  { number: '101', name: 'Kestrel News', mark: 'KESTREL', sub: 'NEWS', tint: '#2f6fd6', shows: [['Evening Report', 60], ['Kestrel Tonight', 60]] },
  { number: '102', name: 'Brightwater Weather', mark: 'BRIGHT', sub: 'WATER', tint: '#1aa0a8', shows: [['Brightwater Weather', 120]] },
  { number: '201', name: 'Lumen Music', mark: 'lumen', sub: 'MUSIC', tint: '#b14ad6', shows: [['Live Session', 30], ['Encore', 30], ['Night Mix', 60]] },
  { number: '202', name: 'Ridgeline Sports', mark: 'RIDGE', sub: 'SPORTS', tint: '#d6702f', shows: [['Matchday Live', 90], ['Highlights', 30]] },
  { number: '301', name: 'Atlas Docs', mark: 'ATLAS', sub: 'DOCS', tint: '#4f9b4a', shows: [['Deep Water', 60], ['The Long Road', 60]] },
];

function guideRows() {
  for (const channel of channels) {
    const total = channel.shows.reduce((sum, [, minutes]) => sum + minutes, 0);
    if (total !== GUIDE_MINUTES) fail(`guide row ${channel.name} spans ${total} minutes, not ${GUIDE_MINUTES}`);
  }
  return channels
    .map((channel, row) => {
      let start = 0;
      const cells = channel.shows
        .map(([title, minutes]) => {
          const live = start <= NOW && NOW < start + minutes;
          const focus = row === 0 && live;
          const classes = ['cell', live ? 'on' : '', focus ? 'focus' : ''].filter(Boolean).join(' ');
          const html =
            `<div class="${classes}" style="grid-column: span ${minutes / 10}">` +
            `<span class="t">${escape(title)}</span>${live ? '<span class="live">LIVE</span>' : ''}</div>`;
          start += minutes;
          return html;
        })
        .join('');
      return (
        `<div class="ch"><span class="logo" style="--tint: ${channel.tint}"><b>${escape(channel.mark)}</b><i>${escape(channel.sub)}</i></span>` +
        `<span class="num">${channel.number}</span></div><div class="row">${cells}</div>`
      );
    })
    .join('');
}

const lastmod = lastChange();

const values = {
  site,
  repo,
  tagline,
  description,
  social_card: socialCard,
  version,
  year: String(new Date().getFullYear()),
  schema: JSON.stringify(schema).replaceAll('<', '\\u003c'),
  guide_rows: guideRows(),
  now_left: String((NOW / GUIDE_MINUTES) * 100),
  channels_code: highlight(codeBlock('Channels', 'toml')),
  compose_code: highlight(codeBlock('2\\. Create the compose file', 'yaml')),
  start_code: highlight(codeBlock('3\\. Start it', 'sh')),
  check_code: highlight(codeBlock('4\\. Check your channels', 'sh')),
};

function fill(file) {
  return readFileSync(join(here, file), 'utf8').replace(/\{\{(\w+)\}\}/g, (_, key) => {
    if (!(key in values)) fail(`${file} uses an unknown value {{${key}}}`);
    return values[key];
  });
}

rmSync(dist, { recursive: true, force: true });
mkdirSync(join(dist, 'img'), { recursive: true });
cpSync(join(here, 'public'), dist, { recursive: true });
cpSync(join(root, 'docs', 'logo.svg'), join(dist, 'logo.svg'));
cpSync(join(root, 'docs', 'social-card.png'), join(dist, 'img', 'social-card.png'));
const index = fill('template.html');
const scripts = [...index.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(
  ([, code]) => `'sha256-${createHash('sha256').update(code).digest('base64')}'`,
);
const csp =
  `default-src 'none'; script-src ${scripts.join(' ') || "'none'"}; style-src 'unsafe-inline'; img-src 'self'; ` +
  "manifest-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'";
const headers = join(dist, '_headers');
writeFileSync(headers, readFileSync(headers, 'utf8').replace(/^\/\*\n/m, `/*\n  Content-Security-Policy: ${csp}\n`));
writeFileSync(join(dist, 'index.html'), index);
writeFileSync(join(dist, '404.html'), fill('404.html'));
writeFileSync(join(dist, 'robots.txt'), `User-agent: *\nAllow: /\n\nSitemap: ${site}/sitemap.xml\n`);
writeFileSync(
  join(dist, 'sitemap.xml'),
  `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
    `  <url><loc>${site}/</loc>${lastmod ? `<lastmod>${lastmod}</lastmod>` : ''}</url>\n</urlset>\n`,
);
console.log(`site: built Tellyard ${version} into dist/`);
