"""MCP-Server "duet": Rückkanal zum Handy (Telegram, sonst ntfy), Freigaben, Dedup, Notizen.

Start: `duet-mcp` (stdio, Standard) oder `duet-mcp --http PORT [--host 127.0.0.1]` (Streamable HTTP).
"""

from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from mcp.server.mcpserver import MCPServer

from duet import __version__
from duet.approvals import TelegramBridge
from duet.config import Config, load_config, telegram_api
from duet.store import Store
from duet.telegram import TelegramClient

log = logging.getLogger(__name__)

# Claude Code bricht MCP-Tool-Aufrufe nach dem "timeout" des Servers in .mcp.json ab (wir setzen 20 min;
# ohne Eintrag: MCP_TOOL_TIMEOUT bzw. ~28 h, stdio-Leerlauf-Abbruch nach 30 min). Wartezeiten daher deckeln.
MAX_WAIT_MIN = 15

INSTRUCTIONS = (
    "Duet: Rückkanal zu Max' Handy. notify für Meldungen, ask_user für Rückfragen, "
    "request_approval vor jeder Aktion mit Außenwirkung (senden, löschen, zusagen, bezahlen, veröffentlichen); "
    "nach 'approved' die Aktion mit exakt demselben tool_name/tool_input ausführen. "
    "inbox liefert Aufträge, die Max dem Bot geschickt hat. seen/mark_seen verhindern Doppelmeldungen."
)

server: MCPServer = MCPServer("duet", instructions=INSTRUCTIONS, version=__version__)


def _cfg() -> Config:
    return load_config()


def _bridge(cfg: Config) -> TelegramBridge:
    if cfg.channel != "telegram" or cfg.chat_id is None:
        raise RuntimeError("Telegram nicht konfiguriert (DUET_TELEGRAM_TOKEN und DUET_CHAT_ID setzen).")
    return TelegramBridge(TelegramClient(cfg.telegram_token, base_url=telegram_api()), cfg.chat_id, Store())


async def _ntfy(cfg: Config, text: str, urgent: bool) -> str:
    headers = {"Title": "Duet", "Priority": "high" if urgent else "default"}
    async with httpx.AsyncClient(timeout=20) as http:
        resp = await http.post(f"{cfg.ntfy_server.rstrip('/')}/{cfg.ntfy_topic}",
                               content=text.encode("utf-8"), headers=headers)
        resp.raise_for_status()
        return str(resp.json().get("id", ""))


# ---- Tools ---------------------------------------------------------------------------------------
@server.tool()
async def notify(text: str, urgent: bool = False) -> str:
    """Schickt eine Nachricht an Max' Handy (Telegram, sonst ntfy). Kurz, Klartext. Gibt die message_id zurück."""
    cfg = _cfg()
    body = f"DRINGEND: {text}" if urgent else text
    if cfg.channel == "telegram":
        bridge = _bridge(cfg)
        try:
            mid: str = str(await bridge.tg.send_message(cfg.chat_id, body))  # type: ignore[arg-type]
        finally:
            await bridge.tg.close()
    elif cfg.channel == "ntfy":
        mid = await _ntfy(cfg, text, urgent)
    else:
        raise RuntimeError("Kein Kanal konfiguriert (Telegram oder DUET_NTFY_TOPIC).")
    Store().set_last_notify({"at": int(time.time()), "channel": cfg.channel, "message_id": mid,
                             "preview": text[:80]})
    return mid


@server.tool()
async def ask_user(question: str, options: list[str] | None = None, timeout_min: float = 10) -> str:
    """Stellt Max eine Frage (mit Buttons, falls options), wartet höchstens timeout_min (max. 15).
    Antwort oder "timeout" (dann ohne Antwort weitermachen; eine spätere Antwort kommt über inbox)."""
    cfg = _cfg()
    bridge = _bridge(cfg)
    try:
        return await bridge.ask_user(question, options, max(0.0, min(timeout_min, MAX_WAIT_MIN)) * 60)
    finally:
        await bridge.tg.close()


@server.tool()
async def request_approval(tool_name: str, tool_input: dict[str, Any], why: str = "") -> str:
    """Holt Max' Freigabe per Telegram-Button für genau diesen Tool-Aufruf.
    Rückgabe "approved" (dann denselben Aufruf unverändert innerhalb von 15 min ausführen),
    "denied" (nicht ausführen, nicht anders versuchen) oder "timeout" (bleibt offen, nächster Lauf fragt erneut)."""
    cfg = _cfg()
    bridge = _bridge(cfg)
    try:
        wait_min = max(0, min(cfg.approval_timeout_min, MAX_WAIT_MIN))
        return await bridge.request_approval(tool_name, tool_input, why, wait_min * 60)
    finally:
        await bridge.tg.close()


@server.tool()
async def pending_approvals() -> list[dict[str, Any]]:
    """Offene Freigaben/Fragen aus früheren Läufen (tool_name, tool_input, why). Freigaben erneut mit
    request_approval vorlegen; eine inzwischen per Button gegebene Antwort wird dabei sofort übernommen."""
    cfg = _cfg()
    if cfg.channel == "telegram":
        bridge = _bridge(cfg)
        try:
            while await bridge.sync(0):
                pass
        finally:
            await bridge.tg.close()
    return [{"id": rid, **{k: v for k, v in e.items() if k in
                           ("kind", "tool_name", "tool_input", "why", "summary", "question", "created")}}
            for rid, e in Store().pending().items()]


@server.tool()
async def inbox(since_minutes: int = 1440) -> list[dict[str, Any]]:
    """Neue Nachrichten von Max an den Bot (Aufträge). Jede Nachricht wird nur einmal geliefert."""
    cfg = _cfg()
    bridge = _bridge(cfg)
    try:
        return await bridge.inbox(since_minutes)
    finally:
        await bridge.tg.close()


@server.tool()
def seen(keys: list[str]) -> list[str]:
    """Welche dieser Schlüssel (z. B. "mail:<id>", "pr:owner/repo#12") wurden schon gemeldet?"""
    return Store().seen(keys)


@server.tool()
def mark_seen(keys: list[str]) -> str:
    """Merkt Schlüssel als gemeldet (60 Tage)."""
    Store().mark_seen(keys)
    return "ok"


@server.tool()
def remember(note: str) -> str:
    """Speichert eine dauerhafte Notiz über Max (für Umgebungen ohne Dateizugriff)."""
    Store().remember(note)
    return "ok"


@server.tool()
def recall(query: str = "") -> str:
    """Liest Notizen; query filtert (alle Wörter müssen vorkommen)."""
    return Store().recall(query) or "(keine Notizen)"


@server.tool()
def status() -> dict[str, Any]:
    """Konfigurationszustand (Token maskiert), Kanal, letzte Meldung."""
    cfg = _cfg()
    store = Store()
    tok = cfg.telegram_token
    last = store.telegram().get("last_notify")
    try:
        now = datetime.now(ZoneInfo(cfg.timezone)).isoformat(timespec="minutes")
    except Exception:
        now = datetime.now().isoformat(timespec="minutes")
    return {
        "version": __version__,
        "channel": cfg.channel or "keiner",
        "telegram_token": f"{tok[:4]}…{tok[-3:]}" if len(tok) > 8 else ("gesetzt" if tok else "fehlt"),
        "chat_id": cfg.chat_id,
        "ntfy_topic": "gesetzt" if cfg.ntfy_topic else "",
        "timezone": cfg.timezone,
        "now": now,
        "approval_timeout_min": cfg.approval_timeout_min,
        "state_dir": str(store.dir),
        "pending": len(store.pending()),
        "last_notify": last,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="duet-mcp", description="Duet MCP-Server")
    parser.add_argument("--http", type=int, metavar="PORT", help="Streamable HTTP statt stdio")
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args(argv)
    # stdout gehört bei stdio dem Protokoll: nur stderr loggen
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    # httpx loggt auf INFO jede Request-URL – bei Telegram steht der Bot-Token darin
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    if args.http:
        server.run("streamable-http", host=args.host, port=args.http)
    else:
        server.run()


if __name__ == "__main__":
    main()
