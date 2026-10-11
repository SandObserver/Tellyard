"""Small HTTP helpers on top of urllib."""

import gzip
import io
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from tellyard.channels import Headers

# Several broadcasters refuse requests that do not look like a browser.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
TEXT_LIMIT = 4 * 1024 * 1024
DOWNLOAD_LIMIT = 256 * 1024 * 1024
CHUNK = 256 * 1024
GZIP_MAGIC = b"\x1f\x8b"


class FetchError(Exception):
    pass


def request(url: str, headers: Headers = ()) -> urllib.request.Request:
    merged = {"User-Agent": USER_AGENT, "Accept-Encoding": "identity"}
    merged.update(dict(headers))
    return urllib.request.Request(url, headers=merged)


def fetch_text(url: str, headers: Headers = (), timeout: float = 10.0) -> tuple[str, str]:
    """Return the final URL after redirects and the decoded body."""
    try:
        with urllib.request.urlopen(request(url, headers), timeout=timeout) as response:
            body = response.read(TEXT_LIMIT + 1)
            final = response.geturl()
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise FetchError(f"{_host(url)}: {_reason(error)}") from error
    if len(body) > TEXT_LIMIT:
        raise FetchError(f"{_host(url)}: response is larger than {TEXT_LIMIT // 1024 // 1024} MB")
    return final, body.decode("utf-8", errors="replace")


def download(url: str, destination: Path, timeout: float = 60.0) -> None:
    """Stream a file to disk so large guides never sit in memory."""
    partial = destination.with_name(destination.name + ".part")
    total = 0
    try:
        with urllib.request.urlopen(request(url), timeout=timeout) as response, partial.open("wb") as out:
            while chunk := response.read(CHUNK):
                total += len(chunk)
                if total > DOWNLOAD_LIMIT:
                    raise FetchError(f"{_host(url)}: file is larger than {DOWNLOAD_LIMIT // 1024 // 1024} MB")
                out.write(chunk)
        partial.replace(destination)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise FetchError(f"{_host(url)}: {_reason(error)}") from error
    finally:
        partial.unlink(missing_ok=True)


def open_maybe_gzip(path: Path) -> io.BufferedIOBase:
    with path.open("rb") as probe:
        magic = probe.read(2)
    return gzip.open(path, "rb") if magic == GZIP_MAGIC else path.open("rb")


def _host(url: str) -> str:
    return urllib.parse.urlsplit(url).netloc or url


def _reason(error: BaseException) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f"HTTP {error.code}"
    if isinstance(error, urllib.error.URLError):
        return str(error.reason)
    return str(error) or type(error).__name__
