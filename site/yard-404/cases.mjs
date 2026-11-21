export const SHARED = [
  ['/', null], ['', null], ['/blog', null], ['/random/thing', null], ['/wp-admin/install.php', null], ['/.env', null],
  ['/favicon-old.png', null], ['/img/screenshot.png', null], ['/<script>alert(1)</script>', null], ['/%E0%A4%A', null],
  ['/日本語', null], ['/🙂', null], ['/' + 'a'.repeat(5000), null], ['/caf%C3%A9', null], ['/up', null],
];

export const CASES = {
  stackyard: [
    ['/docs/instal-docker', '/docs/installation/docker/'],
    ['/docs/isntall-docker', '/docs/installation/docker/'],
    ['/DOCS/Installation/Docker/', '/docs/installation/docker/'],
    ['/docs/installation/docker', '/docs/installation/docker/'],
    ['/docs/installation/docker/index.html', '/docs/installation/docker/'],
    ['/docs/installation/docker.html', '/docs/installation/docker/'],
    ['/docs/installation/docker/)', '/docs/installation/docker/'],
    ['/docs/installation/docker/%29', '/docs/installation/docker/'],
    ['/docs/installation/docker.', '/docs/installation/docker/'],
    ['/docs/installation/docker/?ref=reddit', '/docs/installation/docker/'],
    ['/docs//widgets//weather', '/docs/widgets/weather/'],
    ['/docs/widgets/wether', '/docs/widgets/weather/'],
    ['/docs/widgets/plex', '/docs/widgets/now-playing/'],
    ['/docs/plex', '/docs/widgets/now-playing/'],
    ['/docs/widgets/unknown-thing', '/docs/widgets/'],
    ['/docs/widgets/weather/extra/deep', '/docs/widgets/weather/'],
    ['/docs/backups', '/docs/import-export/backup-and-restore/'],
    ['/docs/reverse-proxy-setup', '/docs/reverse-proxy/'],
    ['/docs/nginx', '/docs/reverse-proxy/'],
    ['/docs/settings', '/docs/settings-reference/'],
    ['/docs/widget', '/docs/widgets/'],
    ['/docs/dns', '/docs/widgets/dns/'],
    ['/en/docs/first-setup', '/docs/first-setup/'],
    ['/docs/first_setup', '/docs/first-setup/'],
    ['/docs/first%20setup', '/docs/first-setup/'],
    ['/docs/first+setup', '/docs/first-setup/'],
    ['/docs/getting-started', '/docs/first-setup/'],
    ['/docs/homarr', '/docs/is-stackyard-for-you/'],
    ['/docs/caf%C3%A9', null],
    ['/docs/up', null],
    ['/docs/dsn', null],
    ['/docs/img/screenshot.png', null],
  ],
  posteryard: [
    ['/getting-start', '/#getting-started'], ['/setings', '/#settings'], ['/Features', '/#features'], ['/install', '/#getting-started'], ['/pricing', null],
  ],
  tellyard: [
    ['/gide', '/#guide'], ['/epg', '/#guide'], ['/why', '/#features'], ['/channels.html', '/#guide'], ['/install', '/#getting-started'], ['/pricing', null],
  ],
  backyard: [
    ['/stackyrd', 'https://stackyard.sandobserver.com/'], ['/posteryard', 'https://posteryard.sandobserver.com/'], ['/tellyard', 'https://tellyard.sandobserver.com/'], ['/about', null],
  ],
};

export const DISPLAY = [
  ['/%E2%80%AEexe.tsetal-dracyretsop', '/exe.tsetal-dracyretsop'],
  ['/a%E2%80%8Eb%E2%80%8Fc%E2%80%AAd%E2%80%ABe%E2%80%ACf%E2%80%ADg%E2%81%A6h%E2%81%A7i%E2%81%A8j%E2%81%A9k%D8%9Cl', '/abcdefghijkl'],
  ['/' + 'a'.repeat(78) + '%F0%9F%99%82'.repeat(60), '/' + 'a'.repeat(78) + '🙂' + '…' + '🙂'.repeat(30)],
];
