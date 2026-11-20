import io
import json

import pytest

from duet import hook, store


@pytest.fixture
def env(tmp_path, monkeypatch):
    proj = tmp_path / "duet-home"
    (proj / "state").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    monkeypatch.setenv("DUET_STATE_DIR", str(proj / "state"))
    monkeypatch.delenv("DUET_UNATTENDED", raising=False)
    return proj


def run(payload, argv=None):
    out = io.StringIO()
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    assert hook.main(argv or [], stdin=io.StringIO(raw), stdout=out) == 0
    text = out.getvalue()
    return json.loads(text)["hookSpecificOutput"] if text else None


SEND = {"tool_name": "mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL",
        "tool_input": {"tools": [{"tool_slug": "GMAIL_SEND_EMAIL",
                                  "arguments": {"recipient_email": "anna@example.com", "subject": "Angebot"}}]}}


def test_auto_no_output(env):
    assert run({"tool_name": "Read", "tool_input": {"file_path": "/x"}}) is None
    assert run({"tool_name": "Write", "tool_input": {"file_path": str(env / "memory" / "a.md")}}) is None


def test_block_deny(env):
    o = run({"tool_name": "mcp__x__COINBASE_SEND_MONEY", "tool_input": {"amount": 5}})
    assert o["hookEventName"] == "PreToolUse" and o["permissionDecision"] == "deny"


def test_ask_interactive(env):
    o = run(SEND)
    assert o["permissionDecision"] == "ask" and "anna@example.com" in o["permissionDecisionReason"]


def test_ask_unattended_denies_with_hint(env, monkeypatch):
    monkeypatch.setenv("DUET_UNATTENDED", "1")
    o = run(SEND)
    assert o["permissionDecision"] == "deny" and "request_approval" in o["permissionDecisionReason"]


def test_one_time_grant_consumed(env, monkeypatch):
    monkeypatch.setenv("DUET_UNATTENDED", "1")
    store.Store().add_grant(SEND["tool_name"], SEND["tool_input"])
    assert run(SEND)["permissionDecision"] == "allow"
    assert run(SEND)["permissionDecision"] == "deny"  # nur einmal


def test_grant_for_other_input_does_not_match(env, monkeypatch):
    monkeypatch.setenv("DUET_UNATTENDED", "1")
    other = json.loads(json.dumps(SEND["tool_input"]))
    other["tools"][0]["arguments"]["recipient_email"] = "boese@example.com"
    store.Store().add_grant(SEND["tool_name"], other)
    assert run(SEND)["permissionDecision"] == "deny"


def test_grant_never_overrides_block(env):
    t = {"tool_name": "Bash", "tool_input": {"command": "rm -rf /"}}
    store.Store().add_grant(t["tool_name"], t["tool_input"])
    assert run(t)["permissionDecision"] == "deny"


def test_duet_own_tools_auto(env):
    assert run({"tool_name": "mcp__duet__notify", "tool_input": {"text": "hi"}}) is None
    assert run({"tool_name": "mcp__plugin_duet_duet__request_approval", "tool_input": {}}) is None


def test_state_persist_command_auto_but_state_write_protected(env):
    assert run({"tool_name": "Bash", "tool_input": {"command": 'git add state && git commit -m "duet: state" && git push'}}) is None
    o = run({"tool_name": "Write", "tool_input": {"file_path": str(env / "state" / "approvals.json")}})
    assert o["permissionDecision"] == "ask"


@pytest.mark.parametrize("raw", ["", "kein json", "[]", '{"tool_input": {}}'])
def test_garbage_fail_closed(env, monkeypatch, raw):
    assert run(raw)["permissionDecision"] == "ask"
    monkeypatch.setenv("DUET_UNATTENDED", "1")
    assert run(raw)["permissionDecision"] == "deny"


def test_unknown_args_tolerated(env):
    assert run(SEND, ["--source", "project", "--foo", "bar", "-x"])["permissionDecision"] == "ask"


def _write_project_hook(proj):
    (proj / ".claude").mkdir(exist_ok=True)
    (proj / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [
        {"matcher": ".*", "hooks": [{"type": "command",
                                     "command": "uvx --from git+https://github.com/stroexd/duet duet-hook --source project"}]}]}}))


def test_plugin_source_silent_when_project_hook_exists(env, monkeypatch):
    _write_project_hook(env)
    monkeypatch.setenv("DUET_UNATTENDED", "1")
    store.Store().add_grant(SEND["tool_name"], SEND["tool_input"])
    assert run(SEND, ["--source", "plugin"]) is None          # Plugin-Hook stumm, verbraucht nichts
    assert run(SEND, ["--source=project"])["permissionDecision"] == "allow"  # Projekt-Hook nutzt Freigabe


def test_plugin_source_silent_with_exec_form_hook(env):
    (env / ".claude").mkdir()
    (env / ".claude" / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [{"matcher": ".*", "hooks": [
        {"type": "command", "command": "uvx",
         "args": ["--from", "git+https://github.com/stroexd/duet", "duet-hook", "--source", "project"]}]}]}}))
    assert run(SEND, ["--source", "plugin"]) is None


def test_plugin_source_active_without_project_hook(env):
    assert run(SEND, ["--source", "plugin"])["permissionDecision"] == "ask"
    (env / ".claude").mkdir()
    (env / ".claude" / "settings.json").write_text("{kaputt")
    assert run(SEND, ["--source", "plugin"])["permissionDecision"] == "ask"


def test_relative_state_dir_resolved_against_project(env, monkeypatch, tmp_path):
    monkeypatch.setenv("DUET_STATE_DIR", "state")
    monkeypatch.chdir(tmp_path)  # Hook-CWD weicht von der Projektwurzel ab
    store.Store(env / "state").add_grant(SEND["tool_name"], SEND["tool_input"])
    assert run(SEND)["permissionDecision"] == "allow"


def test_store_failure_never_allows(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("kaputt")
    monkeypatch.setattr(store.Store, "consume_grant", boom)
    assert run(SEND)["permissionDecision"] == "ask"
