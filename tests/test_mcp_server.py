import pytest

from duet import mcp_server as m
from fake_telegram import CHAT, FakeTelegram


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setenv("DUET_TELEGRAM_TOKEN", "123:ABCDEFGHIJ")
    monkeypatch.setenv("DUET_CHAT_ID", str(CHAT))
    monkeypatch.setenv("DUET_STATE_DIR", str(tmp_path / "state"))
    f = FakeTelegram()
    monkeypatch.setattr(m, "TelegramClient", lambda token, **kw: f.client())
    return f


async def test_tool_list_matches_spec():
    names = {t.name for t in await m.server.list_tools()}
    assert {"notify", "ask_user", "request_approval", "inbox", "seen", "mark_seen", "remember", "recall",
            "status"} <= names


async def test_notify_and_status(fake):
    assert await m.notify("Paket ist da") == "1"
    assert fake.sent()[0]["text"] == "Paket ist da"
    await m.notify("Termin in 30 min", urgent=True)
    assert fake.sent()[1]["text"].startswith("DRINGEND")
    st = m.status()
    assert st["channel"] == "telegram" and "ABCDEFGHIJ" not in st["telegram_token"]
    assert st["last_notify"]["preview"] == "Termin in 30 min"


async def test_notify_without_channel_fails():
    with pytest.raises(RuntimeError):
        await m.notify("x")


async def test_seen_mark_seen_remember_via_server(fake):
    assert m.seen(["pr:x#1"]) == []
    m.mark_seen(["pr:x#1"])
    res = await m.server.call_tool("seen", {"keys": ["pr:x#1", "pr:x#2"]})
    assert res.structured_content == {"result": ["pr:x#1"]}
    m.remember("Max mag kurze Mails")
    assert "kurze Mails" in m.recall("mails")


async def test_request_approval_and_pending(fake, monkeypatch):
    monkeypatch.setenv("DUET_APPROVAL_TIMEOUT_MIN", "0")
    assert await m.request_approval("mcp__gmail__send_email", {"to": "a"}, "x") == "timeout"
    pend = await m.pending_approvals()
    assert pend[0]["tool_name"] == "mcp__gmail__send_email"
    fake.add_message("Bitte X erledigen")
    assert [x["text"] for x in await m.inbox()] == ["Bitte X erledigen"]


def test_main_parses_http(monkeypatch):
    called = {}
    monkeypatch.setattr(m.server, "run", lambda *a, **k: called.update(args=a, kwargs=k))
    m.main(["--http", "8765"])
    assert called == {"args": ("streamable-http",), "kwargs": {"host": "127.0.0.1", "port": 8765}}
    m.main([])
    assert called["args"] == ()
