import json
import os
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from tellyard import channels, check, cli, health
from tellyard.check import Result
from tellyard.server import App
from tellyard.settings import Settings


@pytest.fixture(autouse=True)
def environment(monkeypatch: pytest.MonkeyPatch, channels_file: Path, tmp_path: Path) -> None:
    monkeypatch.setenv("CHANNELS_FILE", str(channels_file))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))


def test_validate_lists_channels(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["validate"]) == 0
    out = capsys.readouterr().out
    assert "101  News One  (LiveSource, guide: placeholder)" in out
    assert out.strip().endswith("2 channels are valid")


def test_bad_channels_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("CHANNELS_FILE", str(tmp_path / "none.toml"))
    assert cli.main(["validate"]) == 2
    assert "cannot read" in capsys.readouterr().err


class QuietServer:
    def __init__(self, address: tuple[str, int], app: object) -> None:
        self.app = app

    def __enter__(self) -> "QuietServer":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def serve_forever(self) -> None:
        return None

    def shutdown(self) -> None:
        return None


def test_serve_starts_without_a_channel_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("CHANNELS_FILE", str(tmp_path / "none.toml"))
    monkeypatch.setattr(cli, "Server", QuietServer)
    monkeypatch.setattr(App, "start", lambda self, stop: None)
    assert cli.main(["serve"]) == 0
    assert "0 from" in capsys.readouterr().err


def test_serve_refuses_a_bad_channel_file(
    monkeypatch: pytest.MonkeyPatch, channels_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    channels_file.write_text("[[channel]]\nnumber = 1\n", encoding="utf-8")
    assert cli.main(["serve"]) == 2
    assert "tellyard:" in capsys.readouterr().err


def test_bad_settings(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("LISTEN_PORT", "x")
    assert cli.main(["validate"]) == 2
    assert "LISTEN_PORT" in capsys.readouterr().err


def test_guide_to_file(tmp_path: Path) -> None:
    output = tmp_path / "guide.xml"
    assert cli.main(["guide", "-o", str(output)]) == 0
    assert b'<channel id="tellyard.101">' in output.read_bytes()


def test_guide_to_stdout(capsysbinary: pytest.CaptureFixture[bytes]) -> None:
    assert cli.main(["guide"]) == 0
    assert capsysbinary.readouterr().out.startswith(b"<?xml")


def test_check_command(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    def fake_check(chosen: list[object], resolver: object, measure: object, minimum: object) -> list[Result]:
        return [Result(channel, 1080, None) for channel in chosen]  # type: ignore[arg-type]

    monkeypatch.setattr(check, "check", fake_check)
    assert cli.main(["check", "201"]) == 0
    assert "1 of 1 channels passed" in capsys.readouterr().out
    assert cli.main(["check", "555"]) == 2


def test_check_command_fails_on_a_bad_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_check(chosen: list[object], resolver: object, measure: object, minimum: object) -> list[Result]:
        return [Result(channel, 720, "below the 1080p minimum") for channel in chosen]  # type: ignore[arg-type]

    monkeypatch.setattr(check, "check", fake_check)
    assert cli.main(["check"]) == 1


class Healthy(BaseHTTPRequestHandler):
    reply: tuple[int, bytes] = (200, json.dumps({"status": "ok"}).encode())

    def do_GET(self) -> None:
        code, body = self.reply
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def health_port() -> Iterator[int]:
    server = HTTPServer(("127.0.0.1", 0), Healthy)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()
    server.server_close()


def test_health_command(monkeypatch: pytest.MonkeyPatch, health_port: int) -> None:
    monkeypatch.setenv("LISTEN_PORT", str(health_port))
    assert cli.main(["health"]) == 0


def test_health_command_ignores_the_proxy(monkeypatch: pytest.MonkeyPatch, health_port: int) -> None:
    monkeypatch.setenv("LISTEN_PORT", str(health_port))
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.setattr("urllib.request._opener", None)
    assert cli.main(["health"]) == 0


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        (json.dumps({"status": "unhealthy", "reason": "the guide thread has stopped"}).encode(), "guide thread"),
        (b"not json", "HTTP 503"),
    ],
)
def test_health_command_prints_the_reason(
    monkeypatch: pytest.MonkeyPatch, health_port: int, capsys: pytest.CaptureFixture[str], body: bytes, reason: str
) -> None:
    monkeypatch.setattr(Healthy, "reply", (503, body))
    monkeypatch.setenv("LISTEN_PORT", str(health_port))
    assert cli.main(["health"]) == 1
    assert reason in capsys.readouterr().err


def test_banner_summarises_the_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MIN_HEIGHT", "1080")
    monkeypatch.setenv("HEARTBEAT_URL", "https://ping.example.com/abc")
    settings = Settings.from_env(os.environ)
    text = cli._banner(settings, channels.load(settings.channels_file))
    assert "  Channels   2 from " in text
    assert "  Quality    check needs 1080p\n" in text + "\n"
    assert "  Guide      3 days, rebuilt every 6 h, 0 XMLTV sources\n" in text
    assert "  Heartbeat  on\n" in text


def test_health_command_when_down(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("LISTEN_PORT", "1")
    assert cli.main(["health"]) == 1
    assert "unhealthy" in capsys.readouterr().err


def test_health_module(monkeypatch: pytest.MonkeyPatch, health_port: int, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.setenv("LISTEN_PORT", str(health_port))
    assert health.main() == 0
    monkeypatch.setenv("LISTEN_PORT", "0")
    assert health.main() == 2
    assert "LISTEN_PORT" in capsys.readouterr().err
