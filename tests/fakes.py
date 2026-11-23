import stat
import sys
import threading
from pathlib import Path

from tellyard.channels import Channel
from tellyard.resolve import Resolved, ResolveError, Resolver
from tellyard.settings import Settings

# Stands in for ffmpeg. FAKE_FFMPEG picks the behaviour: steady, short, silent or fail.
SCRIPT = """#!{python}
import os, sys, time
mode = os.environ.get("FAKE_FFMPEG", "steady")
out = sys.stdout.buffer
if mode == "fail":
    sys.stderr.write("Server returned 403 Forbidden\\n")
    sys.exit(1)
if mode == "silent":
    time.sleep(60)
count = 3 if mode == "short" else 100000
for _ in range(count):
    out.write(b"G" * 188 * 10)
    out.flush()
    time.sleep(0.02)
"""


def fake_ffmpeg(directory: Path) -> Path:
    path = directory / "fake-ffmpeg"
    path.write_text(SCRIPT.format(python=sys.executable), encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


class FakeResolver(Resolver):
    def __init__(
        self, settings: Settings, failures: int = 0, height: int | None = 1080, *, remembered: bool = False
    ) -> None:
        super().__init__(settings)
        self.calls = 0
        self.fresh: list[bool] = []
        self.forgotten = 0
        self._failures = failures
        self._height = height
        self._remembered = remembered

    def forget(self, channel: Channel) -> None:
        self.forgotten += 1
        self._remembered = False

    def resolve(self, channel: Channel, cancel: threading.Event | None = None, *, fresh: bool = False) -> Resolved:
        self.calls += 1
        self.fresh.append(fresh)
        if self.calls <= self._failures:
            raise ResolveError("the YouTube channel is not live right now")
        url = f"https://cdn.example.com/{channel.number}.m3u8?attempt={self.calls}"
        return Resolved(url, None, self._height, (), remembered=self._remembered and not fresh)
