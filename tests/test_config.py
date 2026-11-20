import stat
import sys
from pathlib import Path

import pytest

from duet import config as c


def test_config_dir_prefers_duet_home(tmp_path, monkeypatch):
    monkeypatch.setenv("DUET_HOME", str(tmp_path / "x"))
    assert c.config_dir() == tmp_path / "x"


def test_config_dir_platforms(tmp_path, monkeypatch):
    monkeypatch.delenv("DUET_HOME")
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    assert c.config_dir() == tmp_path / "AppData" / "duet"
    monkeypatch.setattr(sys, "platform", "darwin")
    assert c.config_dir() == Path.home() / "Library" / "Application Support" / "duet"
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert c.config_dir() == tmp_path / "xdg" / "duet"


def test_default_path_and_missing_file(tmp_path):
    assert c.default_config_path() == tmp_path / "home" / "config.toml"
    cfg = c.load_config()
    assert cfg.telegram_token == "" and cfg.chat_id is None and cfg.channel == ""
    with pytest.raises(FileNotFoundError):
        c.load_config(required=True)


def test_roundtrip_and_permissions(tmp_path):
    path = tmp_path / "cfg" / "config.toml"
    cfg = c.Config(telegram_token='12:ab"c', chat_id=-100123, timezone="Europe/Vienna", approval_timeout_min=5)
    c.save_config(cfg, path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = c.load_config(path)
    assert loaded == cfg
    assert loaded.channel == "telegram"


def test_env_overrides(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    c.save_config(c.Config(telegram_token="file", chat_id=1), path)
    monkeypatch.setenv("DUET_TELEGRAM_TOKEN", "env-token")
    monkeypatch.setenv("DUET_CHAT_ID", "777")
    monkeypatch.setenv("DUET_TIMEZONE", "UTC")
    monkeypatch.setenv("DUET_APPROVAL_TIMEOUT_MIN", "3")
    cfg = c.load_config(path)
    assert (cfg.telegram_token, cfg.chat_id, cfg.timezone, cfg.approval_timeout_min) == ("env-token", 777, "UTC", 3)


def test_env_only_ntfy(monkeypatch):
    monkeypatch.setenv("DUET_NTFY_TOPIC", "geheim-topic")
    cfg = c.load_config()
    assert cfg.channel == "ntfy"


def test_unknown_keys_ignored(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('telegram_token = "t"\nchat_id = 5\nheartbeat_hours = 3.0\n')
    cfg = c.load_config(path)
    assert cfg.chat_id == 5
