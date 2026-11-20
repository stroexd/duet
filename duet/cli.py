"""duet-setup: Einrichtung und Diagnose für das Duet-Plugin.

Kommandos:
  init    Telegram-Bot prüfen und koppeln, gibt die Werte für die Cloud-Umgebung aus
  doctor  lokale Prüfung (uv, claude CLI, Login, Module, Konfiguration, Telegram, Plugin/MCP)
  status  Konfiguration (Token maskiert) und Zustandsdateien anzeigen

Backend-Module werden lazy importiert, damit `--help` und `doctor` auch bei
unvollständiger Installation funktionieren.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Sequence

from duet.config import telegram_api

PAIR_TIMEOUT_S = 300
DEFAULT_TZ = "Europe/Berlin"
DEFAULT_APPROVAL_TIMEOUT_MIN = 10
DEFAULT_STATE_DIR = "state"

OK = "✔"    # ✔
FAIL = "✘"  # ✘
WARN = "!"


# --------------------------------------------------------------------------- Hilfen


def mask_token(token: str | None) -> str:
    """Maskiert einen Telegram-Bot-Token: Bot-ID bleibt sichtbar, vom Secret nur die letzten 4 Zeichen."""
    if not token:
        return "(nicht gesetzt)"
    if ":" in token:
        bot_id, secret = token.split(":", 1)
        tail = secret[-4:] if len(secret) > 8 else ""
        return f"{bot_id}:{'*' * 8}{tail}"
    if len(token) <= 8:
        return "*" * len(token)
    return f"{'*' * 8}{token[-4:]}"


def cloud_env(token: str, chat_id: int | None, timezone: str,
              approval_timeout_min: int = DEFAULT_APPROVAL_TIMEOUT_MIN,
              ntfy_topic: str = "") -> list[tuple[str, str]]:
    """Umgebungsvariablen für die Cloud-Umgebung der Routinen (Reihenfolge = Ausgabe)."""
    env = [
        ("DUET_TELEGRAM_TOKEN", token),
        ("DUET_CHAT_ID", "" if chat_id is None else str(chat_id)),
        ("DUET_TIMEZONE", timezone),
        ("DUET_UNATTENDED", "1"),
        ("DUET_STATE_DIR", DEFAULT_STATE_DIR),
        ("DUET_APPROVAL_TIMEOUT_MIN", str(approval_timeout_min)),
    ]
    if ntfy_topic:
        env.append(("DUET_NTFY_TOPIC", ntfy_topic))
    return env


def format_env(env: Sequence[tuple[str, str]]) -> str:
    """.env-Format, wie es das Feld "Environment variables" der Cloud-Umgebung erwartet."""
    return "\n".join(f"{k}={v}" for k, v in env)


def _run(cmd: Sequence[str], timeout: float = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=timeout)


def _telegram_get_me(token: str, timeout: float = 15) -> dict[str, Any]:
    """Ruft getMe auf und liefert das result-Objekt; wirft RuntimeError bei Fehler."""
    import httpx

    try:
        r = httpx.get(f"{telegram_api()}/bot{token}/getMe", timeout=timeout)
    except httpx.HTTPError as e:
        raise RuntimeError(f"Telegram nicht erreichbar: {type(e).__name__}") from None
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"Ungültige Antwort von Telegram (HTTP {r.status_code})") from None
    if not data.get("ok"):
        raise RuntimeError(data.get("description") or f"HTTP {r.status_code}")
    return data["result"]


def _ask(prompt: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    try:
        val = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        val = ""
    return val or (default or "")


def _config_mod():
    from duet import config as dconfig

    return dconfig


def _config_path(args: argparse.Namespace) -> Path:
    if getattr(args, "config", None):
        return Path(args.config).expanduser()
    return _config_mod().default_config_path()


def _state_dir() -> Path:
    sd = os.environ.get("DUET_STATE_DIR")
    if sd:
        return Path(sd).expanduser().resolve()
    return _config_mod().config_dir()


# --------------------------------------------------------------------------- Kopplung


def pair_chat(token: str, code: str, timeout_s: float = PAIR_TIMEOUT_S,
              http_get: Callable[..., Any] | None = None,
              clock: Callable[[], float] = time.monotonic) -> int | None:
    """Wartet per getUpdates auf eine private Nachricht, deren Text exakt `code` ist.

    Liefert die chat_id oder None bei Timeout. Ältere Updates werden vorher verworfen,
    damit keine fremde oder alte Nachricht die Kopplung übernimmt.
    """
    import httpx

    get = http_get or httpx.get
    base = f"{telegram_api()}/bot{token}/getUpdates"
    offset: int | None = None
    try:
        r = get(base, params={"offset": -1, "timeout": 0}, timeout=15)
        res = r.json().get("result") or []
        if res:
            offset = res[-1]["update_id"] + 1
    except Exception:
        pass

    deadline = clock() + timeout_s
    while clock() < deadline:
        poll = int(max(1, min(25, deadline - clock())))
        params: dict[str, Any] = {"timeout": poll,
                                  "allowed_updates": json.dumps(["message", "callback_query"])}
        if offset is not None:
            params["offset"] = offset
        try:
            r = get(base, params=params, timeout=poll + 10)
            data = r.json()
        except Exception as e:
            print(f"  (Verbindungsfehler: {type(e).__name__}, versuche erneut …)")
            time.sleep(2)
            continue
        if not data.get("ok"):
            raise RuntimeError(data.get("description") or "getUpdates fehlgeschlagen")
        for upd in data.get("result") or []:
            offset = upd["update_id"] + 1
            msg = upd.get("message") or {}
            text = (msg.get("text") or "").strip()
            if text == code and (msg.get("chat") or {}).get("type") == "private":
                try:  # Update bestätigen, damit inbox() es nicht als Auftrag liest
                    get(base, params={"offset": offset, "timeout": 0}, timeout=15)
                except Exception:
                    pass
                return int(msg["chat"]["id"])
    return None


def _send_plain(token: str, chat_id: int, text: str) -> None:
    import httpx

    try:
        httpx.post(f"{telegram_api()}/bot{token}/sendMessage",
                   json={"chat_id": chat_id, "text": text}, timeout=15)
    except Exception:
        pass


# --------------------------------------------------------------------------- init


def cmd_init(args: argparse.Namespace) -> int:
    dconfig = _config_mod()
    cfg_path = _config_path(args)
    existing = None
    try:
        existing = dconfig.load_config(cfg_path)
    except Exception:
        existing = None

    print("== Duet einrichten ==\n")
    token = args.token or os.environ.get("DUET_TELEGRAM_TOKEN", "") or (
        existing.telegram_token if existing and args.reuse else "")
    if not token:
        print("Lege in Telegram bei @BotFather einen Bot an (/newbot) und kopiere den Token.")
        token = getpass.getpass("Telegram-Bot-Token (Eingabe unsichtbar): ").strip()
    if not token:
        print(f"{FAIL} Kein Token angegeben.")
        return 1
    try:
        me = _telegram_get_me(token)
    except RuntimeError as e:
        print(f"{FAIL} Token ungültig oder Telegram nicht erreichbar: {e}")
        return 1
    username = me.get("username", "?")
    print(f"{OK} Bot gefunden: @{username}")

    chat_id: int | None = args.chat_id
    if chat_id is None and not args.no_pair:
        code = f"{secrets.randbelow(1_000_000):06d}"
        print("\nKopplung: Öffne den Chat mit deinem Bot in Telegram:")
        print(f"   https://t.me/{username}")
        print(f"und sende ihm GENAU diese Nachricht:   {code}")
        print(f"(Warte max. {PAIR_TIMEOUT_S // 60} Minuten …)")
        try:
            chat_id = pair_chat(token, code)
        except RuntimeError as e:
            print(f"{FAIL} Kopplung fehlgeschlagen: {e}")
            print("  Tipp: Ist ein Webhook gesetzt? Dann zuerst deleteWebhook aufrufen.")
            return 1
        except KeyboardInterrupt:
            print("\nAbgebrochen.")
            return 1
        if chat_id is None:
            print(f"{FAIL} Kein passender Code empfangen. Starte `duet-setup init` erneut.")
            return 1
        print(f"{OK} Gekoppelt mit chat_id {chat_id}")
    elif chat_id is None:
        print(f"{WARN} Keine Kopplung (--no-pair). Ohne chat_id meldet sich Duet bei niemandem.")

    interactive = sys.stdin.isatty() and not args.yes
    tz_default = args.timezone or (existing.timezone if existing else DEFAULT_TZ)
    timezone = args.timezone or (_ask("Zeitzone", tz_default) if interactive else tz_default)
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(timezone)
    except Exception:
        print(f"{FAIL} Unbekannte Zeitzone: {timezone}")
        return 1

    approval_timeout = args.approval_timeout or DEFAULT_APPROVAL_TIMEOUT_MIN

    if not args.no_save:
        cfg = dconfig.Config(telegram_token=token, chat_id=chat_id, timezone=timezone,
                             approval_timeout_min=approval_timeout,
                             **({"ntfy_topic": args.ntfy_topic} if args.ntfy_topic else {}))
        dconfig.save_config(cfg, cfg_path)
        print(f"\n{OK} Lokale Konfiguration gespeichert: {cfg_path} (nur für dich lesbar)")

    if chat_id is not None and not args.no_pair:
        _send_plain(token, chat_id, "Gekoppelt. Ab jetzt melde ich mich hier, wenn etwas deine "
                                    "Aufmerksamkeit braucht. Du kannst mir Aufträge schreiben; "
                                    "ich lese sie beim nächsten Lauf (stündlich).")

    env = cloud_env(token, chat_id, timezone, approval_timeout, args.ntfy_topic or "")
    print("\n== Werte für die Cloud-Umgebung (claude.ai/code → Umgebung → Environment variables) ==")
    print("-" * 60)
    print(format_env(env))
    print("-" * 60)
    print("Hinweise:")
    print("  • Diese Werte sind für alle sichtbar, die die Umgebung nutzen – nutze eine eigene,")
    print("    private Umgebung. Den Token NICHT ins Repo committen.")
    print("  • Netzwerkzugriff der Umgebung: 'Limited' + 'Allow package managers' und unter")
    print("    'Allowed domains' zusätzlich:  api.telegram.org" + ("  ntfy.sh" if args.ntfy_topic else ""))
    print("  • Danach die Routinen anlegen (Skill /duet:setup führt durch alle Schritte).")
    return 0


# --------------------------------------------------------------------------- status


def cmd_status(args: argparse.Namespace) -> int:
    dconfig = _config_mod()
    cfg_path = _config_path(args)
    print("== Duet Status ==")
    try:
        cfg = dconfig.load_config(cfg_path)
    except Exception as e:
        print(f"{FAIL} Konfiguration nicht lesbar: {e}")
        return 1
    src = "Datei + Umgebung" if cfg_path.exists() else "nur Umgebungsvariablen"
    print(f"Konfiguration:   {cfg_path} ({src})")
    print(f"Bot-Token:       {mask_token(cfg.telegram_token)}")
    print(f"chat_id:         {cfg.chat_id if cfg.chat_id is not None else '(nicht gekoppelt)'}")
    print(f"Kanal:           {getattr(cfg, 'channel', '') or '(keiner – duet-setup init ausführen)'}")
    print(f"Zeitzone:        {cfg.timezone}")
    print(f"Freigabe-Timeout: {cfg.approval_timeout_min} min")
    print(f"Unbeaufsichtigt: {'ja' if os.environ.get('DUET_UNATTENDED') == '1' else 'nein'}")
    sd = _state_dir()
    print(f"Zustand:         {sd}")
    if sd.is_dir():
        files = sorted(p for p in sd.iterdir() if p.is_file() and not p.name.startswith("."))
        if not files:
            print("  (noch keine Zustandsdateien)")
        for p in files:
            ts = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            print(f"  {p.name:<16} {ts}  {p.stat().st_size} B")
    else:
        print("  (Ordner existiert noch nicht)")
    return 0


# --------------------------------------------------------------------------- doctor


class _Doctor:
    def __init__(self) -> None:
        self.failed = 0

    def ok(self, msg: str) -> None:
        print(f"{OK} {msg}")

    def fail(self, msg: str, fix: str) -> None:
        self.failed += 1
        print(f"{FAIL} {msg}\n    → {fix}")

    def warn(self, msg: str, fix: str = "") -> None:
        print(f"{WARN} {msg}" + (f"\n    → {fix}" if fix else ""))


MODULES = ("mcp", "httpx", "duet.config", "duet.telegram", "duet.policy", "duet.approvals",
           "duet.store", "duet.hook", "duet.mcp_server")


def cmd_doctor(args: argparse.Namespace) -> int:
    d = _Doctor()
    print("== Duet Diagnose (lokal) ==")

    if sys.version_info >= (3, 11):
        d.ok(f"Python {sys.version.split()[0]}")
    else:
        d.fail(f"Python {sys.version.split()[0]} ist zu alt", "Python 3.11 oder neuer installieren.")

    if shutil.which("uvx"):
        d.ok(f"uvx gefunden ({shutil.which('uvx')})")
    else:
        d.fail("uvx nicht gefunden – Plugin startet MCP-Server und Hook über uvx",
               "uv installieren: https://docs.astral.sh/uv/ (z. B. `pipx install uv` oder Paketmanager).")

    claude = shutil.which("claude")
    if not claude:
        d.fail("`claude` CLI nicht im PATH", "Claude Code installieren: https://claude.ai/install")
    else:
        try:
            r = _run([claude, "--version"], timeout=20)
            d.ok(f"claude CLI: {r.stdout.strip() or claude}")
        except Exception as e:
            d.fail(f"`claude --version` fehlgeschlagen ({type(e).__name__})", "Claude Code neu installieren.")
        try:
            r = _run([claude, "auth", "status"], timeout=20)
            out = (r.stdout or "").strip()
            if r.returncode == 0 and out.startswith("{"):
                info = json.loads(out)
                if info.get("loggedIn"):
                    d.ok(f"Claude-Login vorhanden ({info.get('authMethod', '?')})")
                else:
                    d.fail("Nicht bei Claude eingeloggt", "`claude` starten und /login (Pro/Max).")
            elif r.returncode == 0:
                d.ok("Claude-Login: " + (out.splitlines()[0] if out else "ok"))
            elif "unknown command" in (r.stderr + out).lower():
                d.warn("`claude auth status` gibt es in dieser Version nicht", "Login in `claude` mit /login prüfen.")
            else:
                d.fail("Nicht bei Claude eingeloggt", "`claude` starten und /login (Pro/Max).")
        except Exception as e:
            d.warn(f"Login-Status nicht prüfbar ({type(e).__name__})", "`claude` starten, ggf. /login.")

    for mod in MODULES:
        try:
            __import__(mod)
            d.ok(f"Modul {mod}")
        except Exception as e:
            d.fail(f"Modul {mod} nicht importierbar ({type(e).__name__}: {e})",
                   "Mit `uvx --from <repo> duet-setup doctor` starten oder `pip install -e .`.")

    cfg = None
    try:
        dconfig = _config_mod()
        cfg_path = _config_path(args)
        cfg = dconfig.load_config(cfg_path)
        if cfg_path.exists():
            d.ok(f"Konfiguration lesbar: {cfg_path}")
            mode = cfg_path.stat().st_mode & 0o777
            if os.name == "posix" and mode & 0o077:
                d.warn(f"Konfiguration hat Rechte {oct(mode)}", f"chmod 600 {cfg_path}")
        else:
            d.warn(f"Keine Konfigurationsdatei ({cfg_path}) – nur Umgebungsvariablen",
                   "Lokal: `duet-setup init`. In der Cloud ist das normal.")
        if not cfg.telegram_token and not getattr(cfg, "ntfy_topic", ""):
            d.fail("Kein Kanal konfiguriert (weder Telegram noch ntfy)", "`duet-setup init` ausführen.")
        elif cfg.telegram_token and cfg.chat_id is None:
            d.fail("Telegram-Token, aber keine chat_id gekoppelt", "`duet-setup init` ausführen.")
    except Exception as e:
        d.fail(f"Konfiguration nicht lesbar: {e}", "Datei prüfen oder `duet-setup init` erneut ausführen.")

    if cfg is not None and cfg.telegram_token:
        try:
            me = _telegram_get_me(cfg.telegram_token)
            d.ok(f"Telegram-Bot erreichbar: @{me.get('username')}")
        except Exception as e:
            d.fail(f"Telegram getMe fehlgeschlagen: {e}", "Token (BotFather) und Netzwerk prüfen.")

    try:
        sd = _state_dir()
        sd.mkdir(parents=True, exist_ok=True)
        probe = sd / ".duet-doctor"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        d.ok(f"Zustandsordner beschreibbar: {sd}")
    except Exception as e:
        d.fail(f"Zustandsordner nicht beschreibbar ({e})", "DUET_STATE_DIR bzw. Rechte prüfen.")

    if claude:
        try:
            r = _run([claude, "plugin", "list"], timeout=60)
            if r.returncode == 0 and "duet@" in r.stdout:
                d.ok("Plugin duet ist installiert")
            elif r.returncode == 0:
                d.warn("Plugin duet ist lokal nicht installiert (für Cloud-Routinen nicht nötig)",
                       "In Claude Code: /plugin marketplace add stroexd/duet, dann /plugin install duet@duet-lokal")
        except Exception:
            pass
        try:
            r = _run([claude, "mcp", "list"], timeout=90)
            out = (r.stdout + r.stderr).strip()
            if r.returncode == 0:
                d.ok("`claude mcp list` (Konnektoren):")
                for line in (out or "(keine MCP-Server konfiguriert)").splitlines():
                    print(f"      {line}")
            else:
                d.warn("`claude mcp list` fehlgeschlagen", out[:300])
        except Exception as e:
            d.warn(f"`claude mcp list` fehlgeschlagen ({type(e).__name__})")

    print()
    if d.failed:
        print(f"{FAIL} {d.failed} Problem(e) gefunden.")
        return 1
    print(f"{OK} Alles in Ordnung.")
    return 0


# --------------------------------------------------------------------------- Parser


def _version() -> str:
    try:
        from duet import __version__

        return __version__
    except Exception:
        return "unbekannt"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="duet-setup",
        description="Einrichtung und Diagnose für Duet (Claude-Code-Plugin mit Telegram-Rückkanal).",
    )
    p.add_argument("--version", action="version", version=f"duet {_version()}")
    p.add_argument("--config", help="Pfad zur config.toml (Standard: Config-Ordner bzw. $DUET_CONFIG)")
    sub = p.add_subparsers(dest="command", metavar="KOMMANDO")
    sub.required = True

    s = sub.add_parser("init", help="Bot prüfen, koppeln, Werte für die Cloud-Umgebung ausgeben")
    s.add_argument("--token", help="Telegram-Bot-Token (sonst Abfrage bzw. $DUET_TELEGRAM_TOKEN)")
    s.add_argument("--chat-id", type=int, dest="chat_id", help="chat_id direkt setzen (keine Kopplung)")
    s.add_argument("--timezone", help=f"Zeitzone (Standard {DEFAULT_TZ})")
    s.add_argument("--approval-timeout", type=int, dest="approval_timeout",
                   help=f"Minuten, die auf eine Freigabe gewartet wird (Standard {DEFAULT_APPROVAL_TIMEOUT_MIN})")
    s.add_argument("--ntfy-topic", dest="ntfy_topic", help="Optionaler ntfy.sh-Kanal als Push-Fallback")
    s.add_argument("--no-pair", action="store_true", dest="no_pair", help="Kopplung überspringen")
    s.add_argument("--no-save", action="store_true", dest="no_save",
                   help="Keine lokale config.toml schreiben (nur Werte ausgeben)")
    s.add_argument("--reuse", action="store_true", help="Token aus vorhandener Konfiguration übernehmen")
    s.add_argument("-y", "--yes", action="store_true", help="Keine Rückfragen, Standardwerte nutzen")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("status", help="Konfiguration und Zustand anzeigen")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("doctor", help="Lokale Installation prüfen")
    s.set_defaults(func=cmd_doctor)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.config:
        os.environ["DUET_CONFIG"] = str(Path(args.config).expanduser())
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
        return 130
    except ModuleNotFoundError as e:
        print(f"{FAIL} Modul fehlt: {e.name}. Installation unvollständig – `duet-setup doctor` ausführen.",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
