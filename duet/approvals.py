"""Telegram-Rückkanal für MCP-Tools: Freigaben, Rückfragen, Inbox.

Es gibt keinen Dauerprozess: Antworten (Button-Klicks, Nachrichten) werden per getUpdates im
laufenden Tool abgeholt. Was ankommt, während niemand wartet, landet im Store (answers/inbox) und
wird beim nächsten Lauf ausgewertet. Freigaben, die nicht rechtzeitig beantwortet werden, bleiben
in pending.json offen und werden beim nächsten Aufruf erneut vorgelegt.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from typing import Any

from duet.policy import Tier, classify
from duet.store import Store, approval_key
from duet.telegram import TelegramClient

try:  # Geheimnisse in Nachrichten/Store maskieren, falls policy das anbietet
    from duet.policy import mask as _mask
except ImportError:  # pragma: no cover
    def _mask(value: Any, key: str = "") -> Any:
        return value

log = logging.getLogger(__name__)

APPROVED, DENIED, TIMEOUT = "approved", "denied", "timeout"
_DETAIL_LIMIT = 1500
_POLL_MAX_S = 25


def _details(tool_name: str, tool_input: dict) -> str:
    raw = json.dumps(_mask(tool_input), ensure_ascii=False, indent=1, default=str)
    if len(raw) > _DETAIL_LIMIT:
        raw = raw[:_DETAIL_LIMIT] + " …"
    return f"Tool: {tool_name}\n{raw}"


class TelegramBridge:
    def __init__(self, tg: TelegramClient, chat_id: int, store: Store) -> None:
        self.tg = tg
        self.chat_id = chat_id
        self.store = store

    # ---- Updates abholen ------------------------------------------------------------------
    async def sync(self, wait_s: int = 0) -> int:
        """Holt Updates (Long-Poll bis wait_s), bestätigt sie und legt sie im Store ab."""
        data = self.store.telegram()
        try:
            updates = await self.tg.get_updates(data["offset"], timeout=wait_s)
        except Exception as exc:
            log.warning("getUpdates fehlgeschlagen: %s", exc)
            if wait_s:
                await asyncio.sleep(min(wait_s, 3))
            return 0
        if not updates:
            return 0
        pending = self.store.pending()
        edits: list[tuple[int, str]] = []
        callbacks: list[tuple[str, str]] = []
        for upd in updates:
            data["offset"] = int(upd["update_id"]) + 1
            if cb := upd.get("callback_query"):
                chat = ((cb.get("message") or {}).get("chat") or {}).get("id")
                if chat != self.chat_id:
                    log.warning("Callback aus fremdem Chat ignoriert")
                    continue
                note = self._handle_callback(cb.get("data") or "", data, pending, edits)
                callbacks.append((cb["id"], note))
            elif msg := upd.get("message"):
                if (msg.get("chat") or {}).get("id") != self.chat_id:
                    log.warning("Nachricht aus fremdem Chat ignoriert")
                    continue
                data["inbox"].append({
                    "update_id": upd["update_id"],
                    "message_id": msg.get("message_id"),
                    "date": msg.get("date", int(time.time())),
                    "text": msg.get("text") or msg.get("caption") or "(kein Text)",
                    "reply_to": (msg.get("reply_to_message") or {}).get("message_id"),
                })
        self.store.save_telegram(data)
        for cb_id, note in callbacks:
            try:
                await self.tg.answer_callback(cb_id, note)
            except Exception:
                log.debug("answerCallbackQuery fehlgeschlagen", exc_info=True)
        for message_id, text in edits:
            try:
                await self.tg.edit_message(self.chat_id, message_id, text)
            except Exception:
                log.debug("editMessageText fehlgeschlagen", exc_info=True)
        return len(updates)

    @staticmethod
    def _handle_callback(
        cb_data: str, data: dict[str, Any], pending: dict[str, dict[str, Any]], edits: list[tuple[int, str]]
    ) -> str:
        parts = cb_data.split(":", 2)
        if len(parts) != 3 or parts[0] not in ("ap", "q"):
            return "Unbekannt"
        kind, rid, value = parts
        entry = pending.get(rid, {})
        if kind == "ap":
            if value not in ("y", "n"):
                return "Unbekannt"
            data["answers"][rid] = value
            outcome = "Freigegeben" if value == "y" else "Abgelehnt"
            head = entry.get("head", "Freigabe")
            for mid in entry.get("message_ids", []):
                edits.append((mid, f"{head}\n\n-> {outcome}"))
            return outcome
        options = entry.get("options") or []
        try:
            answer = options[int(value)]
        except (ValueError, IndexError):
            return "Unbekannt"
        data["answers"][rid] = answer
        for mid in entry.get("message_ids", []):
            edits.append((mid, f"{entry.get('question', 'Frage')}\n\n-> {answer}"))
        return "Gespeichert"

    # ---- Warten -------------------------------------------------------------------------------
    async def wait_answer(self, rid: str, timeout_s: float) -> str | None:
        deadline = time.monotonic() + max(0.0, timeout_s)
        first = True
        while True:
            if (answer := self.store.pop_answer(rid)) is not None:
                return answer
            if (answer := self._free_text_answer(rid)) is not None:
                return answer
            remaining = deadline - time.monotonic()
            if remaining <= 0 and not first:
                return None
            first = False
            await self.sync(int(min(max(remaining, 0), _POLL_MAX_S)))

    def _free_text_answer(self, rid: str) -> str | None:
        """Freitext-Antwort auf eine Frage ohne Optionen: Antwort per Reply oder nächste Nachricht."""
        entry = self.store.pending().get(rid)
        if not entry or entry.get("kind") != "question" or entry.get("options"):
            return None
        data = self.store.telegram()
        ids = set(entry.get("message_ids", []))
        candidates = [m for m in data["inbox"] if m.get("reply_to") in ids] or [
            m for m in data["inbox"] if m.get("date", 0) >= entry.get("created", 0)
        ]
        if not candidates:
            return None
        msg = candidates[0]
        data["inbox"] = [m for m in data["inbox"] if m is not msg and m != msg]
        self.store.save_telegram(data)
        return str(msg.get("text", ""))

    # ---- Freigaben ------------------------------------------------------------------------------
    async def request_approval(self, tool_name: str, tool_input: dict, why: str, timeout_s: float) -> str:
        decision = classify(tool_name, tool_input)
        if decision.tier is Tier.BLOCK:
            log.info("Freigabe für %s sofort abgelehnt (BLOCK): %s", tool_name, decision.reason)
            return DENIED
        key = approval_key(tool_name, tool_input)
        await self.sync(0)
        rid = self.store.find_pending(key)
        if rid and (answer := self.store.pop_answer(rid)) is not None:
            return self._resolve(rid, answer, tool_name, tool_input)
        head = f"Freigabe nötig: {decision.summary}"
        text = f"{head}\n\nWarum: {why}\n\n{_details(tool_name, tool_input)}" if why else \
            f"{head}\n\n{_details(tool_name, tool_input)}"
        entry: dict[str, Any] = self.store.pending().get(rid or "", {})
        if rid:
            text = "Erinnerung – noch offen:\n" + text
        else:
            rid = secrets.token_hex(4)
            entry = {"kind": "approval", "key": key, "tool_name": tool_name, "tool_input": _mask(tool_input),
                     "why": why, "summary": decision.summary, "head": head, "created": int(time.time()),
                     "message_ids": []}
        buttons = [[("Freigeben", f"ap:{rid}:y"), ("Ablehnen", f"ap:{rid}:n")]]
        try:
            message_id = await self.tg.send_message(self.chat_id, text, buttons=buttons)
        except Exception:
            log.exception("Freigabe-Nachricht konnte nicht gesendet werden")
            return DENIED
        entry["message_ids"] = [*entry.get("message_ids", []), message_id][-5:]
        self.store.put_pending(rid, entry)
        answer = await self.wait_answer(rid, timeout_s)
        if answer is None:
            log.info("Freigabe %s für %s: Timeout, bleibt offen", rid, tool_name)
            return TIMEOUT
        return self._resolve(rid, answer, tool_name, tool_input)

    def _resolve(self, rid: str, answer: str, tool_name: str, tool_input: dict) -> str:
        self.store.drop_pending(rid)
        if answer == "y":
            self.store.add_grant(tool_name, tool_input)
            log.info("Freigabe %s für %s erteilt", rid, tool_name)
            return APPROVED
        log.info("Freigabe %s für %s abgelehnt", rid, tool_name)
        return DENIED

    # ---- Rückfragen -------------------------------------------------------------------------------
    async def ask_user(self, question: str, options: list[str] | None, timeout_s: float) -> str:
        rid = secrets.token_hex(4)
        opts = [str(o)[:60] for o in (options or [])][:8]
        buttons = [[(o, f"q:{rid}:{i}")] for i, o in enumerate(opts)] or None
        text = question if opts else f"{question}\n\n(Antworte einfach auf diese Nachricht.)"
        await self.sync(0)  # ältere Nachrichten nicht als Antwort werten
        message_id = await self.tg.send_message(self.chat_id, text, buttons=buttons)
        self.store.put_pending(rid, {"kind": "question", "question": question, "options": opts,
                                     "created": int(time.time()), "message_ids": [message_id]})
        answer = await self.wait_answer(rid, timeout_s)
        if answer is None:
            return TIMEOUT
        self.store.drop_pending(rid)
        return answer

    # ---- Inbox --------------------------------------------------------------------------------------
    async def inbox(self, since_minutes: int = 1440) -> list[dict[str, Any]]:
        while await self.sync(0):
            pass
        since = time.time() - since_minutes * 60
        return [{"date": m.get("date"), "text": m.get("text")} for m in self.store.take_inbox(since)]
