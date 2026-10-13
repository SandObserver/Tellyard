import logging
import threading
from pathlib import Path

from tellyard import channels
from tellyard.channels import ChannelError, Lineup

log = logging.getLogger(__name__)


class ChannelStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        self._lineup = channels.load(path)
        self._stamp = self._read_stamp()
        self.version = 1

    def current(self) -> Lineup:
        with self._lock:
            stamp = self._read_stamp()
            if stamp != self._stamp:
                self._stamp = stamp
                try:
                    self._lineup = channels.load(self._path)
                except ChannelError as error:
                    log.error(
                        "channel file has an error, keeping the previous channels",
                        extra={"file": self._path, "reason": str(error)},
                    )
                else:
                    self.version += 1
                    log.info("channels reloaded", extra={"channels": len(self._lineup.channels), "file": self._path})
            return self._lineup

    def _read_stamp(self) -> tuple[int, int] | None:
        try:
            info = self._path.stat()
        except OSError:
            return None
        return info.st_mtime_ns, info.st_size
