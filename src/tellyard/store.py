import logging
import os
import threading
from pathlib import Path

from tellyard import channels
from tellyard.channels import ChannelError, Lineup

log = logging.getLogger(__name__)


class ChannelStore:
    def __init__(self, path: Path, saved: Path | None = None) -> None:
        self._path = path
        self._saved = saved
        self._lock = threading.Lock()
        self.error: str | None = None
        self._stamp = self._read_stamp()
        if self._stamp is None and not path.exists():
            self._lineup = Lineup(())
            log.warning("no channel file yet, add your channels to it", extra={"file": path})
        else:
            try:
                self._lineup = self._load()
            except ChannelError as error:
                self._lineup = self._last_good(error)
        self.version = 1

    def current(self) -> Lineup:
        return self.snapshot()[0]

    def snapshot(self) -> tuple[Lineup, int]:
        with self._lock:
            stamp = self._read_stamp()
            if stamp != self._stamp:
                self._stamp = stamp
                try:
                    self._lineup = self._load()
                except ChannelError as error:
                    self.error = str(error)
                    log.error(
                        "channel file has an error, keeping the previous channels",
                        extra={"file": self._path, "reason": self.error},
                    )
                else:
                    self.version += 1
                    log.info("channels reloaded", extra={"channels": len(self._lineup.channels), "file": self._path})
            return self._lineup, self.version

    def _load(self) -> Lineup:
        lineup, text = channels.load_text(self._path)
        self.error = None
        self._save(text)
        return lineup

    def _last_good(self, error: ChannelError) -> Lineup:
        if self._saved is None or not self._saved.exists():
            raise error
        try:
            lineup = channels.load(self._saved)
        except ChannelError:
            raise error from None
        self.error = str(error)
        log.error(
            "channel file has an error, starting with the last good copy",
            extra={"file": self._path, "reason": self.error, "copy": self._saved},
        )
        return lineup

    def _save(self, text: str) -> None:
        if self._saved is None:
            return
        try:
            if self._saved.exists() and self._saved.read_text(encoding="utf-8") == text:
                return
            self._saved.parent.mkdir(parents=True, exist_ok=True)
            partial = self._saved.with_name(f".{self._saved.name}.partial")
            descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                file.write(text)
            partial.replace(self._saved)
        except (OSError, UnicodeDecodeError) as error:
            log.warning("channel file copy not saved", extra={"copy": self._saved, "reason": str(error)})

    def _read_stamp(self) -> tuple[int, ...] | None:
        try:
            info = self._path.stat()
        except OSError:
            return None
        return info.st_mtime_ns, info.st_size, info.st_ino, info.st_mode, info.st_uid, info.st_gid
