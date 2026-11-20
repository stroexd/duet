from duet.approvals import APPROVED, DENIED, TIMEOUT, TelegramBridge
from duet.store import Store
from fake_telegram import CHAT, FakeTelegram

SEND = "mcp__gmail__send_email"
INPUT = {"to": "anna@example.org", "subject": "Hi", "body": "Hallo Anna"}


def make(tmp_path):
    fake = FakeTelegram()
    store = Store(tmp_path / "state")
    return fake, store, TelegramBridge(fake.client(), CHAT, store)


def _button_data(payload, idx):
    return payload["reply_markup"]["inline_keyboard"][0][idx]["callback_data"]


async def test_approve_creates_single_use_grant(tmp_path):
    fake, store, bridge = make(tmp_path)
    fake.on_send = lambda f, p: f.add_callback(_button_data(p, 0)) if "reply_markup" in p else None
    assert await bridge.request_approval(SEND, INPUT, "Anna wartet", timeout_s=5) == APPROVED
    msg = fake.sent()[0]
    assert msg["text"].startswith("Freigabe nötig") and "Anna wartet" in msg["text"]
    assert store.consume_grant(SEND, INPUT) and not store.consume_grant(SEND, INPUT)
    assert store.pending() == {}
    assert fake.sent("answerCallbackQuery") and "Freigegeben" in fake.sent("editMessageText")[0]["text"]


async def test_deny(tmp_path):
    fake, store, bridge = make(tmp_path)
    fake.on_send = lambda f, p: f.add_callback(_button_data(p, 1)) if "reply_markup" in p else None
    assert await bridge.request_approval(SEND, INPUT, "", timeout_s=5) == DENIED
    assert not store.consume_grant(SEND, INPUT) and store.pending() == {}


async def test_block_is_denied_without_message(tmp_path):
    fake, store, bridge = make(tmp_path)
    assert await bridge.request_approval("mcp__bank__transfer_money", {"amount": 100}, "", 5) == DENIED
    assert fake.sent() == []


async def test_timeout_stays_pending_and_late_answer_is_used(tmp_path):
    fake, store, bridge = make(tmp_path)
    assert await bridge.request_approval(SEND, INPUT, "", timeout_s=0) == TIMEOUT
    (rid, entry), = store.pending().items()
    assert entry["tool_name"] == SEND and entry["kind"] == "approval"
    # Max klickt zwischen zwei Läufen auf "Freigeben" (Button der ersten Nachricht)
    fake.add_callback(f"ap:{rid}:y")
    assert await bridge.request_approval(SEND, INPUT, "", timeout_s=0) == APPROVED
    assert len(fake.sent()) == 1  # nicht erneut gefragt
    assert store.consume_grant(SEND, INPUT)


async def test_timeout_twice_sends_reminder_with_same_id(tmp_path):
    fake, store, bridge = make(tmp_path)
    await bridge.request_approval(SEND, INPUT, "", timeout_s=0)
    await bridge.request_approval(SEND, INPUT, "", timeout_s=0)
    first, second = fake.sent()
    assert second["text"].startswith("Erinnerung")
    assert _button_data(first, 0) == _button_data(second, 0)
    assert len(store.pending()) == 1


async def test_foreign_chat_ignored(tmp_path):
    fake, store, bridge = make(tmp_path)
    await bridge.request_approval(SEND, INPUT, "", timeout_s=0)
    (rid, _), = store.pending().items()
    fake.add_callback(f"ap:{rid}:y", chat_id=999)
    fake.add_message("fremd", chat_id=999)
    assert await bridge.request_approval(SEND, INPUT, "", timeout_s=0) == TIMEOUT
    assert await bridge.inbox() == []


async def test_inbox_buffers_messages_once(tmp_path):
    fake, store, bridge = make(tmp_path)
    fake.add_message("Bitte Termin mit Anna verschieben", date=2_000_000_000)
    fake.add_message("uralt", date=1)
    msgs = await bridge.inbox(since_minutes=60 * 24 * 365 * 100)
    assert [m["text"] for m in msgs] == ["Bitte Termin mit Anna verschieben", "uralt"]
    assert await bridge.inbox() == []
    assert store.telegram()["offset"] == fake.next_update_id


async def test_ask_user_with_options(tmp_path):
    fake, store, bridge = make(tmp_path)
    fake.on_send = lambda f, p: f.add_callback(p["reply_markup"]["inline_keyboard"][1][0]["callback_data"])
    assert await bridge.ask_user("Welcher Tag?", ["Montag", "Dienstag"], timeout_s=5) == "Dienstag"
    assert store.pending() == {}


async def test_ask_user_free_text_reply(tmp_path):
    fake, store, bridge = make(tmp_path)
    fake.add_message("alte Nachricht", date=1)  # vor der Frage: landet in inbox, ist keine Antwort
    fake.on_send = lambda f, p: f.add_message("Um 15 Uhr", reply_to=1)
    assert await bridge.ask_user("Wann passt es?", None, timeout_s=5) == "Um 15 Uhr"
    assert [m["text"] for m in store.telegram()["inbox"]] == ["alte Nachricht"]


async def test_ask_user_timeout(tmp_path):
    fake, store, bridge = make(tmp_path)
    assert await bridge.ask_user("Hallo?", ["Ja"], timeout_s=0) == TIMEOUT
