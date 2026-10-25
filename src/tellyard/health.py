import json
import os
import sys
import urllib.error
import urllib.request

from tellyard.settings import Settings, SettingsError


def probe(port: int) -> int:
    url = f"http://127.0.0.1:{port}/healthz"
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with direct.open(url, timeout=5) as response:
            status = json.load(response).get("status")
    except urllib.error.HTTPError as error:
        try:
            reason = json.load(error).get("reason") or f"HTTP {error.code}"
        except ValueError:
            reason = f"HTTP {error.code}"
        print(f"tellyard: unhealthy: {reason}", file=sys.stderr)
        return 1
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
        print(f"tellyard: unhealthy: {error}", file=sys.stderr)
        return 1
    return 0 if status == "ok" else 1


def main() -> int:
    try:
        settings = Settings.from_env(os.environ)
    except SettingsError as error:
        print(f"tellyard: {error}", file=sys.stderr)
        return 2
    return probe(settings.port)


if __name__ == "__main__":
    raise SystemExit(main())
