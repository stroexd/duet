"""End-to-End-Smoke-Test ohne echtes Telegram.

Baut das Paket frisch in eine venv (uv), startet `duet-mcp` per stdio über einen echten MCP-Client
(ClientSession + stdio_client) und lässt ihn gegen einen lokalen Fake-Telegram-HTTP-Server laufen
(DUET_TELEGRAM_API). Danach wird `duet-hook --source project` unbeaufsichtigt mit genau dem
freigegebenen Aufruf gefüttert: erst allow (Freigabe verbraucht), dann deny.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.slow

ROOT = Path(__file__).resolve().parents[1]
TOKEN = "123456:E2E-TEST-TOKEN"
CHAT = 4242
BIN = "Scripts" if sys.platform == "win32" else "bin"


# ---------------------------------------------------------------------------- Fake-Telegram (HTTP)
class FakeBot:
    """Minimaler Bot-API-Server: getUpdates/sendMessage/editMessageText/answerCallbackQuery.
    Mit auto_click "drückt" Max sofort den ersten Inline-Button jeder Nachricht."""

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.updates: list[dict[str, Any]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.next_update = 500
        self.next_msg = 1
        self.auto_click = True
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def add_update(self, body: dict[str, Any]) -> None:
        with self.cond:
            self.updates.append({"update_id": self.next_update, **body})
            self.next_update += 1
            self.cond.notify_all()

    def add_message(self, text: str) -> None:
        self.add_update({"message": {"message_id": 9000 + self.next_update, "chat": {"id": CHAT},
                                     "date": 2_000_000_000, "text": text}})

    def sent(self, method: str = "sendMessage") -> list[dict[str, Any]]:
        return [p for m, p in self.calls if m == method]

    def handle(self, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        prefix = f"/bot{TOKEN}/"
        if not path.startswith(prefix):
            return 401, {"ok": False, "description": "Unauthorized"}
        method = path[len(prefix):]
        with self.cond:
            self.calls.append((method, payload))
        if method == "getUpdates":
            offset = payload.get("offset")
            wait = min(float(payload.get("timeout") or 0), 1.0)
            with self.cond:
                if offset is not None:
                    self.updates = [u for u in self.updates if u["update_id"] >= offset]
                if not self.updates and wait:
                    self.cond.wait(wait)
                return 200, {"ok": True, "result": list(self.updates)}
        if method == "sendMessage":
            with self.cond:
                mid = self.next_msg
                self.next_msg += 1
            kb = (payload.get("reply_markup") or {}).get("inline_keyboard")
            if kb and self.auto_click:
                self.add_update({"callback_query": {
                    "id": f"cb{mid}", "data": kb[0][0]["callback_data"],
                    "message": {"message_id": mid, "chat": {"id": CHAT}}}})
            return 200, {"ok": True, "result": {"message_id": mid, "chat": {"id": payload["chat_id"]}}}
        if method == "getMe":
            return 200, {"ok": True, "result": {"id": 1, "username": "duet_e2e_bot"}}
        return 200, {"ok": True, "result": True}

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        bot = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                n = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(n) or b"{}")
                status, body = bot.handle(self.path, payload)
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *args: Any) -> None:
                pass

        return H

    def __enter__(self) -> "FakeBot":
        self.thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.server.shutdown()
        self.server.server_close()


# ---------------------------------------------------------------------------- Fixtures
@pytest.fixture(scope="session")
def venv_bin(tmp_path_factory) -> Path:
    """Frisch installiertes Paket (wie `uvx --from <repo>`), damit Entry-Points echt getestet werden."""
    uv = shutil.which("uv")
    if not uv:
        pytest.skip("uv nicht installiert")
    venv = tmp_path_factory.mktemp("e2e") / "venv"
    subprocess.run([uv, "venv", "-q", str(venv)], check=True)
    py = venv / BIN / ("python.exe" if sys.platform == "win32" else "python")
    r = subprocess.run([uv, "pip", "install", "-q", "--python", str(py), str(ROOT)], capture_output=True, text=True)
    if r.returncode:
        pytest.skip(f"Installation fehlgeschlagen (offline?): {r.stderr[-300:]}")
    return venv / BIN


def _env(tmp: Path, bot: FakeBot) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("DUET_")}
    env.update({
        "DUET_TELEGRAM_API": bot.url,
        "DUET_TELEGRAM_TOKEN": TOKEN,
        "DUET_CHAT_ID": str(CHAT),
        "DUET_STATE_DIR": str(tmp / "state"),
        "DUET_HOME": str(tmp / "home"),
        "DUET_APPROVAL_TIMEOUT_MIN": "1",
        "NO_PROXY": "127.0.0.1,localhost",
        "no_proxy": "127.0.0.1,localhost",
    })
    return env


def _value(result: Any) -> Any:
    assert not getattr(result, "is_error", getattr(result, "isError", False)), result
    sc = getattr(result, "structured_content", getattr(result, "structuredContent", None))
    if isinstance(sc, dict) and set(sc) == {"result"}:
        return sc["result"]
    texts = [c.text for c in result.content if getattr(c, "type", "") == "text"]
    if len(texts) == 1:
        try:
            return json.loads(texts[0])
        except ValueError:
            return texts[0]
    return [json.loads(t) for t in texts]


# ---------------------------------------------------------------------------- Test
async def test_mcp_stdio_and_hook_end_to_end(tmp_path, venv_bin):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    tool_name = "mcp__github__merge_pull_request"
    tool_input = {"owner": "stroexd", "repo": "duet", "pullNumber": 7, "merge_method": "squash"}

    with FakeBot() as bot:
        bot.add_message("Bitte schau nach meinem Paket")
        env = _env(tmp_path, bot)
        params = StdioServerParameters(command=str(venv_bin / "duet-mcp"), args=[], env=env, cwd=str(tmp_path))
        errlog = (tmp_path / "server-stderr.log").open("w", encoding="utf-8")
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                assert {"notify", "inbox", "seen", "mark_seen", "ask_user", "request_approval",
                        "pending_approvals", "status"} <= names

                assert str(_value(await session.call_tool("notify", {"text": "Hallo Max"}))) == "1"
                assert bot.sent()[0]["chat_id"] == CHAT and bot.sent()[0]["text"] == "Hallo Max"

                inbox = _value(await session.call_tool("inbox", {}))
                inbox = inbox if isinstance(inbox, list) else [inbox]
                assert [m["text"] for m in inbox] == ["Bitte schau nach meinem Paket"]
                assert _value(await session.call_tool("inbox", {})) in ([], None, "[]")

                assert _value(await session.call_tool("seen", {"keys": ["pr:stroexd/duet#7"]})) in ([], None, "[]")
                assert _value(await session.call_tool("mark_seen", {"keys": ["pr:stroexd/duet#7"]})) == "ok"
                assert _value(await session.call_tool("seen", {"keys": ["pr:stroexd/duet#7", "x"]})) \
                    in (["pr:stroexd/duet#7"], "pr:stroexd/duet#7")

                answer = _value(await session.call_tool(
                    "ask_user", {"question": "Paket abholen?", "options": ["Ja", "Nein"], "timeout_min": 0.5}))
                assert answer == "Ja"

                verdict = _value(await session.call_tool(
                    "request_approval", {"tool_name": tool_name, "tool_input": tool_input, "why": "PR ist grün"}))
                assert verdict == "approved"
                assert any(p["text"].startswith("Freigabe nötig") for p in bot.sent())
                assert bot.sent("answerCallbackQuery")  # Button-Klick wurde bestätigt
                assert _value(await session.call_tool("pending_approvals", {})) in ([], None, "[]")

                st = _value(await session.call_tool("status", {}))
                assert TOKEN not in json.dumps(st) and st["channel"] == "telegram"

    errlog.close()
    # Token darf weder in Server-Logs (httpx loggt URLs) noch im Zustand landen
    assert TOKEN not in (tmp_path / "server-stderr.log").read_text(encoding="utf-8")
    for f in (tmp_path / "state").iterdir():
        assert TOKEN not in f.read_text(encoding="utf-8"), f

    hook_env = {**env, "DUET_UNATTENDED": "1", "DUET_STATE_DIR": "state", "CLAUDE_PROJECT_DIR": str(tmp_path)}
    event = json.dumps({"hook_event_name": "PreToolUse", "tool_name": tool_name, "tool_input": tool_input,
                        "cwd": str(tmp_path)})

    def hook() -> dict[str, Any]:
        r = subprocess.run([str(venv_bin / "duet-hook"), "--source", "project"], input=event, env=hook_env,
                           cwd=str(tmp_path), capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        return json.loads(r.stdout)["hookSpecificOutput"]

    first = hook()
    assert first["permissionDecision"] == "allow", first
    second = hook()
    assert second["permissionDecision"] == "deny" and "request_approval" in second["permissionDecisionReason"]
