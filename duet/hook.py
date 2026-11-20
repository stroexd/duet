"""PreToolUse-Hook für Claude Code: setzt die Duet-Freigabestufen durch.

Liest das Hook-JSON von stdin ({tool_name, tool_input, cwd, ...}) und schreibt ggf.
{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": ..., ...}}.

AUTO  -> keine Ausgabe (normaler Ablauf)
BLOCK -> deny mit Grund
ASK   -> passende einmalige Freigabe im Store: verbrauchen, allow
         sonst interaktiv "ask", unbeaufsichtigt (DUET_UNATTENDED=1) "deny" mit Anleitung.
Fehler führen nie zu allow (fail closed).

Aufruf: duet-hook [--source project|plugin] [weitere Argumente werden ignoriert]
Mit --source plugin bleibt der Hook stumm, wenn das Projekt ($CLAUDE_PROJECT_DIR/.claude/settings.json)
bereits einen duet-hook einträgt; sonst liefe die Prüfung doppelt und die einmalige Freigabe würde
vom ersten Lauf verbraucht und vom zweiten abgelehnt.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, TextIO

from duet import policy
from duet.policy import Tier

UNATTENDED_HINT = ("Freigabe nötig: rufe zuerst mcp duet request_approval mit genau diesem "
                   "tool_name/tool_input auf und wiederhole dann.")


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in {"1", "true", "yes", "ja", "on"}


def _source(argv: list[str]) -> str:
    for i, a in enumerate(argv):
        if a == "--source" and i + 1 < len(argv):
            return argv[i + 1].strip().lower()
        if a.startswith("--source="):
            return a.split("=", 1)[1].strip().lower()
    return ""


def project_has_duet_hook(project_dir: str | None) -> bool:
    """True, wenn <project>/.claude/settings.json (oder settings.local.json) einen duet-hook einträgt."""
    if not project_dir:
        return False
    for name in ("settings.json", "settings.local.json"):
        p = Path(project_dir) / ".claude" / name
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for entry in (data.get("hooks", {}) or {}).get("PreToolUse", []) or []:
            for h in (entry or {}).get("hooks", []) or []:
                h = h if isinstance(h, dict) else {}
                args = h.get("args") if isinstance(h.get("args"), list) else []
                if "duet-hook" in " ".join([str(h.get("command", "")), *map(str, args)]):
                    return True
    return False


def _store_path(env: Any) -> Path | None:
    """Relativer DUET_STATE_DIR wird relativ zur Projektwurzel aufgelöst (Hook-CWD kann abweichen)."""
    sd, proj = env.get("DUET_STATE_DIR"), env.get("CLAUDE_PROJECT_DIR")
    if sd and proj and not Path(sd).expanduser().is_absolute():
        return (Path(proj) / sd).resolve()
    return None


def _consume(tool_name: str, tool_input: dict, env: Any) -> bool:
    """Einmalige Freigabe aus dem Store prüfen und verbrauchen (Hash-Logik liegt in duet.store)."""
    try:
        from duet import store
        path = _store_path(env)
        if path is not None:
            return store.Store(path).consume_grant(tool_name, tool_input) is True
        fn = getattr(store, "consume_approval", None)
        if callable(fn):
            return fn(tool_name, tool_input) is True
        return store.Store(_store_path(env)).consume_grant(tool_name, tool_input) is True
    except Exception:
        return False


def _out(decision: str, reason: str) -> dict:
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse",
                                   "permissionDecision": decision,
                                   "permissionDecisionReason": reason[:500]}}


def decide(data: Any, env: dict | None = None) -> dict | None:
    """Reine Entscheidungslogik; gibt das Ausgabe-JSON oder None (keine Ausgabe) zurück."""
    env = os.environ if env is None else env
    unattended = _truthy(env.get("DUET_UNATTENDED"))
    if not isinstance(data, dict) or not isinstance(data.get("tool_name"), str):
        return _out("deny" if unattended else "ask", "Duet: Hook-Eingabe unlesbar (fail closed)")
    tool_name = data["tool_name"]
    tool_input = data.get("tool_input") if isinstance(data.get("tool_input"), dict) else {}
    policy.set_workspace(env.get("CLAUDE_PROJECT_DIR") or data.get("cwd") or os.getcwd())
    d = policy.classify(tool_name, tool_input)
    if d.tier is Tier.AUTO:
        return None
    if d.tier is Tier.BLOCK:
        return _out("deny", f"Duet blockiert: {d.reason}. Aktion: {d.summary}")
    if _consume(tool_name, tool_input, env):
        return _out("allow", f"Duet: einmalige Freigabe verwendet ({d.summary})")
    if unattended:
        return _out("deny", f"{UNATTENDED_HINT} ({d.summary})")
    return _out("ask", f"Duet: {d.summary}")


def main(argv: list[str] | None = None, stdin: TextIO | None = None, stdout: TextIO | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    if _source(argv) == "plugin" and project_has_duet_hook(os.environ.get("CLAUDE_PROJECT_DIR")):
        return 0
    try:
        raw = stdin.read()
        data = json.loads(raw) if raw.strip() else None
    except (ValueError, OSError):
        data = None
    try:
        result = decide(data)
    except Exception as exc:  # niemals allow bei Fehlern
        unattended = _truthy(os.environ.get("DUET_UNATTENDED"))
        result = _out("deny" if unattended else "ask", f"Duet: interner Fehler ({type(exc).__name__})")
    if result is not None:
        stdout.write(json.dumps(result, ensure_ascii=False))
        stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
