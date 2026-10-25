import pytest

from tellyard.settings import LogLevel, Settings, SettingsError


def test_defaults() -> None:
    settings = Settings.from_env({})
    assert settings.port == 5004
    assert settings.tuners == 4
    assert settings.base_url == ""
    assert settings.log_level is LogLevel.INFO
    assert settings.min_height is None
    assert len(settings.device_id) == 8


def test_device_id_is_stable_and_follows_the_name() -> None:
    first = Settings.from_env({"DEVICE_NAME": "Living Room"}).device_id
    assert first == Settings.from_env({"DEVICE_NAME": "Living Room"}).device_id
    assert first != Settings.from_env({"DEVICE_NAME": "Bedroom"}).device_id


def test_reads_values() -> None:
    settings = Settings.from_env(
        {
            "LISTEN_PORT": "8080",
            "MIN_HEIGHT": "1080",
            "TUNERS": "2",
            "BASE_URL": "http://10.1.2.3:8080/",
            "DEVICE_ID": "abcd1234",
            "YTDLP_ARGS": "--extractor-args 'youtube:player_client=web'",
            "LOG_LEVEL": "DEBUG",
        }
    )
    assert settings.port == 8080
    assert settings.tuners == 2
    assert settings.base_url == "http://10.1.2.3:8080"
    assert settings.device_id == "ABCD1234"
    assert settings.ytdlp_args == ("--extractor-args", "youtube:player_client=web")
    assert settings.log_level is LogLevel.DEBUG
    assert settings.min_height == 1080
    assert Settings.from_env({"MIN_HEIGHT": "Off"}).min_height is None


def test_reads_the_time_zone() -> None:
    assert Settings.from_env({}).time_zone is None
    assert str(Settings.from_env({"TZ": "Europe/Berlin"}).time_zone) == "Europe/Berlin"
    assert str(Settings.from_env({"TZ": ":Europe/Berlin"}).time_zone) == "Europe/Berlin"
    assert Settings.from_env({"TZ": "Europe/Berlin"}).unknown_time_zone == ""


@pytest.mark.parametrize(
    "value", ["Mars/Olympus", "/etc/localtime", ":/etc/localtime", "UTC0", "EST5EDT,M3.2.0,M11.1.0"]
)
def test_unknown_time_zone_keeps_utc(value: str) -> None:
    settings = Settings.from_env({"TZ": value})
    assert settings.time_zone is None
    assert settings.unknown_time_zone == value.removeprefix(":")


def test_empty_values_mean_the_defaults() -> None:
    names = ["TUNERS", "MIN_HEIGHT", "DEVICE_NAME", "DEVICE_ID", "BASE_URL", "GUIDE_DAYS", "GUIDE_REFRESH_HOURS"]
    names += ["YTDLP_ARGS", "HEARTBEAT_URL", "LOG_LEVEL", "LISTEN_PORT", "TZ"]
    assert Settings.from_env(dict.fromkeys(names, "")) == Settings.from_env({})


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"LISTEN_PORT": "abc"}, "whole number"),
        ({"MIN_HEIGHT": "100"}, "between 144 and 4320"),
        ({"MIN_HEIGHT": "hd"}, "whole number"),
        ({"TUNERS": "0"}, "between 1 and 32"),
        ({"DEVICE_ID": "xyz"}, "8 hexadecimal"),
        ({"BASE_URL": "http://host:1/path"}, "no path"),
        ({"LOG_LEVEL": "loud"}, "LOG_LEVEL"),
        ({"YTDLP_ARGS": "'unclosed"}, "cannot be parsed"),
        ({"HEARTBEAT_URL": "example.com/ping"}, "HEARTBEAT_URL"),
    ],
)
def test_rejects_bad_values(env: dict[str, str], message: str) -> None:
    with pytest.raises(SettingsError, match=message):
        Settings.from_env(env)
