"""Fake-Telegram-Bot-API für Tests (httpx.MockTransport)."""

from __future__ import annotations

import json
from typing import Any

import httpx

from duet.telegram import TelegramClient

CHAT = 4242


class FakeTelegram:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.updates: list[dict[str, Any]] = []
        self.next_update_id = 100
        self.next_message_id = 1
        self.on_send = None  # optional: callback(fake, payload) nach sendMessage

    # Hilfen zum Einspeisen
    def add_message(self, text: str, chat_id: int = CHAT, date: int = 2_000_000_000, reply_to: int | None = None) -> None:
        msg: dict[str, Any] = {"message_id": 900 + self.next_update_id, "chat": {"id": chat_id}, "date": date,
                               "text": text}
        if reply_to:
            msg["reply_to_message"] = {"message_id": reply_to}
        self._add({"message": msg})

    def add_callback(self, data: str, chat_id: int = CHAT) -> None:
        self._add({"callback_query": {"id": f"cb{self.next_update_id}", "data": data,
                                      "message": {"message_id": 1, "chat": {"id": chat_id}}}})

    def _add(self, body: dict[str, Any]) -> None:
        self.updates.append({"update_id": self.next_update_id, **body})
        self.next_update_id += 1

    def sent(self, method: str = "sendMessage") -> list[dict[str, Any]]:
        return [p for m, p in self.calls if m == method]

    def handler(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1]
        payload = json.loads(request.content or b"{}")
        self.calls.append((method, payload))
        result: Any = True
        if method == "getUpdates":
            offset = payload.get("offset")
            if offset is not None:
                self.updates = [u for u in self.updates if u["update_id"] >= offset]
            result = list(self.updates)
        elif method == "sendMessage":
            result = {"message_id": self.next_message_id, "chat": {"id": payload["chat_id"]}}
            self.next_message_id += 1
            if self.on_send:
                self.on_send(self, payload)
        elif method == "getMe":
            result = {"id": 1, "username": "duet_test_bot"}
        elif method == "fail":
            return httpx.Response(200, json={"ok": False, "description": "Bad Request: kaputt"})
        return httpx.Response(200, json={"ok": True, "result": result})

    def client(self) -> TelegramClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handler))
        return TelegramClient("TEST:TOKEN", http=http)
