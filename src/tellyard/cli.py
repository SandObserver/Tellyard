import argparse
import functools
import logging
import os
import platform
import signal
import sys
import threading
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType

from tellyard import __version__, channels, check, health, logfmt
from tellyard.channels import ChannelError, Lineup
from tellyard.guide import Guide, build
from tellyard.relay import Relay
from tellyard.resolve import Resolver
from tellyard.server import App, Server
from tellyard.settings import Settings, SettingsError
from tellyard.store import ChannelStore

log = logging.getLogger("tellyard")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tellyard", description="Live channels for Plex and Jellyfin, tuned only when watched."
    )
    parser.add_argument("--version", action="version", version=f"tellyard {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="run the tuner")
    commands.add_parser("validate", help="read channels.toml and list the channels")
    probe = commands.add_parser("check", help="tune each channel and report the resolution it really sends")
    probe.add_argument("numbers", nargs="*", metavar="NUMBER", help="only these channel numbers")
    guide = commands.add_parser("guide", help="build the guide once and write it to a file or stdout")
    guide.add_argument("-o", "--output", type=Path, help="file to write instead of stdout")
    commands.add_parser("health", help="exit 0 when the running service is healthy")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = Settings.from_env(os.environ)
    except SettingsError as error:
        print(f"tellyard: {error}", file=sys.stderr)
        return 2
    logfmt.setup(settings.log_level)
    if args.command == "health":
        return health.probe(settings.port)
    if args.command == "serve":
        return _serve(settings)
    try:
        lineup = channels.load(settings.channels_file)
    except ChannelError as error:
        print(f"tellyard: {error}", file=sys.stderr)
        return 2
    if args.command == "validate":
        for channel in lineup.channels:
            guide = f"xmltv {channel.xmltv.channel_id}" if channel.xmltv else "placeholder"
            print(f"{channel.number:>7}  {channel.name}  ({type(channel.source).__name__}, guide: {guide})")
        print(f"{len(lineup.channels)} channels are valid")
        return 0
    if args.command == "check":
        chosen = [c for c in lineup.channels if not args.numbers or c.number in args.numbers]
        if not chosen:
            print("tellyard: no channels match those numbers", file=sys.stderr)
            return 2
        relay = Relay()
        try:
            measure = functools.partial(check.probe_height, settings.ffmpeg, relay)
            results = check.check(chosen, Resolver(settings), measure, settings.min_height)
        finally:
            relay.close()
        print(check.report(results))
        return 0 if all(result.ok for result in results) else 1
    data = build(lineup, datetime.now(UTC), settings.guide_days).data
    if args.output:
        args.output.write_bytes(data)
    else:
        sys.stdout.buffer.write(data)
    return 0


def _serve(settings: Settings) -> int:
    try:
        store = ChannelStore(settings.channels_file)
    except ChannelError as error:
        print(f"tellyard: {error}", file=sys.stderr)
        return 2
    guide = Guide(settings.data_dir / "guide.xml", settings.guide_days)
    app = App(settings, store, guide)
    server = Server(("", settings.port), app)
    stop = threading.Event()

    def shutdown(signum: int, frame: FrameType | None) -> None:
        log.info("stopping")
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    print(_banner(settings, store.current()), file=sys.stderr, flush=True)
    app.start(stop)
    log.info("service ready", extra={"version": __version__, "port": settings.port})
    try:
        with server:
            server.serve_forever()
    finally:
        app.close()
    return 0


def _banner(settings: Settings, lineup: Lineup) -> str:
    own = sum(1 for channel in lineup.channels if channel.min_height is not None)
    quality = f"check needs {settings.min_height}p" if settings.min_height else "check accepts any height"
    if own:
        quality += ", 1 channel sets its own" if own == 1 else f", {own} channels set their own"
    sources = len({channel.xmltv.url for channel in lineup.channels if channel.xmltv})
    xmltv = "1 XMLTV source" if sources == 1 else f"{sources} XMLTV sources"
    rows = [
        ("Channels", f"{len(lineup.channels)} from {settings.channels_file}"),
        ("Tuners", str(settings.tuners)),
        ("Quality", quality),
        ("Guide", f"{settings.guide_days} days, rebuilt every {settings.guide_refresh_hours:g} h, {xmltv}"),
        ("Address", settings.base_url or "from each request"),
        ("Device", f"{settings.device_name}, ID {settings.device_id}"),
        ("Heartbeat", "on" if settings.heartbeat_url else "off"),
        ("Data", str(settings.data_dir)),
    ]
    return logfmt.banner(f"Tellyard {__version__} · Python {platform.python_version()}", rows)
