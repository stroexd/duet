"""Konfiguration (TOML) mit plattformneutralen Pfaden und Env-Overrides für Cloud-Routinen."""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any


def config_dir() -> Path:
    """DUET_HOME > Windows %APPDATA%\\duet > macOS ~/Library/Application Support/duet > XDG/~/.config/duet."""
    if env := os.environ.get("DUET_HOME"):
        return Path(env).expanduser()
    if sys.platform == "win32" and (appdata := os.environ.get("APPDATA")):
        return Path(appdata) / "duet"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "duet"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    return (Path(xdg) if xdg else Path.home() / ".config") / "duet"


def default_config_path() -> Path:
    if env := os.environ.get("DUET_CONFIG"):
        return Path(env).expanduser()
    return config_dir() / "config.toml"


CONFIG_PATH = default_config_path()

DEFAULT_TELEGRAM_API = "https://api.telegram.org"


def telegram_api() -> str:
    """Basis-URL der Telegram-Bot-API; Override per DUET_TELEGRAM_API (Tests, lokaler Bot-API-Server)."""
    return (os.environ.get("DUET_TELEGRAM_API") or "").strip().rstrip("/") or DEFAULT_TELEGRAM_API


@dataclass
class Config:
    telegram_token: str = ""
    chat_id: int | None = None  # None bis gekoppelt
    ntfy_topic: str = ""  # Fallback-Kanal (reiner Push), "" = aus
    ntfy_server: str = "https://ntfy.sh"
    timezone: str = "Europe/Berlin"
    approval_timeout_min: int = 10

    @property
    def channel(self) -> str:
        """"telegram", "ntfy" oder "" (kein Kanal)."""
        if self.telegram_token and self.chat_id is not None:
            return "telegram"
        return "ntfy" if self.ntfy_topic else ""


_ENV = {
    "DUET_TELEGRAM_TOKEN": "telegram_token",
    "DUET_CHAT_ID": "chat_id",
    "DUET_NTFY_TOPIC": "ntfy_topic",
    "DUET_NTFY_SERVER": "ntfy_server",
    "DUET_TIMEZONE": "timezone",
    "DUET_APPROVAL_TIMEOUT_MIN": "approval_timeout_min",
}


def _coerce(name: str, value: Any) -> Any:
    if name == "chat_id":
        return None if value in (None, "") else int(value)
    if name == "approval_timeout_min":
        return int(value)
    return str(value)


def load_config(path: Path | None = None, required: bool = False) -> Config:
    """Liest config.toml (falls vorhanden) und wendet DUET_*-Env-Overrides an.

    In Cloud-Routinen gibt es meist keine Datei: dann kommt alles aus der Umgebung.
    required=True wirft FileNotFoundError, wenn die Datei fehlt.
    """
    path = Path(path).expanduser() if path else default_config_path()
    data: dict[str, Any] = {}
    if path.exists():
        with path.open("rb") as fh:
            data = tomllib.load(fh)
    elif required:
        raise FileNotFoundError(path)
    known = {f.name for f in fields(Config)}
    kwargs = {k: _coerce(k, v) for k, v in data.items() if k in known}
    for env, name in _ENV.items():
        if value := os.environ.get(env):
            kwargs[name] = _coerce(name, value.strip())
    return Config(**kwargs)


def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    s = str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{s}"'


def save_config(cfg: Config, path: Path | None = None) -> None:
    """Schreibt config.toml (None-Werte weggelassen) mit Rechten 600 (unter Windows best effort)."""
    path = Path(path).expanduser() if path else default_config_path()
    lines = ["# Duet Konfiguration"]
    lines += [f"{f.name} = {_toml_value(getattr(cfg, f.name))}" for f in fields(Config)
              if getattr(cfg, f.name) is not None]
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:  # pragma: no cover
        pass
