import httpx
import pytest

from duet.telegram import MAX_LEN, TelegramClient, TelegramError, split_text
from fake_telegram import CHAT, FakeTelegram


def test_split_text():
    assert split_text("hallo") == ["hallo"]
    long = ("wort " * 2000).strip()
    parts = split_text(long)
    assert len(parts) > 1 and all(len(p) <= MAX_LEN for p in parts)
    assert " ".join(parts) == long
    assert all(len(p) <= MAX_LEN for p in split_text("x" * 9000))


async def test_send_message_plain_with_buttons():
    fake = FakeTelegram()
    tg = fake.client()
    mid = await tg.send_message(CHAT, "Hallo", buttons=[[("Ja", "ap:1:y"), ("Nein", "ap:1:n")]])
    assert mid == 1
    payload = fake.sent()[0]
    assert payload["text"] == "Hallo" and "parse_mode" not in payload
    assert payload["reply_markup"]["inline_keyboard"][0][1] == {"text": "Nein", "callback_data": "ap:1:n"}


async def test_long_message_split_buttons_on_last():
    fake = FakeTelegram()
    mid = await fake.client().send_message(CHAT, "a " * 5000, buttons=[[("OK", "x")]])
    sent = fake.sent()
    assert len(sent) == 3 and mid == 3
    assert "reply_markup" not in sent[0] and "reply_markup" in sent[-1]


async def test_get_updates_and_other_methods():
    fake = FakeTelegram()
    fake.add_message("hi")
    tg = fake.client()
    ups = await tg.get_updates(None, timeout=0)
    assert ups[0]["message"]["text"] == "hi"
    assert await tg.get_updates(ups[-1]["update_id"] + 1, timeout=0) == []
    await tg.edit_message(CHAT, 5, "neu")
    await tg.answer_callback("cb1", "ok")
    await tg.send_chat_action(CHAT)
    assert (await tg.get_me())["username"] == "duet_test_bot"
    methods = [m for m, _ in fake.calls]
    assert methods[-4:] == ["editMessageText", "answerCallbackQuery", "sendChatAction", "getMe"]
    assert fake.calls[0][1]["allowed_updates"] == ["message", "callback_query"]
    await tg.close()


async def test_api_error_raises_without_token():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})

    tg = TelegramClient("SECRET:TOKEN", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(TelegramError) as exc:
        await tg.send_message(1, "x")
    assert "chat not found" in str(exc.value) and "SECRET" not in str(exc.value)


async def test_edit_not_modified_is_ignored():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"ok": False, "description": "Bad Request: message is not modified"})

    tg = TelegramClient("t", http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    await tg.edit_message(1, 1, "x")
