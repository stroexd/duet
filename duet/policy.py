"""Freigabestufen für Tool-Aufrufe (nach dem Vorbild von Claude Duet).

AUTO  = lesen, suchen, entwerfen: läuft ohne Nachfrage.
ASK   = senden, antworten, posten, veröffentlichen, löschen, archivieren, Termine bei anderen,
        bezahlen, unterschreiben, mergen, pushen, Berechtigungen ändern, Code ausführen,
        und alles Unbekannte (fail closed): nur nach Freigabe per Telegram.
BLOCK = Geld bewegen, Überweisungen, Trading, Krypto-Transfers, Wertpapierkäufe,
        offensichtlich zerstörerische Shell-Befehle: immer abgelehnt.

Die Klassifikation ist eine Heuristik auf Tool-Namen und Eingaben (MCP-Annotations sind nicht
verfügbar). Sie ist bewusst konservativ: AUTO nur, wenn ein Lese-/Entwurfs-Signal vorliegt und
kein riskantes Wort im Namen steht.
"""

from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any


class Tier(Enum):
    AUTO = "auto"
    ASK = "ask"
    BLOCK = "block"


_RANK = {Tier.AUTO: 0, Tier.ASK: 1, Tier.BLOCK: 2}


@dataclass(frozen=True)
class Decision:
    tier: Tier
    reason: str
    summary: str  # kurze deutsche Beschreibung der Aktion für die Freigabe-Nachricht


def strictest(decisions: list[Decision]) -> Decision:
    return max(decisions, key=lambda d: _RANK[d.tier])


# --------------------------------------------------------------------------- Workspace

_workspace: Path | None = None
_PROTECTED_IN_WORKSPACE = (".mcp.json", ".claude", ".duet", ".git")


def set_workspace(path: str | os.PathLike[str] | None) -> None:
    """Setzt den Workspace, innerhalb dessen Write/Edit automatisch erlaubt sind."""
    global _workspace
    _workspace = None if path is None else Path(path).expanduser().resolve()


def get_workspace() -> Path | None:
    return _workspace


def _path_in_workspace(raw: Any) -> tuple[bool, str]:
    """(True, '') wenn raw sicher im Workspace liegt, sonst (False, Grund)."""
    if _workspace is None:
        return False, "kein Workspace gesetzt"
    if not isinstance(raw, str) or not raw.strip() or "\x00" in raw:
        return False, "Pfad fehlt oder ist ungültig"
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = _workspace / p
    try:
        resolved = p.resolve()  # löst ../ und Symlinks auf
    except (OSError, RuntimeError):
        return False, "Pfad nicht auflösbar"
    if not resolved.is_relative_to(_workspace):
        return False, "Pfad liegt außerhalb des Workspace"
    rel = resolved.relative_to(_workspace).parts
    if rel and rel[0] in _PROTECTED_IN_WORKSPACE:
        return False, f"geschützte Konfiguration im Workspace ({rel[0]})"
    # Duet-Zustand (approvals.json!) nur über den Duet-MCP-Server, sonst ließen sich Freigaben fälschen
    sd = tuple(_state_dir_name().split("/"))
    if rel[: len(sd)] == sd:
        return False, f"Duet-Zustand ({'/'.join(sd)}/) ist geschützt"
    return True, ""


# --------------------------------------------------------------------------- Tokenisierung

def tokens(name: str) -> list[str]:
    """Zerlegt snake_case, kebab-case, camelCase, UPPER_CASE in kleingeschriebene Wörter."""
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", s)
    return [t for t in re.split(r"[^A-Za-z0-9]+", s.lower()) if t]


# Wortlisten (ganze Tokens, keine Substrings: "BLACKLIST" enthält kein "list")
READ_VERBS = {
    "get", "list", "search", "fetch", "read", "find", "query", "describe", "view", "show",
    "lookup", "retrieve", "count", "browse", "inspect", "preview", "download", "navigate",
    "screenshot", "summarize", "analyze", "explain", "estimate", "validate", "diagnose", "diff",
    "compare", "ping", "whoami", "scan",
}
READ_NOUNS = {"details", "detail", "info", "stats", "status", "schema", "schemas"}
DRAFT_WORDS = {"draft", "drafts", "compose"}
# Wörter, die zusammen mit "draft" harmlos sind (Entwurf anlegen/ändern, Antwort-Entwurf)
DRAFT_COMPATIBLE = {"create", "update", "edit", "save", "write", "new", "add", "make", "generate",
                    "reply", "respond", "email", "message", "comment", "replace", "modify"}
# Wörter, die oft Substantive sind ("get_latest_release", "get_comment"): nach einem Leseverb harmlos
NOUNISH = {"release", "comment", "post", "order", "event", "tag", "commit", "label", "link",
           "share", "star", "signature", "payment", "invoice", "envelope", "schedule", "run",
           "trigger", "job", "reaction", "script", "command", "request", "role", "transfer",
           "permission", "permissions", "member", "pin", "like", "follow", "block", "log", "copy",
           "change", "import", "patch", "upload", "book", "order", "submit", "tweet", "message"}

# Riskante Kategorien -> (deutscher Aktionsname, Grund); Reihenfolge = Priorität
ASK_CATEGORIES: list[tuple[set[str], str, str]] = [
    ({"send", "sends", "forward", "notify", "broadcast", "resend"}, "senden", "versendet etwas an Dritte"),
    ({"reply", "respond", "answer", "comment", "react", "reaction"}, "antworten", "antwortet in Ihrem Namen"),
    ({"post", "tweet", "retweet", "share", "unshare", "upload"}, "posten", "veröffentlicht oder teilt Inhalte"),
    ({"publish", "release", "deploy", "unpublish"}, "veröffentlichen", "veröffentlicht Inhalte"),
    ({"delete", "remove", "destroy", "purge", "erase", "trash", "clear", "drop", "wipe", "empty"},
     "löschen", "löscht Daten"),
    ({"archive", "unarchive", "mute", "spam"}, "archivieren", "verschiebt/archiviert Daten"),
    ({"accept", "decline", "rsvp", "invite", "schedule", "book", "reserve", "cancel", "reschedule"},
     "zusagen/Termin", "sagt zu oder legt Termine bei anderen an"),
    ({"pay", "payment", "checkout", "order", "subscribe", "charge", "refund", "invoice", "buy",
      "purchase", "donate", "tip"}, "bezahlen", "löst eine Zahlung oder einen Kauf aus"),
    ({"sign", "signature", "esign", "envelope", "approve", "submit"}, "unterschreiben/einreichen",
     "gibt eine verbindliche Erklärung ab"),
    ({"merge", "rebase"}, "mergen", "verändert Code-Stände"),
    ({"push"}, "pushen", "verändert ein Repository"),
    ({"grant", "revoke", "transfer", "permission", "permissions", "role", "acl", "visibility"},
     "Berechtigungen ändern", "ändert Zugriffsrechte oder Eigentum"),
    ({"execute", "exec", "run", "eval", "javascript", "script", "bash", "shell", "command",
      "trigger", "fire", "dispatch", "install", "computer"}, "ausführen", "führt Code oder Aktionen aus"),
    ({"create", "add", "insert", "new", "make", "upsert", "register", "duplicate", "import",
      "generate", "fork", "copy"}, "anlegen", "legt Daten an"),
    ({"update", "edit", "modify", "set", "patch", "put", "write", "move", "rename", "mark",
      "label", "star", "assign", "close", "reopen", "resolve", "unresolve", "enable", "disable",
      "restore", "reorder", "regenerate", "replace", "change", "apply", "start", "stop", "kill",
      "interrupt", "connect", "disconnect", "link", "unlink", "associate", "follow", "unfollow",
      "like", "block", "unblock", "ban", "kick", "pin", "unpin", "save", "store", "request",
      "tag", "commit", "lock", "unlock", "reset", "sync", "login", "logout", "authorize"},
     "ändern", "verändert Daten"),
]
_ALL_ASK: set[str] = set().union(*(w for w, _, _ in ASK_CATEGORIES))

# Geld bewegen / Trading / Krypto -> BLOCK
BLOCK_ALWAYS = {
    "payout", "payouts", "withdraw", "withdrawal", "withdrawals", "wire", "remit", "remittance",
    "trade", "trades", "trading", "sepa", "ueberweisung", "uberweisung", "ueberweisen",
}
MONEY_VERBS = {"send", "transfer", "move", "buy", "sell", "purchase", "swap", "exchange", "place",
               "create", "initiate", "execute", "submit", "convert", "stake", "bridge", "deposit",
               "pay"}
MONEY_NOUNS = {
    "money", "funds", "cash", "crypto", "bitcoin", "btc", "eth", "ether", "ethereum", "sol",
    "usdc", "usdt", "coin", "coins", "wallet", "stock", "stocks", "securities", "etf", "etfs",
    "bond", "bonds", "forex", "currency", "lamports", "wei", "satoshi", "sats", "nft",
}
FINANCE_APPS = {
    "stripe", "paypal", "wise", "transferwise", "revolut", "coinbase", "binance", "kraken",
    "robinhood", "alpaca", "bitpanda", "n26", "plaid", "square", "venmo", "cashapp", "moneygram",
    "westernunion", "traderepublic", "ibkr", "interactivebrokers", "etoro", "bybit", "okx",
    "kucoin", "bitstamp", "metamask", "solana", "ethereum", "bitcoin", "mercury", "brex",
    "payoneer", "klarna", "sumup", "mollie", "adyen", "gocardless", "qonto", "moonpay",
    "phantom", "uniswap", "trading212", "scalable", "comdirect", "dkb", "ing", "sparkasse",
}
FINANCE_ACTIONS = {"transfer", "transfers", "payout", "payouts", "order", "orders", "buy", "sell",
                   "trade", "send", "withdraw", "swap", "convert", "transaction", "transactions",
                   "deposit", "stake", "bridge", "position", "positions"}
_MONEY_KEYS = {"amount", "iban", "currency", "lamports", "wei", "quantity", "qty", "notional"}


def _is_money_move(toks: list[str], app_toks: set[str], tool_input: dict) -> bool:
    ts = set(toks)
    if ts & BLOCK_ALWAYS:
        return True
    verbs, nouns = ts & MONEY_VERBS, ts & MONEY_NOUNS
    if verbs and nouns and (verbs | nouns) != (verbs & nouns):
        return True
    actions = ts & FINANCE_ACTIONS
    if (app_toks & FINANCE_APPS) and actions:
        purely_reading = bool(ts & READ_VERBS) and not (ts & (_ALL_ASK - NOUNISH) - {"transfer"})
        if not purely_reading:
            return True
    keys = {str(k).lower() for k in tool_input} if isinstance(tool_input, dict) else set()
    if ts & {"transfer", "send", "transfers"} and keys & _MONEY_KEYS and (ts & MONEY_VERBS):
        return True
    return False


def classify_name(name: str, tool_input: dict | None, label: str | None = None,
                  app: str = "") -> Decision:
    """Verb-Heuristik auf einem Tool-Namen bzw. Composio-Slug (z. B. GMAIL_SEND_EMAIL)."""
    toks = tokens(name)
    label = label or name
    args = tool_input if isinstance(tool_input, dict) else {}
    app_toks = set(tokens(app)) | ({toks[0]} if toks else set())
    if _is_money_move(toks, app_toks, args):
        return Decision(Tier.BLOCK, f"{label}: Geld bewegen/Trading ist immer blockiert",
                        _summary("Geld bewegen", toks, args, label))
    ts = set(toks)
    has_draft = bool(ts & DRAFT_WORDS)
    # Substantiv-Wörter nach einem Leseverb ignorieren ("get_latest_release", "get_check_run")
    first_read = next((i for i, t in enumerate(toks) if t in READ_VERBS), None)
    ignore: set[str] = set()
    if first_read is not None:
        ignore = {t for t in toks[first_read + 1:] if t in NOUNISH}
        ignore -= set(toks[:first_read])
    for words, action, why in ASK_CATEGORIES:
        hit = (ts & words) - ignore
        if has_draft:
            hit -= DRAFT_COMPATIBLE
        if hit:
            return Decision(Tier.ASK, f"{label}: {why} ({', '.join(sorted(hit))})",
                            _summary(action, toks, args, label))
    if has_draft:
        return Decision(Tier.AUTO, f"{label}: Entwurf", _summary("entwerfen", toks, args, label))
    if ts & (READ_VERBS | READ_NOUNS):
        return Decision(Tier.AUTO, f"{label}: lesend", _summary("lesen", toks, args, label))
    return Decision(Tier.ASK, f"{label}: unbekannte Aktion (fail closed)",
                    _summary("ausführen", toks, args, label))


# --------------------------------------------------------------------------- Zusammenfassung

_SECRET_KEY_TOKENS = {"token", "tokens", "password", "passwd", "pwd", "passphrase", "secret",
                      "apikey", "auth", "authorization", "credential", "credentials", "cookie",
                      "cookies", "bearer", "otp", "cvv", "cvc", "pin", "iban", "mnemonic", "seed"}
_SECRET_KEY_PAIRS = {("api", "key"), ("private", "key"), ("access", "key"), ("signing", "key"),
                     ("client", "secret"), ("card", "number"), ("session", "id")}


def _is_secret_key(key: str) -> bool:
    t = tokens(key)
    if set(t) & _SECRET_KEY_TOKENS:
        return True
    return any(pair in _SECRET_KEY_PAIRS for pair in zip(t, t[1:]))


_SECRET_VAL_RE = re.compile(
    r"(gh[pousr]_[A-Za-z0-9]{10,}|github_pat_\w{10,}|sk-[A-Za-z0-9_\-]{10,}|xox[abprs]-[\w-]{10,}|"
    r"AKIA[0-9A-Z]{12,}|AIza[\w\-]{20,}|eyJ[\w-]{10,}\.[\w-]+\.[\w-]+|\d{8,12}:[A-Za-z0-9_-]{30,}|"
    r"Bearer\s+\S+|-----BEGIN [A-Z ]*PRIVATE KEY-----)")


def mask(value: Any, key: str = "") -> Any:
    """Maskiert Geheimnisse rekursiv (nach Schlüsselname und typischen Token-Mustern)."""
    if key and _is_secret_key(key):
        return "***"
    if isinstance(value, dict):
        return {k: mask(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask(v) for v in value]
    if isinstance(value, str):
        return _SECRET_VAL_RE.sub("***", value)
    return value


def _short(value: Any, n: int = 80) -> str:
    if isinstance(value, (list, tuple)):
        s = ", ".join(_short(v, n) for v in value[:5]) + (" …" if len(value) > 5 else "")
    elif isinstance(value, dict):
        s = ", ".join(f"{k}={_short(v, 30)}" for k, v in list(value.items())[:4])
    else:
        s = str(value)
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _limit(s: str, n: int = 200) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


_OBJECTS = [
    ({"email", "emails", "mail", "gmail", "outlook", "draft", "drafts"}, "E-Mail"),
    ({"message", "messages", "chat", "dm", "slack", "telegram", "whatsapp", "sms", "discord", "teams"}, "Nachricht"),
    ({"event", "events", "calendar", "googlecalendar", "meeting", "invite"}, "Termin"),
    ({"issue", "issues"}, "Issue"),
    ({"pull", "pr", "merge"}, "Pull-Request"),
    ({"file", "files", "document", "doc", "docs", "page", "drive", "notion"}, "Dokument"),
    ({"tweet", "post", "linkedin", "twitter"}, "Beitrag"),
]

_FIELDS = [
    (("to", "recipient", "recipients", "recipient_email", "to_email", "email", "attendees", "user", "users", "phone"), "an {}"),
    (("channel", "channel_id", "chat_id", "conversation"), "Kanal {}"),
    (("repo", "repository", "owner"), "Repo {}"),
    (("subject",), "Betreff '{}'"),
    (("title", "name", "summary"), "Titel '{}'"),
    (("amount", "value"), "Betrag {}"),
    (("currency",), "{}"),
    (("start", "start_time", "start_datetime", "date"), "Beginn {}"),
    (("path", "file_path", "url", "endpoint"), "{}"),
    (("text", "body", "message", "content", "comment"), "Text '{}'"),
]


def _summary(action: str, toks: list[str], args: dict, label: str) -> str:
    ts = set(toks)
    obj = next((o for words, o in _OBJECTS if ts & words), "")
    head = f"{obj} {action}".strip() if obj else action[:1].upper() + action[1:]
    margs = mask(args) if isinstance(args, dict) else {}
    parts: list[str] = []
    lower = {str(k).lower(): v for k, v in margs.items()}
    for keys, fmt in _FIELDS:
        for k in keys:
            if k in lower and lower[k] not in (None, "", [], {}):
                parts.append(fmt.format(_short(lower[k], 60)))
                break
    text = head
    if parts and parts[0].startswith("an "):
        text += " " + parts.pop(0)
    if parts:
        text += ": " + ", ".join(parts)
    return _limit(f"{text} [{label}]") if len(text) + len(label) < 197 else _limit(text)


# --------------------------------------------------------------------------- Bash

BASH_READ_ALLOW = {"ls", "cat", "date", "grep", "head", "tail", "wc", "echo", "pwd", "df", "uptime"}
_BASH_META = re.compile(r"[;&|<>`\n\r\\]|\$\(|\$\{|\$[A-Za-z_]")
_BASH_DESTRUCTIVE = [
    re.compile(r"\brm\s+(-[a-zA-Z]*\s+|--[a-z-]+\s+)*-[a-zA-Z]*[rR][a-zA-Z]*\s+(-\S+\s+)*"
               r"(/|/\*|~/?|~/\*|\$HOME/?|\$\{HOME\}/?|/(home|etc|usr|boot|var|bin|lib|lib64|root|opt|srv)/?\*?|\*|\.)"
               r"(?=\s|$|;|&|\|)"),
    re.compile(r"\brm\s+.*--no-preserve-root"),
    re.compile(r"\bmkfs(\.\w+)?\b"),
    re.compile(r"\bdd\b.*\bof=/dev/"),
    re.compile(r">\s*/dev/(sd|nvme|hd|vd|mmcblk)"),
    re.compile(r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"),
    re.compile(r"\b(shred|wipefs)\b.*\s/dev/"),
    re.compile(r"\bchmod\s+(-\w+\s+)*-R\s+\S+\s+/(\s|$)"),
    re.compile(r"\bchown\s+(-\w+\s+)*-R\s+\S+\s+/(\s|$)"),
    re.compile(r"\bmv\s+(/|~)\s+/dev/null"),
]


def _state_dir_name() -> str:
    """DUET_STATE_DIR relativ zum Workspace (Default "state")."""
    d = os.environ.get("DUET_STATE_DIR", "state").strip() or "state"
    p = Path(d).expanduser()
    if p.is_absolute():
        try:
            d = str(p.resolve().relative_to(_workspace)) if _workspace else "state"
        except ValueError:
            d = "state"
    d = d.replace("\\", "/").strip("/")
    if d.startswith("./"):
        d = d[2:]
    return d if re.fullmatch(r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*", d) and ".." not in d.split("/") else "state"


def _is_state_persist(command: str) -> bool:
    """Exakt der Sicherungsbefehl der Duet-Skills (KURSWECHSEL 2), sonst nichts. Kanonisch:
    git pull --rebase --autostash origin main && git add state && (git diff --cached --quiet || git commit -m "duet: state") && git push origin HEAD:main
    Erlaubt sind außerdem die Kurzformen ohne pull bzw. ohne diff-Klammer
    (git add state && git commit [-q] -m "duet: state..." && git push [-q] [origin [HEAD|HEAD:main]]).
    --autostash ist nötig, weil state/ beim pull ungesicherte Änderungen hat; origin main/HEAD:main, weil
    Cloud-Sitzungen auf einem claude/-Branch laufen können; die diff-Klammer macht "nothing to commit" zu Exit 0.
    Nur state/ wird hinzugefügt, nie force-push."""
    d = re.escape(_state_dir_name())
    msg = r"(\"duet: state[A-Za-z0-9 :._-]*\"|'duet: state[A-Za-z0-9 :._-]*')"
    commit = rf"git commit( -q)? -m {msg}"
    rx = (r"(git pull --rebase( --autostash)?( -q)?( origin main)? && )?"
          rf"git add (-A )?{d}/? && "
          rf"({commit}|\(git diff --cached --quiet \|\| {commit}\))"
          r" && git push( -q)?( origin( HEAD| HEAD:main)?)?")
    return re.fullmatch(rx, " ".join(command.split())) is not None and "\n" not in command


def classify_bash(command: Any) -> Decision:
    if not isinstance(command, str) or not command.strip():
        return Decision(Tier.ASK, "Bash: leerer/ungültiger Befehl", "Befehl ausführen (leer)")
    shown = _limit(" ".join(_SECRET_VAL_RE.sub("***", command).split()), 180)
    summary = f"Befehl ausführen: {shown}"
    for rx in _BASH_DESTRUCTIVE:
        if rx.search(command):
            return Decision(Tier.BLOCK, "Bash: offensichtlich zerstörerischer Befehl", summary)
    if _is_state_persist(command):
        return Decision(Tier.AUTO, "Bash: Duet-Zustand (state/) sichern", summary)
    if _BASH_META.search(command):
        return Decision(Tier.ASK, "Bash: Verkettung, Umleitung oder Ersetzung", summary)
    try:
        argv = shlex.split(command)
    except ValueError:
        return Decision(Tier.ASK, "Bash: nicht parsebar", summary)
    if not argv:
        return Decision(Tier.ASK, "Bash: leer", summary)
    prog = argv[0]
    if prog not in BASH_READ_ALLOW:
        return Decision(Tier.ASK, f"Bash: '{_short(prog, 40)}' nicht auf der Lese-Allowlist", summary)
    if prog == "date" and any(a in ("-s", "--set") or a.startswith("--set=") for a in argv[1:]):
        return Decision(Tier.ASK, "Bash: date --set ändert die Systemzeit", summary)
    return Decision(Tier.AUTO, f"Bash: lesender Befehl ({prog})", summary)


# --------------------------------------------------------------------------- Composio

_COMPOSIO_AUTO = {
    "COMPOSIO_SEARCH_TOOLS", "COMPOSIO_GET_TOOL_SCHEMAS", "COMPOSIO_SEARCH_SKILLS",
    "COMPOSIO_WAIT_FOR_CONNECTIONS", "COMPOSIO_CHECK_ACTIVE_CONNECTION", "COMPOSIO_LIST_TOOLKITS",
    "COMPOSIO_GET_REQUIRED_PARAMETERS",
}
_COMPOSIO_ASK = {
    "COMPOSIO_MANAGE_CONNECTIONS": "ändert Verbindungen/Konten",
    "COMPOSIO_INITIATE_CONNECTION": "ändert Verbindungen/Konten",
    "COMPOSIO_REMOTE_BASH_TOOL": "führt Code in der Composio-Sandbox aus",
    "COMPOSIO_REMOTE_WORKBENCH": "führt Code in der Composio-Sandbox aus",
    "COMPOSIO_MANAGE_SKILL": "ändert Skills",
    "COMPOSIO_USE_SKILL": "führt einen Skill aus",
    "COMPOSIO_SUBMIT_FEEDBACK": "sendet Daten an Composio",
}


def _classify_composio(meta: str, tool_input: dict, label: str) -> Decision:
    if meta in _COMPOSIO_AUTO:
        return Decision(Tier.AUTO, f"{meta}: Suche/Schema", f"Composio: {meta.removeprefix('COMPOSIO_').lower()}")
    if meta in _COMPOSIO_ASK:
        why = _COMPOSIO_ASK[meta]
        detail = ""
        if isinstance(tool_input, dict):
            m = mask(tool_input)
            detail = ": " + _short(m, 150) if m else ""
        return Decision(Tier.ASK, f"{meta}: {why}", _limit(f"Composio {why}{detail}"))
    if meta == "COMPOSIO_PROXY_EXECUTE":
        method = str(tool_input.get("method", "")).strip().upper() if isinstance(tool_input, dict) else ""
        ep = _short(mask(tool_input).get("endpoint", ""), 120) if isinstance(tool_input, dict) else ""
        summary = _limit(f"Composio-API-Aufruf {method or '?'} {ep}".rstrip())
        if method in ("GET", "HEAD"):
            return Decision(Tier.AUTO, f"{meta}: lesender HTTP-{method}", summary)
        return Decision(Tier.ASK, f"{meta}: schreibender HTTP-Aufruf ({method or 'unbekannt'})", summary)
    if meta in ("COMPOSIO_MULTI_EXECUTE_TOOL", "COMPOSIO_EXECUTE_TOOL", "COMPOSIO_EXECUTE_ACTION"):
        items: list[Any]
        if meta == "COMPOSIO_MULTI_EXECUTE_TOOL":
            items = tool_input.get("tools") if isinstance(tool_input, dict) else None  # type: ignore[assignment]
        else:
            items = [tool_input] if isinstance(tool_input, dict) else []
        if not isinstance(items, list) or not items:
            return Decision(Tier.ASK, f"{meta}: keine Tool-Liste (fail closed)", "Composio-Aktion (unbekannt)")
        decisions: list[Decision] = []
        for it in items:
            if not isinstance(it, dict):
                decisions.append(Decision(Tier.ASK, "ungültiger Eintrag", "Composio-Aktion (ungültig)"))
                continue
            slug = it.get("tool_slug") or it.get("slug") or it.get("action") or it.get("tool")
            args = it.get("arguments") or it.get("params") or it.get("input") or {}
            if not isinstance(slug, str) or not slug.strip():
                decisions.append(Decision(Tier.ASK, "Eintrag ohne tool_slug", "Composio-Aktion (ohne Slug)"))
                continue
            slug = slug.strip().upper()
            if slug.startswith("COMPOSIO_"):
                decisions.append(_classify_composio(slug, args if isinstance(args, dict) else {}, slug))
            else:
                decisions.append(classify_name(slug, args if isinstance(args, dict) else {}, slug))
        top = strictest(decisions)
        relevant = [d for d in decisions if d.tier == top.tier] if top.tier != Tier.AUTO else decisions
        summary = _limit("; ".join(d.summary for d in relevant), 300)
        reason = "; ".join(d.reason for d in decisions)
        if len(decisions) > 1:
            reason = f"strengste Stufe aus {len(decisions)} Aktionen – " + reason
        return Decision(top.tier, _limit(reason, 400), summary)
    return Decision(Tier.ASK, f"{meta}: unbekanntes Composio-Meta-Tool (fail closed)", f"Composio: {meta}")


# --------------------------------------------------------------------------- Einstieg

BUILTIN_AUTO = {
    "Read", "Glob", "Grep", "LS", "WebSearch", "WebFetch", "TodoWrite", "TodoRead", "Task", "Agent",
    "NotebookRead", "BashOutput", "ToolSearch", "ListMcpResourcesTool", "ReadMcpResourceTool",
    "TaskOutput", "Skill",
}
BUILTIN_WRITE = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def classify(tool_name: str, tool_input: dict) -> Decision:
    try:
        return _classify(tool_name, tool_input if isinstance(tool_input, dict) else {})
    except Exception as exc:  # niemals mit AUTO durchrutschen
        return Decision(Tier.ASK, f"Fehler bei der Einstufung: {type(exc).__name__}", f"{_short(tool_name, 60)} ausführen")


def _classify(tool_name: str, tool_input: dict) -> Decision:
    if not isinstance(tool_name, str) or not tool_name:
        return Decision(Tier.ASK, "kein Tool-Name", "Unbekannte Aktion")
    if tool_name in BUILTIN_AUTO:
        detail = tool_input.get("file_path") or tool_input.get("pattern") or tool_input.get("query") \
            or tool_input.get("url") or tool_input.get("description") or ""
        return Decision(Tier.AUTO, f"{tool_name}: lesend/planend", _limit(f"{tool_name}: {_short(mask(detail), 150)}".rstrip(": ")))
    if tool_name in BUILTIN_WRITE:
        raw = tool_input.get("file_path", tool_input.get("notebook_path"))
        ok, why = _path_in_workspace(raw)
        verb = "Datei schreiben" if tool_name == "Write" else "Datei bearbeiten"
        summary = _limit(f"{verb}: {_short(raw, 180)}")
        if ok:
            return Decision(Tier.AUTO, f"{tool_name}: innerhalb des Workspace", summary)
        return Decision(Tier.ASK, f"{tool_name}: {why}", summary)
    if tool_name == "Bash":
        return classify_bash(tool_input.get("command"))
    if tool_name.startswith("mcp__"):
        rest = tool_name[len("mcp__"):]
        server, sep, tool = rest.partition("__")
        if not sep or not server or not tool:
            return Decision(Tier.ASK, "ungültiger MCP-Toolname (fail closed)", f"{_short(tool_name, 80)} ausführen")
        if "duet" in tokens(server) or server.lower().endswith("duet"):
            # Duet-eigene Tools (notify, ask_user, request_approval, inbox, seen, ...) sind immer AUTO
            return Decision(Tier.AUTO, f"Duet-eigenes Tool ({tool})", f"Duet: {tool}")
        if tool.upper().startswith("COMPOSIO_"):
            return _classify_composio(tool.upper(), tool_input, tool)
        return classify_name(tool, tool_input, f"{server}/{tool}", app=server)
    return Decision(Tier.ASK, f"{tool_name}: unbekanntes Tool (fail closed)", _limit(f"{tool_name} ausführen: {_short(mask(tool_input), 150)}"))
