"""Minimaler async Telegram-Bot-API-Client (Plaintext, kein parse_mode)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)

MAX_LEN = 4000


class TelegramError(RuntimeError):
    """Telegram-API meldet ok=false oder antwortet unlesbar."""


def split_text(text: str, limit: int = MAX_LEN) -> list[str]:
    """Teilt Text in Stücke <= limit, bevorzugt an Zeilenumbrüchen/Leerzeichen."""
    text = text or "(leer)"
    parts: list[str] = []
    while len(text) > limit:
        cut = text.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = text.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(text[:cut].rstrip())
        text = text[cut:].lstrip("\n ")
    if text:
        parts.append(text)
    return parts


class TelegramClient:
    def __init__(
        self,
        token: str,
        http: httpx.AsyncClient | None = None,
        base_url: str = "https://api.telegram.org",
    ) -> None:
        self._url = f"{base_url.rstrip('/')}/bot{token}"
        self._own_http = http is None
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0))

    async def _call(self, method: str, params: dict[str, Any], http_timeout: float | None = None) -> Any:
        payload = {k: v for k, v in params.items() if v is not None}
        kwargs: dict[str, Any] = {"json": payload}
        if http_timeout is not None:
            kwargs["timeout"] = http_timeout
        resp = await self._http.post(f"{self._url}/{method}", **kwargs)
        try:
            data = resp.json()
        except ValueError as exc:
            # Token steht in der URL: nie die URL loggen, nur Methode + Status
            raise TelegramError(f"{method}: HTTP {resp.status_code}, keine JSON-Antwort") from exc
        if not data.get("ok"):
            raise TelegramError(f"{method}: {data.get('description', resp.status_code)}")
        return data.get("result")

    async def get_updates(self, offset: int | None, timeout: int = 50) -> list[dict]:
        params = {"offset": offset, "timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        return await self._call("getUpdates", params, http_timeout=timeout + 15) or []

    async def send_message(
        self, chat_id: int, text: str, buttons: list[list[tuple[str, str]]] | None = None
    ) -> int:
        """Sendet Text (gesplittet bei >4000 Zeichen); Buttons hängen an der letzten Nachricht.
        Gibt die message_id der letzten Nachricht zurück."""
        parts = split_text(text)
        message_id = 0
        for i, part in enumerate(parts):
            params: dict[str, Any] = {"chat_id": chat_id, "text": part, "disable_web_page_preview": True}
            if buttons and i == len(parts) - 1:
                params["reply_markup"] = {
                    "inline_keyboard": [
                        [{"text": label, "callback_data": data} for label, data in row] for row in buttons
                    ]
                }
            result = await self._call("sendMessage", params)
            message_id = int(result["message_id"])
        return message_id

    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None:
        """Ersetzt den Text (entfernt damit auch Inline-Buttons)."""
        params = {"chat_id": chat_id, "message_id": message_id, "text": split_text(text)[0]}
        try:
            await self._call("editMessageText", params)
        except TelegramError as exc:
            if "not modified" not in str(exc):
                raise

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        await self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text or None})

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        await self._call("sendChatAction", {"chat_id": chat_id, "action": action})

    async def get_me(self) -> dict:
        """Zusatz (für `duet doctor`/`init`): Bot-Infos."""
        return await self._call("getMe", {})

    async def close(self) -> None:
        if self._own_http:
            await self._http.aclose()
