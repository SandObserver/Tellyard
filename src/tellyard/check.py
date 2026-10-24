import re
import subprocess
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from tellyard.channels import Channel
from tellyard.net import redact
from tellyard.relay import Relay
from tellyard.resolve import Resolved, ResolveError, Resolver, Runner
from tellyard.stream import input_options, local

PROBE_TIMEOUT = 45.0
VIDEO_SIZE = re.compile(r"^\s*Stream #\d+:\d+.*?: Video: .*?, (\d{1,5})x(\d{1,5})\b", re.MULTILINE)
OPEN_ERROR = "Error opening input"


@dataclass(frozen=True)
class Result:
    channel: Channel
    height: int | None
    problem: str | None

    @property
    def ok(self) -> bool:
        return self.problem is None


def run_probe(args: Sequence[str], timeout: float, cancel: threading.Event | None = None) -> str:
    # ffmpeg with only an input prints the stream details to stderr and exits with an error.
    try:
        done = subprocess.run(
            args,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except FileNotFoundError as error:
        raise ResolveError(f"{args[0]} is not installed") from error
    except subprocess.TimeoutExpired:
        raise ResolveError(f"{args[0]} took longer than {timeout:.0f} s") from None
    return done.stderr.decode("utf-8", errors="replace")


def probe_height(ffmpeg: str, relay: Relay, resolved: Resolved, runner: Runner = run_probe) -> int | None:
    feed = relay.open(resolved, "check")
    try:
        args = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "info", *input_options()]
        args += ["-i", local(resolved, feed).video]
        output = runner(args, PROBE_TIMEOUT, None)
    finally:
        feed.close()
    heights = [int(found.group(2)) for found in VIDEO_SIZE.finditer(output)]
    if heights:
        return max(heights)
    if OPEN_ERROR in output:
        lines = [line.strip() for line in output.splitlines() if line.strip()]
        raise ResolveError(redact(lines[-1]))
    return None


def check(
    channels: Iterable[Channel],
    resolver: Resolver,
    measure: Callable[[Resolved], int | None],
    minimum: int | None = None,
) -> list[Result]:
    results: list[Result] = []
    for channel in channels:
        try:
            resolved = resolver.resolve(channel)
            height = measure(resolved)
        except (ResolveError, ValueError) as error:
            results.append(Result(channel, None, str(error)))
            continue
        if height is None:
            results.append(Result(channel, None, "no video stream found"))
        elif (limit := channel.min_height or minimum) and height < limit:
            results.append(Result(channel, height, f"below the {limit}p minimum"))
        else:
            results.append(Result(channel, height, None))
    return results


def report(results: Sequence[Result]) -> str:
    width = max((len(result.channel.name) for result in results), default=4)
    lines = []
    for result in results:
        status = "ok" if result.ok else "FAIL"
        height = f"{result.height}p" if result.height else "-"
        problem = f"  {result.problem}" if result.problem else ""
        lines.append(f"{result.channel.number:>7}  {result.channel.name:<{width}}  {height:>6}  {status}{problem}")
    passed = sum(result.ok for result in results)
    lines.append(f"{passed} of {len(results)} channels passed")
    return "\n".join(lines)
