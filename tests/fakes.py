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
    def __init__(self, settings: Settings, failures: int = 0, height: int | None = 1080) -> None:
        super().__init__(settings)
        self.calls = 0
        self._failures = failures
        self._height = height

    def resolve(self, channel: Channel, cancel: threading.Event | None = None) -> Resolved:
        self.calls += 1
        if self.calls <= self._failures:
            raise ResolveError("the YouTube channel is not live right now")
        return Resolved(f"https://cdn.example.com/{channel.number}.m3u8", None, self._height, ())
