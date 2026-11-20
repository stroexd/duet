"""Vorlagen-Repo template-duet-home: Skills synchron, keine Platzhalter, Sicherungsbefehl erlaubt."""

import importlib.util
import json
import re
from pathlib import Path

import pytest

from duet.policy import Tier, classify

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "template-duet-home"


def _sync():
    spec = importlib.util.spec_from_file_location("sync_template", ROOT / "scripts" / "sync_template.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_template_skills_in_sync():
    mod = _sync()
    for path, text in mod.targets().items():
        assert path.exists(), f"{path} fehlt – python scripts/sync_template.py ausführen"
        assert path.read_text(encoding="utf-8") == text, f"{path} veraltet – python scripts/sync_template.py"
    assert mod.main(["--check"]) == 0


@pytest.mark.parametrize("name", ["duet-heartbeat", "duet-brief"])
def test_template_skill_names(name):
    text = (TEMPLATE / ".claude" / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    assert re.search(rf"(?m)^name: {name}$", text.split("\n---", 1)[0])


def test_render_rejects_missing_name():
    with pytest.raises(ValueError):
        _sync().render("---\ndescription: x\n---\nbody", "duet-x")


def test_no_placeholders_left():
    files = [p for p in [*TEMPLATE.rglob("*"), *(ROOT / "skills").rglob("*"), ROOT / ".claude-plugin" / "plugin.json",
                         ROOT / ".claude-plugin" / "marketplace.json"] if p.is_file()]
    for p in files:
        text = p.read_text(encoding="utf-8")
        assert not re.search(r"\bOWNER\b|<owner>/duet|DEIN_GITHUB", text), p


def test_template_json_valid_and_points_to_plugin_repo():
    mcp = json.loads((TEMPLATE / ".mcp.json").read_text())["mcpServers"]["duet"]
    assert "git+https://github.com/stroexd/duet" in mcp["args"]
    assert mcp.get("timeout", 0) >= 15 * 60 * 1000  # Warten auf Freigabe (10 min) + Puffer
    settings = json.loads((TEMPLATE / ".claude" / "settings.json").read_text())
    hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]
    assert hook["args"][-2:] == ["--source", "project"]


@pytest.mark.parametrize("path", ["skills/heartbeat/SKILL.md", "skills/brief/SKILL.md", "template-duet-home/CLAUDE.md"])
def test_persist_command_in_docs_is_auto(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    cmds = [ln.strip() for ln in text.splitlines() if ln.strip().startswith("git pull") and "duet: state" in ln]
    assert cmds, f"kein Sicherungsbefehl in {path}"
    for cmd in cmds:
        assert classify("Bash", {"command": cmd}).tier is Tier.AUTO, cmd
