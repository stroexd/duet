"""Tests für duet.cli (duet-setup): Parser, Token-Maskierung, Kopplung, Cloud-Env, Plugin-Dateien."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from duet import cli

ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------- Parser

def test_parser_requires_command():
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([])


@pytest.mark.parametrize("cmd", ["init", "status", "doctor"])
def test_parser_commands(cmd):
    args = cli.build_parser().parse_args([cmd])
    assert args.command == cmd and callable(args.func)


@pytest.mark.parametrize("cmd", ["run", "connect", "connections"])
def test_removed_commands(cmd):
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args([cmd])


def test_parser_init_flags():
    args = cli.build_parser().parse_args(
        ["init", "--token", "123:abc", "--chat-id", "42", "--no-pair", "--timezone", "UTC",
         "--approval-timeout", "5", "--ntfy-topic", "t", "--no-save", "-y"])
    assert args.func is cli.cmd_init
    assert (args.token, args.chat_id, args.timezone) == ("123:abc", 42, "UTC")
    assert args.no_pair and args.no_save and args.yes
    assert args.approval_timeout == 5 and args.ntfy_topic == "t"


def test_parser_global_config():
    assert cli.build_parser().parse_args(["--config", "/x/c.toml", "status"]).config == "/x/c.toml"


# ---------------------------------------------------------------- Maskierung

def test_mask_token_telegram():
    tok = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
    masked = cli.mask_token(tok)
    assert masked.startswith("123456789:") and masked.endswith("Dsaw")
    assert "AAHdqTcv" not in masked


@pytest.mark.parametrize("tok,expected", [(None, "(nicht gesetzt)"), ("", "(nicht gesetzt)"),
                                          ("short", "*****"), ("1:abc", "1:********")])
def test_mask_token_edge_cases(tok, expected):
    assert cli.mask_token(tok) == expected


# ---------------------------------------------------------------- Cloud-Env

def test_cloud_env_values():
    env = dict(cli.cloud_env("1:tok", 42, "Europe/Berlin"))
    assert env == {"DUET_TELEGRAM_TOKEN": "1:tok", "DUET_CHAT_ID": "42", "DUET_TIMEZONE": "Europe/Berlin",
                   "DUET_UNATTENDED": "1", "DUET_STATE_DIR": "state", "DUET_APPROVAL_TIMEOUT_MIN": "10"}
    assert "DUET_NTFY_TOPIC" in dict(cli.cloud_env("1:t", 1, "UTC", ntfy_topic="x"))


def test_format_env():
    assert cli.format_env([("A", "1"), ("B", "x")]) == "A=1\nB=x"


def test_init_noninteractive_prints_env(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(cli, "_telegram_get_me", lambda t: {"username": "testbot"})
    monkeypatch.setattr(cli, "_send_plain", lambda *a: pytest.fail("darf ohne Kopplung nichts senden"))
    monkeypatch.delenv("DUET_TELEGRAM_TOKEN", raising=False)
    cfg = tmp_path / "c.toml"
    rc = cli.main(["--config", str(cfg), "init", "--token", "123:abcdefghijkl", "--chat-id", "42",
                   "--no-pair", "-y", "--timezone", "UTC"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "DUET_TELEGRAM_TOKEN=123:abcdefghijkl" in out
    assert "DUET_CHAT_ID=42" in out and "DUET_UNATTENDED=1" in out
    assert "api.telegram.org" in out
    assert cfg.exists()
    if hasattr(__import__("os"), "geteuid"):
        assert cfg.stat().st_mode & 0o077 == 0


def test_init_bad_token(monkeypatch, tmp_path):
    def boom(t):
        raise RuntimeError("Unauthorized")
    monkeypatch.setattr(cli, "_telegram_get_me", boom)
    assert cli.main(["--config", str(tmp_path / "c.toml"), "init", "--token", "x", "--no-pair", "-y"]) == 1


def test_status_masks_token(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DUET_TELEGRAM_TOKEN", "123456:SECRETSECRETabcd")
    monkeypatch.setenv("DUET_CHAT_ID", "7")
    monkeypatch.setenv("DUET_STATE_DIR", str(tmp_path / "state"))
    assert cli.main(["--config", str(tmp_path / "none.toml"), "status"]) == 0
    out = capsys.readouterr().out
    assert "SECRETSECRET" not in out and "123456:********abcd" in out


# ---------------------------------------------------------------- Kopplung

class _Resp:
    def __init__(self, data):
        self._d = data

    def json(self):
        return self._d


def test_pair_chat_accepts_only_exact_code_from_private_chat():
    responses = [
        _Resp({"ok": True, "result": [{"update_id": 10}]}),
        _Resp({"ok": True, "result": [
            {"update_id": 11, "message": {"text": "hallo", "chat": {"id": 1, "type": "private"}}},
            {"update_id": 12, "message": {"text": "123456", "chat": {"id": 2, "type": "group"}}},
            {"update_id": 13, "message": {"text": " 123456 ", "chat": {"id": 77, "type": "private"}}},
        ]}),
        _Resp({"ok": True, "result": []}),
    ]
    seen = []

    def fake_get(url, params=None, timeout=None):
        seen.append(params)
        return responses.pop(0)

    assert cli.pair_chat("t", "123456", timeout_s=60, http_get=fake_get) == 77
    assert seen[1]["offset"] == 11 and seen[2]["offset"] == 14


def test_pair_chat_timeout():
    t = [0.0]

    def clock():
        t[0] += 100
        return t[0]

    assert cli.pair_chat("t", "000000", timeout_s=150, clock=clock,
                         http_get=lambda *a, **k: _Resp({"ok": True, "result": []})) is None


# ---------------------------------------------------------------- Plugin-/Template-Dateien

def _json(rel):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def test_plugin_manifest_and_marketplace():
    assert _json(".claude-plugin/plugin.json")["name"] == "duet"
    mp = _json(".claude-plugin/marketplace.json")
    assert mp["name"] == "duet-lokal" and mp["owner"]["name"]
    assert mp["plugins"][0]["name"] == "duet" and mp["plugins"][0]["source"] == "./"


def test_plugin_mcp_and_hook_use_uvx():
    srv = _json(".mcp.json")["mcpServers"]["duet"]
    assert srv["command"] == "uvx" and srv["args"] == ["--from", "${CLAUDE_PLUGIN_ROOT}", "duet-mcp"]
    entry = _json("hooks/hooks.json")["hooks"]["PreToolUse"][0]
    assert entry["matcher"] == ".*"
    h = entry["hooks"][0]
    assert h["command"] == "uvx" and h["args"][:3] == ["--from", "${CLAUDE_PLUGIN_ROOT}", "duet-hook"]
    assert "--source" in h["args"] and h["args"][-1] == "plugin"


def test_template_settings():
    s = _json("template-duet-home/.claude/settings.json")
    assert s["enabledPlugins"] == {"duet@duet-lokal": True}
    assert s["extraKnownMarketplaces"]["duet-lokal"]["source"] == {"source": "github", "repo": "stroexd/duet"}
    h = s["hooks"]["PreToolUse"][0]["hooks"][0]
    assert h["args"][-2:] == ["--source", "project"] and "duet-hook" in h["args"]
    srv = _json("template-duet-home/.mcp.json")["mcpServers"]["duet"]
    assert srv["args"] == ["--from", "git+https://github.com/stroexd/duet", "duet-mcp"]
    assert (ROOT / "template-duet-home/state/.gitkeep").exists()


def test_pyproject_entry_points():
    import tomllib
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["scripts"] == {"duet-mcp": "duet.mcp_server:main", "duet-hook": "duet.hook:main",
                                          "duet-setup": "duet.cli:main"}
    deps = " ".join(data["project"]["dependencies"])
    assert "mcp>=2.3" in deps and "httpx" in deps and "claude-agent-sdk" not in deps
