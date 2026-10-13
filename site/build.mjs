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

const WINDOW = 120;
const STATIC_START = 20 * 60;
const STATIC_NOW = STATIC_START + 40;
const channels = [
  { number: '101', name: 'Kestrel News', mark: 'KESTREL', sub: 'NEWS', tint: '#2f6fd6',
    day: '0:00 Overnight Desk|2:00 World Desk|3:30 Overnight Desk|5:00 Early Edition|6:00 Kestrel Morning|9:00 Market Watch|10:30 Midday Desk|13:00 Kestrel Live|15:00 Afternoon Report|17:00 First at Five|18:00 Evening Report|19:30 Kestrel Tonight|21:00 The Late Brief|22:30 Night Desk' },
  { number: '102', name: 'Brightwater Weather', mark: 'BRIGHT', sub: 'WATER', tint: '#1aa0a8',
    day: Array.from({ length: 12 }, (_, i) => `${i * 2}:00 Brightwater Weather`).join('|') },
  { number: '201', name: 'Lumen Music', mark: 'lumen', sub: 'MUSIC', tint: '#b14ad6',
    day: '0:25 Night Mix|2:15 Ambient Hours|5:40 Wake-Up Set|8:20 Morning Rotation|11:05 Lumen Sessions|12:50 Deep Cuts|14:35 Live Session|15:25 Afternoon Rotation|17:45 Encore|18:30 Drive Time|20:10 Live Session|21:40 Encore|22:20 Late Set' },
  { number: '202', name: 'Ridgeline Sports', mark: 'RIDGE', sub: 'SPORTS', tint: '#d6702f',
    day: '1:15 Classic Matches|4:30 Overnight Replay|6:00 Morning Kickoff|8:00 Ridgeline Daily|9:15 Training Ground|10:45 Highlights|11:35 Cup Preview|12:55 Matchday Live|15:05 Post-Match|16:00 Ridgeline Daily|17:20 Highlights|18:05 Matchday Live|20:20 Post-Match|21:10 Night Game|23:40 Highlights' },
  { number: '301', name: 'Atlas Docs', mark: 'ATLAS', sub: 'DOCS', tint: '#4f9b4a',
    day: '0:40 Night Owl|3:40 Deep Water|4:35 The Long Road|5:50 Wild Coast|6:45 Atlas Mornings|8:25 Deep Water|9:20 The Long Road|10:35 Built to Last|11:50 Wild Coast|12:45 Northern Lines|14:10 Deep Water|15:05 The Long Road|16:20 Built to Last|17:35 Wild Coast|18:30 Northern Lines|19:55 Deep Water|20:50 The Long Road|22:05 Built to Last|23:20 Northern Lines' },
].map(({ day, ...channel }) => {
  const slots = day.split('|').map((entry) => {
    const [, h, m, title] = entry.match(/^(\d{1,2}):(\d{2}) (.+)$/) ?? fail(`guide slot "${entry}" is not "H:MM Title"`);
    return [Number(h) * 60 + Number(m), title];
  });
  if (slots.some(([minute], i) => i > 0 && minute <= slots[i - 1][0])) fail(`guide day of ${channel.name} is not in order`);
  return { ...channel, slots };
});

function shows(channel, from, to) {
  const starts = [-1, 0, 1].flatMap((day) => channel.slots.map(([minute, title]) => [day * 1440 + minute, title]));
  return starts
    .slice(0, -1)
    .map(([start, title], i) => ({ title, start, end: starts[i + 1][0] }))
    .filter(({ start, end }) => end > from && start < to);
}

function clock(minute) {
  const h = Math.floor(minute / 60) % 24;
  return `${h % 12 || 12}:${String(minute % 60).padStart(2, '0')} ${h < 12 ? 'AM' : 'PM'}`;
}

function guideRows() {
  return channels
    .map((channel, row) => {
      const cells = shows(channel, STATIC_START, STATIC_START + WINDOW)
        .map(({ title, start, end }) => {
          const a = Math.max(start, STATIC_START);
          const z = Math.min(end, STATIC_START + WINDOW);
          const live = start <= STATIC_NOW && STATIC_NOW < end;
          const classes = ['cell', live ? 'on' : '', live && row === 0 ? 'focus' : ''].filter(Boolean).join(' ');
          const style = `left: ${((a - STATIC_START) / WINDOW) * 100}%; width: ${((z - a) / WINDOW) * 100}%`;
          return `<div class="${classes}" style="${style}"><span class="t">${escape(title)}</span>${live ? '<span class="live">LIVE</span>' : ''}</div>`;
        })
        .join('');
      return (
        `<div class="ch"><span class="num">${channel.number}</span><span class="logo" style="--tint: ${channel.tint}"><b>${escape(channel.mark)}</b><i>${escape(channel.sub)}</i></span></div>` +
        `<div class="row">${cells}</div>`
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
  guide_ruler: [0, 30, 60, 90].map((m) => `<span style="left: ${(m / WINDOW) * 100}%">${clock(STATIC_START + m)}</span>`).join(''),
  guide_data: JSON.stringify(channels.map(({ number, slots }) => ({ number, slots }))).replaceAll('<', '\\u003c'),
  now_left: String(((STATIC_NOW - STATIC_START) / WINDOW) * 100),
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
