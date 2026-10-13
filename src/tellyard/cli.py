import argparse
import functools
import json
import logging
import os
import signal
import sys
import threading
import urllib.error
import urllib.request
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import FrameType

from tellyard import __version__, channels, check
from tellyard.channels import ChannelError
from tellyard.guide import Guide, build
from tellyard.resolve import Resolver
from tellyard.server import App, Server
from tellyard.settings import Settings, SettingsError
from tellyard.store import ChannelStore

log = logging.getLogger("tellyard")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tellyard", description="Live channels for Plex, tuned only when watched.")
    parser.add_argument("--version", action="version", version=f"tellyard {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="run the tuner")
    commands.add_parser("validate", help="read channels.toml and list the channels")
    probe = commands.add_parser("check", help="tune each channel and report the resolution it really sends")
    probe.add_argument("numbers", nargs="*", metavar="NUMBER", help="only these channel numbers")
    guide = commands.add_parser("guide", help="build the guide once and write it to a file or stdout")
    guide.add_argument("-o", "--output", type=Path, help="file to write instead of stdout")
    commands.add_parser("health", help="exit 0 when the running service answers")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = Settings.from_env(os.environ)
    except SettingsError as error:
        print(f"tellyard: {error}", file=sys.stderr)
        return 2
    logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=settings.log_level.upper())
    if args.command == "health":
        return _health(settings)
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
        measure = functools.partial(check.probe_height, settings.ffprobe)
        results = check.check(chosen, Resolver(settings), measure, settings.min_height)
        print(check.report(results))
        return 0 if all(result.ok for result in results) else 1
    if args.command == "guide":
        data = build(lineup, datetime.now(UTC), settings.guide_days)
        if args.output:
            args.output.write_bytes(data)
        else:
            sys.stdout.buffer.write(data)
        return 0
    return _serve(settings)


def _serve(settings: Settings) -> int:
    store = ChannelStore(settings.channels_file)
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
    threading.Thread(target=app.guide_loop, args=(stop,), name="guide", daemon=True).start()
    count = len(store.current().channels)
    log.info(
        "tellyard %s serving %d channels on port %d with %d tuners", __version__, count, settings.port, settings.tuners
    )
    with server:
        server.serve_forever()
    return 0


def _health(settings: Settings) -> int:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{settings.port}/health", timeout=5) as response:
            status = json.load(response).get("status")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        print(f"tellyard: unhealthy: {error}", file=sys.stderr)
        return 1
    return 0 if status == "ok" else 1
