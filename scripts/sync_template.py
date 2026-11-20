#!/usr/bin/env python3
"""Kopiert die Skills des Plugins in das Vorlagen-Repo template-duet-home/.claude/skills/.

Cloud-Routinen installieren keine Plugins; sie finden Skills nur unter .claude/skills/ im geklonten Repo.
Dort heißen sie duet-heartbeat / duet-brief (Befehle /duet-heartbeat, /duet-brief).

    python scripts/sync_template.py          # Kopien neu schreiben
    python scripts/sync_template.py --check  # Exit 1, wenn die Kopien veraltet sind (CI/Tests)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = {"heartbeat": "duet-heartbeat", "brief": "duet-brief"}


def render(src: str, name: str) -> str:
    """Setzt `name:` im Frontmatter auf den Template-Namen (nur die erste name-Zeile im Frontmatter)."""
    if not src.startswith("---\n"):
        raise ValueError("SKILL.md ohne Frontmatter")
    end = src.index("\n---", 4)
    head, body = src[:end], src[end:]
    head, n = re.subn(r"(?m)^name:.*$", f"name: {name}", head, count=1)
    if n != 1:
        raise ValueError("Frontmatter ohne name:")
    return head + body


def targets(root: Path = ROOT) -> dict[Path, str]:
    out: dict[Path, str] = {}
    for skill, name in SKILLS.items():
        src = (root / "skills" / skill / "SKILL.md").read_text(encoding="utf-8")
        out[root / "template-duet-home" / ".claude" / "skills" / name / "SKILL.md"] = render(src, name)
    return out


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    check = "--check" in argv
    stale = []
    for path, text in targets().items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current == text:
            continue
        stale.append(path)
        if not check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8", newline="\n")
    for p in stale:
        print(("veraltet: " if check else "geschrieben: ") + str(p.relative_to(ROOT)))
    return 1 if (check and stale) else 0


if __name__ == "__main__":
    raise SystemExit(main())
