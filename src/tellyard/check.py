import json
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from tellyard.channels import Channel
from tellyard.relay import Relay
from tellyard.resolve import Resolved, ResolveError, Resolver, Runner, run_command
from tellyard.stream import input_options, local

PROBE_TIMEOUT = 45.0


@dataclass(frozen=True)
class Result:
    channel: Channel
    height: int | None
    problem: str | None

    @property
    def ok(self) -> bool:
        return self.problem is None


def probe_height(ffprobe: str, relay: Relay, resolved: Resolved, runner: Runner = run_command) -> int | None:
    feed = relay.open(resolved, "check")
    try:
        args = [ffprobe, "-v", "error", *input_options()]
        args += ["-select_streams", "v", "-show_entries", "stream=height", "-of", "json", local(resolved, feed).video]
        data = json.loads(runner(args, PROBE_TIMEOUT, None) or "{}")
    finally:
        feed.close()
    heights = [stream.get("height") for stream in data.get("streams", []) if isinstance(stream, dict)]
    numbers = [height for height in heights if isinstance(height, int)]
    return max(numbers) if numbers else None


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
