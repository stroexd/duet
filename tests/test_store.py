import json

import pytest
import time

from duet import store as s


def test_state_dir_resolution(tmp_path, monkeypatch):
    assert s.state_dir() == tmp_path / "home"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DUET_STATE_DIR", "state")
    assert s.state_dir() == (tmp_path / "state").resolve()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))
    assert s.state_dir() == (tmp_path / "proj" / "state").resolve()


def test_seen_and_mark_seen(tmp_path):
    st = s.Store(tmp_path)
    assert st.seen(["a", "b"]) == []
    st.mark_seen(["a"])
    assert st.seen(["a", "b"]) == ["a"]
    raw = (tmp_path / "seen.json").read_text()
    assert raw.endswith("\n") and json.loads(raw).keys() == {"a"}


def test_approval_key_canonical():
    assert s.approval_key("t", {"a": 1, "b": [1, 2]}) == s.approval_key("t", {"b": [1, 2], "a": 1})
    assert s.approval_key("t", {"a": 1}) != s.approval_key("u", {"a": 1})


def test_grant_is_single_use(tmp_path):
    st = s.Store(tmp_path)
    st.add_grant("mcp__gmail__send", {"to": "x"})
    assert not st.consume_grant("mcp__gmail__send", {"to": "y"})
    assert st.consume_grant("mcp__gmail__send", {"to": "x"})
    assert not st.consume_grant("mcp__gmail__send", {"to": "x"})


def test_grant_expires(tmp_path, monkeypatch):
    st = s.Store(tmp_path)
    st.add_grant("t", {}, ttl_s=1)
    monkeypatch.setattr(s, "_now", lambda: time.time() + 5)
    assert not st.consume_grant("t", {})


def test_consume_approval_uses_default_store(tmp_path, monkeypatch):
    monkeypatch.setenv("DUET_STATE_DIR", str(tmp_path / "st"))
    s.Store().add_grant("t", {"x": 1})
    assert s.consume_approval("t", {"x": 1})
    assert not s.consume_approval("t", {"x": 1})


def test_pending_inbox_answers(tmp_path):
    st = s.Store(tmp_path)
    st.put_pending("r1", {"key": "k1"})
    assert st.find_pending("k1") == "r1" and st.find_pending("k2") is None
    st.drop_pending("r1")
    assert st.pending() == {}
    data = st.telegram()
    data["inbox"] = [{"date": 10, "text": "alt"}, {"date": 100, "text": "neu"}]
    data["answers"]["r2"] = "y"
    st.save_telegram(data)
    assert st.pop_answer("r2") == "y" and st.pop_answer("r2") is None
    assert [m["text"] for m in st.take_inbox(since_ts=50)] == ["neu"]
    assert st.take_inbox() == []


def test_remember_recall(tmp_path):
    st = s.Store(tmp_path)
    assert st.recall() == ""
    st.remember("Max trinkt  Kaffee schwarz")
    st.remember("Anna ist Max' Schwester")
    assert "Kaffee schwarz" in st.recall("kaffee")
    assert "Anna" not in st.recall("kaffee")
    assert len(st.recall().splitlines()) == 2


def test_lock_timeout_and_stale(tmp_path, monkeypatch):
    import os
    import time

    st = s.Store(tmp_path)
    lock = tmp_path / ".approvals.json.lock"
    lock.write_text("123")
    with pytest.raises(TimeoutError):
        with st._lock("approvals.json", timeout_s=0.2):
            pass
    old = time.time() - s.LOCK_STALE_S - 5
    os.utime(lock, (old, old))
    st.add_grant("t", {"x": 1})  # verwaiste Sperre wird übernommen
    assert not lock.exists()
    assert st.consume_grant("t", {"x": 1})


def test_hook_fails_closed_when_locked(tmp_path, monkeypatch):
    st = s.Store(tmp_path)
    st.add_grant("mcp__github__merge_pull_request", {"x": 1})
    (tmp_path / ".approvals.json.lock").write_text("1")
    monkeypatch.setattr(s, "LOCK_TIMEOUT_S", 0.1)
    from duet import hook
    env = {"DUET_UNATTENDED": "1", "DUET_STATE_DIR": str(tmp_path)}
    monkeypatch.setenv("DUET_STATE_DIR", str(tmp_path))
    out = hook.decide({"tool_name": "mcp__github__merge_pull_request", "tool_input": {"x": 1}}, env)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"  # gesperrt => fail closed
    (tmp_path / ".approvals.json.lock").unlink()
    out = hook.decide({"tool_name": "mcp__github__merge_pull_request", "tool_input": {"x": 1}}, env)
    assert out["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_concurrent_grants_not_lost(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    st = s.Store(tmp_path)
    with ThreadPoolExecutor(8) as ex:
        list(ex.map(lambda i: s.Store(tmp_path).add_grant("t", {"i": i}), range(40)))
    assert all(st.consume_grant("t", {"i": i}) for i in range(40))
