const STACKYARD_MARK = '<svg viewBox="146 105 772 817" aria-hidden="true" focusable="false"><path fill="#00D2E0" d="M512 105.5A406.5 406.5 0 0 1 864.04 715.25A75.5 75.5 0 0 1 733.27 639.75A255.5 255.5 0 0 0 512 256.5A75.5 75.5 0 0 1 512 105.5Z"/><path fill="#F2F2F7" d="M551.5 443.58L264.85 278.08A79 79 0 0 0 185.85 414.92L472.5 580.42A79 79 0 0 0 551.5 443.58ZM472.5 443.58L185.85 609.08A79 79 0 0 0 264.85 745.92L551.5 580.42A79 79 0 0 0 472.5 443.58ZM433 512L433 843A79 79 0 0 0 591 843L591 512A79 79 0 0 0 433 512Z"/></svg>';
const POSTERYARD_MARK = '<svg viewBox="250 200 520 640" aria-hidden="true" focusable="false"><line x1="352" y1="302" x2="352" y2="742" stroke="#F2F2F7" stroke-width="140" stroke-linecap="round"/><path d="M500 292V532L712 412Z" fill="#FFD60A" stroke="#FFD60A" stroke-width="96" stroke-linejoin="round"/></svg>';
const TELLYARD_MARK = '<svg viewBox="222 202 580 620" aria-hidden="true" focusable="false"><rect x="242" y="362" width="140" height="300" rx="70" fill="#F2F2F7"/><rect x="642" y="362" width="140" height="300" rx="70" fill="#F2F2F7"/><rect x="442" y="222" width="140" height="580" rx="70" fill="#FF4245"/></svg>';
const BACKYARD_MARK = '<svg viewBox="1 3 22 18" aria-hidden="true" focusable="false"><rect x="2" y="4" width="20" height="4" rx="2" fill="#B78A66"/><rect x="5" y="10" width="4" height="10" rx="2" fill="#F2F2F7"/><rect x="15" y="10" width="4" height="10" rx="2" fill="#F2F2F7"/></svg>';

export const SITES = {
  stackyard: {
    name: 'Stackyard',
    repo: 'https://github.com/SandObserver/stackyard',
    mark: STACKYARD_MARK,
    pills: [['Install', '/docs/installation/docker/', 'solid'], ['Documentation', '/docs/', 'ghost'], ['GitHub', 'https://github.com/SandObserver/stackyard', 'ghost']],
    icons: [['icon', '/favicon.svg', 'image/svg+xml'], ['apple-touch-icon', '/apple-touch-icon.png']],
    pages: null,
    keywords: {
      '/docs/installation/docker/': 'install setup compose',
      '/docs/first-setup/': 'getting started onboarding',
      '/docs/is-stackyard-for-you/': 'homarr homepage dashy alternatives comparison',
      '/docs/apps-and-folders/': 'services tiles',
      '/docs/customization/': 'customize theme',
      '/docs/widgets/books/': 'audiobookshelf komga kavita',
      '/docs/widgets/connections/': 'vpn gluetun map',
      '/docs/widgets/disk-health/': 'scrutiny smart nas',
      '/docs/widgets/dns/': 'pihole adguard',
      '/docs/widgets/downloads/': 'qbittorrent sabnzbd transmission',
      '/docs/widgets/git/': 'github gitea forgejo gitlab',
      '/docs/widgets/now-playing/': 'plex jellyfin emby navidrome',
      '/docs/widgets/system-summary/': 'cpu ram stats',
      '/docs/widgets/up-next/': 'sonarr radarr lidarr calendar',
      '/docs/update/': 'upgrade',
      '/docs/import-export/backup-and-restore/': 'backups export',
      '/docs/import-export/migrating/': 'migrate migration',
      '/docs/reverse-proxy/': 'nginx caddy traefik',
      '/docs/verify-image/': 'cosign signature',
      '/docs/settings-reference/': 'options config',
      '/docs/changelog/': 'releases release notes',
    },
  },
  posteryard: {
    name: 'Posteryard',
    repo: 'https://github.com/SandObserver/Posteryard',
    mark: POSTERYARD_MARK,
    pills: [['Install', '/#getting-started', 'solid'], ['GitHub', 'https://github.com/SandObserver/Posteryard', 'ghost']],
    icons: [['icon', '/favicon.ico', null, '32x32'], ['icon', '/logo.svg', 'image/svg+xml'], ['apple-touch-icon', '/apple-touch-icon.png']],
    pages: [['What it changes', '/#features', 'features before after'], ['Get started', '/#getting-started', 'install setup docker compose'], ['Settings', '/#settings', 'options config']],
  },
  tellyard: {
    name: 'Tellyard',
    repo: 'https://github.com/SandObserver/Tellyard',
    mark: TELLYARD_MARK,
    pills: [['Install', '/#getting-started', 'solid'], ['GitHub', 'https://github.com/SandObserver/Tellyard', 'ghost']],
    icons: [['icon', '/favicon.ico', null, '32x32'], ['icon', '/logo.svg', 'image/svg+xml'], ['apple-touch-icon', '/apple-touch-icon.png']],
    pages: [['Why Tellyard', '/#features', 'features about why'], ['Get started', '/#getting-started', 'install setup docker compose'], ['Live TV guide', '/#guide', 'epg channels guide']],
  },
  backyard: {
    name: 'SandObserver',
    repo: 'https://github.com/SandObserver',
    mark: BACKYARD_MARK,
    pills: [['GitHub', 'https://github.com/SandObserver', 'ghost']],
    icons: [['icon', '/favicon.svg', 'image/svg+xml'], ['icon', '/favicon.png', 'image/png', '64x64'], ['apple-touch-icon', '/apple-touch-icon.png']],
    pages: [['Stackyard', 'https://stackyard.sandobserver.com/'], ['Posteryard', 'https://posteryard.sandobserver.com/'], ['Tellyard', 'https://tellyard.sandobserver.com/']],
  },
};
