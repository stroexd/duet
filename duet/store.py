"""Plattformneutraler Zustand als kleine, diff-freundliche JSON-Dateien.

Ort: env DUET_STATE_DIR (relativ = relativ zum CWD, z. B. "state" im duet-home-Repo) > config_dir().
Dateien: seen.json, approvals.json (einmalige Freigaben), pending.json (offene Freigaben/Fragen),
telegram.json (offset, Inbox-Puffer, Button-Antworten, letzte Meldung), memory.md (Notizen).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from duet.config import config_dir

log = logging.getLogger(__name__)

GRANT_TTL_S = 15 * 60
SEEN_TTL_S = 60 * 24 * 3600  # gemeldete Schlüssel 60 Tage merken
INBOX_MAX = 200
LOCK_TIMEOUT_S = 10.0
LOCK_STALE_S = 60.0


def state_dir() -> Path:
    """DUET_STATE_DIR (relativ: zu CLAUDE_PROJECT_DIR, sonst CWD – wie im Hook) > config_dir()."""
    if env := os.environ.get("DUET_STATE_DIR"):
        p = Path(env).expanduser()
        if not p.is_absolute() and (root := os.environ.get("CLAUDE_PROJECT_DIR")):
            p = Path(root) / p
        return p.resolve()
    return config_dir()


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def approval_key(tool_name: str, tool_input: dict) -> str:
    """sha256 über tool_name + kanonisches JSON des Inputs (identisch für Hook und MCP-Server)."""
    return hashlib.sha256(f"{tool_name}\n{canonical_json(tool_input or {})}".encode()).hexdigest()


def _now() -> float:
    return time.time()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.dir = Path(path) if path else state_dir()

    # ---- Datei-Helfer ------------------------------------------------------------
    def _read(self, name: str) -> dict[str, Any]:
        p = self.dir / name
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            log.warning("%s unlesbar, wird neu angelegt", p, exc_info=True)
            return {}

    def _write(self, name: str, data: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.dir / name
        tmp = p.with_name(f".{name}.{secrets.token_hex(3)}.tmp")
        tmp.write_text(json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, p)

    @contextmanager
    def _lock(self, name: str, timeout_s: float | None = None) -> Iterator[None]:
        """Einfache, plattformneutrale Dateisperre (O_CREAT|O_EXCL-Lockfile) für Read-Modify-Write.
        Lockfiles älter als LOCK_STALE_S gelten als verwaist und werden entfernt.
        Nach timeout_s: TimeoutError (Aufrufer im Hook => fail closed)."""
        self.dir.mkdir(parents=True, exist_ok=True)
        lock = self.dir / f".{name}.lock"
        deadline = time.monotonic() + (LOCK_TIMEOUT_S if timeout_s is None else timeout_s)
        while True:
            try:
                fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                break
            except FileExistsError:
                try:
                    if time.time() - lock.stat().st_mtime > LOCK_STALE_S:
                        lock.unlink(missing_ok=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Sperre {lock} nicht erhalten")
                time.sleep(0.05)
        try:
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            yield
        finally:
            try:
                lock.unlink()
            except OSError:  # pragma: no cover
                pass

    # ---- seen ------------------------------------------------------------------------
    def seen(self, keys: list[str]) -> list[str]:
        known = self._read("seen.json")
        return [k for k in keys if k in known]

    def mark_seen(self, keys: list[str]) -> None:
        now = _now()
        data = {k: v for k, v in self._read("seen.json").items()
                if now - datetime.fromisoformat(v).timestamp() < SEEN_TTL_S}
        for k in keys:
            data.setdefault(k, _iso(now))
        self._write("seen.json", data)

    # ---- einmalige Freigaben (vom Hook verbraucht) ------------------------------------
    def add_grant(self, tool_name: str, tool_input: dict, ttl_s: float = GRANT_TTL_S) -> str:
        key = approval_key(tool_name, tool_input)
        with self._lock("approvals.json"):
            now = _now()
            data = {k: v for k, v in self._read("approvals.json").items() if v.get("expires", 0) > now}
            data[key] = {"tool_name": tool_name, "expires": now + ttl_s, "granted": _iso(now)}
            self._write("approvals.json", data)
        return key

    def consume_grant(self, tool_name: str, tool_input: dict) -> bool:
        """True (und Freigabe gelöscht), wenn eine gültige einmalige Freigabe existiert."""
        key = approval_key(tool_name, tool_input)
        with self._lock("approvals.json"):
            data = self._read("approvals.json")
            entry = data.pop(key, None)
            valid = entry is not None and entry.get("expires", 0) > _now()
            if entry is not None:
                self._write("approvals.json", data)
        return valid

    # ---- offene Freigaben/Fragen ---------------------------------------------------------
    def pending(self) -> dict[str, dict[str, Any]]:
        return self._read("pending.json")

    def put_pending(self, req_id: str, entry: dict[str, Any]) -> None:
        with self._lock("pending.json"):
            data = self._read("pending.json")
            data[req_id] = entry
            self._write("pending.json", data)

    def drop_pending(self, req_id: str) -> None:
        with self._lock("pending.json"):
            data = self._read("pending.json")
            if data.pop(req_id, None) is not None:
                self._write("pending.json", data)

    def find_pending(self, key: str) -> str | None:
        """req_id einer offenen Freigabe mit diesem approval_key."""
        return next((rid for rid, e in self.pending().items() if e.get("key") == key), None)

    # ---- Telegram-Zustand ------------------------------------------------------------------
    def telegram(self) -> dict[str, Any]:
        data = self._read("telegram.json")
        data.setdefault("offset", None)
        data.setdefault("inbox", [])
        data.setdefault("answers", {})
        return data

    def save_telegram(self, data: dict[str, Any]) -> None:
        data["inbox"] = data.get("inbox", [])[-INBOX_MAX:]
        self._write("telegram.json", data)

    def pop_answer(self, req_id: str) -> str | None:
        data = self.telegram()
        answer = data["answers"].pop(req_id, None)
        if answer is not None:
            self.save_telegram(data)
        return answer

    def take_inbox(self, since_ts: float = 0.0) -> list[dict[str, Any]]:
        """Gibt gepufferte Nachrichten (date >= since_ts) zurück und entfernt alle gelieferten."""
        data = self.telegram()
        msgs = [m for m in data["inbox"] if m.get("date", 0) >= since_ts]
        if data["inbox"]:
            data["inbox"] = []
            self.save_telegram(data)
        return msgs

    def set_last_notify(self, info: dict[str, Any]) -> None:
        data = self.telegram()
        data["last_notify"] = info
        self.save_telegram(data)

    # ---- Notizen ------------------------------------------------------------------------------
    def remember(self, note: str) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        p = self.dir / "memory.md"
        line = " ".join(note.split())
        stamp = datetime.now().strftime("%Y-%m-%d")
        prefix = "" if p.exists() else "# Duet-Notizen\n\n"
        with p.open("a", encoding="utf-8") as fh:
            fh.write(f"{prefix}- [{stamp}] {line}\n")

    def recall(self, query: str = "") -> str:
        p = self.dir / "memory.md"
        if not p.exists():
            return ""
        lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.startswith("- ")]
        words = query.lower().split()
        if words:
            lines = [ln for ln in lines if all(w in ln.lower() for w in words)]
        return "\n".join(lines)


def consume_approval(tool_name: str, tool_input: dict) -> bool:
    """Für duet/hook.py: verbraucht eine passende einmalige Freigabe (15 min gültig) im Standard-Store."""
    return Store().consume_grant(tool_name, tool_input)
