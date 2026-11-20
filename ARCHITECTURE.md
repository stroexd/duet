# Duet-Lokal: Architektur (verbindlich für alle Rollen)

Ziel: Nachbau von Anthropics "Claude Duet" (Research Preview, Okt 2026) für einen einzelnen Nutzer
auf einem eigenen Linux-Rechner (Arch, systemd, Python 3.11+), ohne US-/Firmen-Account.

Duet-Merkmale, die wir nachbauen:
1. Immer live: ein Dauerprozess (systemd user service), eine fortlaufende Claude-Sitzung.
2. Proaktiv: prüft verbundene Konten alle paar Stunden ("Heartbeat") und schickt ein Morgen-Briefing.
   Meldet sich nur, wenn etwas Aufmerksamkeit braucht, fertig ist oder eine Entscheidung braucht.
3. Chat am Handy: Telegram-Bot (zwei Richtungen). Nur die eine gekoppelte chat_id wird akzeptiert.
4. Konnektoren "wie in echt": ALLE MCP-Server aus der normalen Claude-Code-Konfiguration des Nutzers
   (user scope, z. B. Composio mit tausenden Apps, GitHub) plus Skills und CLAUDE.md, über
   `setting_sources=["user", "project"]` im Claude Agent SDK. Zusätzliche Server in `<workspace>/.mcp.json`.
5. Freigabestufen wie Duet: lesen/entwerfen automatisch; senden/bezahlen/unterschreiben/löschen/
   veröffentlichen nur nach Freigabe per Telegram-Button; Geld bewegen/Trading immer blockiert.
6. Gedächtnis: Workspace-Ordner mit CLAUDE.md (Persona, Regeln) und memory/*.md, die Claude selbst pflegt.

Laufzeit: Claude Agent SDK für Python (`claude-agent-sdk`, getestet mit 0.2.186), das die lokal
installierte, eingeloggte `claude` CLI nutzt. HTTP: `httpx` (async). Config: TOML via `tomllib`.

## Paketlayout (Python-Paket `duet`, Repo-Wurzel /home/user/duet)

```
pyproject.toml            DevOps   (name "duet-lokal", deps: claude-agent-sdk>=0.2.186, httpx>=0.27; dev: pytest, pytest-asyncio; console script duet=duet.cli:main)
duet/__init__.py          Backend  (__version__ = "0.1.0")
duet/config.py            Backend
duet/telegram.py          Backend
duet/agent.py             Backend
duet/approvals.py         Backend
duet/scheduler.py         Backend
duet/workspace.py         Backend  (+ duet/templates/CLAUDE.md, duet/templates/memory/README.md)
duet/app.py               Backend  (Orchestrator)
duet/policy.py            Security
duet/cli.py               DevOps
deploy/duet.service       DevOps
install.sh                DevOps
tests/                    jede Rolle ihre eigenen; QA ergänzt
docs/SICHERHEIT.md        Security
README.md                 Tech-Writer (Deutsch)
```

## Schnittstellen (exakt so implementieren)

### duet/config.py
```python
@dataclass
class Config:
    telegram_token: str
    chat_id: int | None            # None bis gekoppelt
    workspace: Path                # default ~/duet-home
    timezone: str = "Europe/Berlin"
    heartbeat_hours: float = 3.0
    active_from: str = "07:00"     # Heartbeats nur in diesem Fenster
    active_until: str = "22:00"
    morning_brief: str = "07:30"   # "" = aus
    model: str | None = None       # None = Claude-Code-Standard
    approval_timeout_min: int = 30
    paused: bool = False
CONFIG_PATH = Path(os.environ.get("DUET_CONFIG", "~/.config/duet/config.toml")).expanduser()
def load_config(path: Path = CONFIG_PATH) -> Config
def save_config(cfg: Config, path: Path = CONFIG_PATH) -> None   # schreibt TOML, chmod 600
```
Env-Override: `DUET_TELEGRAM_TOKEN` überschreibt telegram_token.

### duet/policy.py
```python
class Tier(Enum): AUTO = "auto"; ASK = "ask"; BLOCK = "block"
@dataclass(frozen=True)
class Decision: tier: Tier; reason: str; summary: str   # summary = kurze deutsche Beschreibung der Aktion für die Freigabe-Nachricht
def classify(tool_name: str, tool_input: dict) -> Decision
```
Muss MCP-Tools (`mcp__<server>__<tool>`), Composio-Meta-Tools (z. B. `mcp__composio__COMPOSIO_MULTI_EXECUTE_TOOL`
mit `tools: [{tool_slug, arguments}]`, auch andere Server-Namen wie `mcp__Composio__...`), Bash, Write/Edit
(innerhalb Workspace AUTO, außerhalb ASK), WebFetch/WebSearch/Read/Grep/Glob (AUTO) abdecken.
Unbekannt = ASK (fail closed). Bei mehreren Composio-Slugs gilt die strengste Stufe.

### duet/telegram.py
```python
class TelegramClient:
    def __init__(self, token: str, http: httpx.AsyncClient | None = None, base_url: str = "https://api.telegram.org")
    async def get_updates(self, offset: int | None, timeout: int = 50) -> list[dict]
    async def send_message(self, chat_id: int, text: str, buttons: list[list[tuple[str, str]]] | None = None) -> int  # returns message_id; Text >4000 Zeichen wird gesplittet; buttons = Zeilen von (label, callback_data); parse_mode NICHT setzen (Plaintext, robust)
    async def edit_message(self, chat_id: int, message_id: int, text: str) -> None
    async def answer_callback(self, callback_id: str, text: str = "") -> None
    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None
    async def close(self) -> None
```

### duet/approvals.py
```python
class ApprovalBroker:
    def __init__(self, tg: TelegramClient, chat_id: int, timeout_s: float)
    async def request(self, decision: Decision, tool_name: str, tool_input: dict) -> bool   # schickt Nachricht mit Buttons "Freigeben"/"Ablehnen" (callback_data "ap:<id>:y"/"ap:<id>:n"), wartet; Timeout = False und Nachricht wird editiert
    def handle_callback(self, data: str) -> bool     # vom App-Update-Loop aufgerufen; True wenn erkannt
```

### duet/agent.py
```python
class DuetAgent:
    def __init__(self, cfg: Config, can_use_tool: Callable[[str, dict, Any], Awaitable[PermissionResultAllow | PermissionResultDeny]], state: "State")
    async def ask(self, prompt: str) -> str     # ein Turn in der fortlaufenden Sitzung, gibt Antworttext zurück; serialisiert per asyncio.Lock
    async def reset(self) -> None                # neue Sitzung
    async def close(self) -> None
```
Nutzt `ClaudeSDKClient` mit `ClaudeAgentOptions(cwd=cfg.workspace, setting_sources=["user","project"], can_use_tool=..., resume=state.session_id, model=cfg.model, permission_mode="default", system_prompt={"type":"preset","preset":"claude_code","append": DUET_APPEND})`.
Speichert session_id aus ResultMessage/SystemMessage in state. Bei Fehler beim Resume: neue Sitzung.

### duet/scheduler.py
```python
@dataclass
class Job: kind: Literal["heartbeat","brief"]; at: datetime
def next_job(now: datetime, cfg: Config, last_heartbeat: datetime | None, last_brief_date: date | None) -> Job   # reine Funktion, tz-aware
HEARTBEAT_PROMPT: str ; BRIEF_PROMPT: str
SILENT_TOKEN = "NICHTS"   # Antwort exakt/beginnend damit => nichts an Nutzer senden
```

### duet/workspace.py
```python
@dataclass
class State: session_id: str | None = None; last_heartbeat: datetime | None = None; last_brief_date: date | None = None; update_offset: int | None = None
def ensure_workspace(path: Path) -> None   # legt Ordner, CLAUDE.md (aus Template, nur falls fehlt), memory/ an
def load_state(ws: Path) -> State ; def save_state(ws: Path, st: State) -> None   # <ws>/.duet/state.json
```

### duet/app.py
`async def run(cfg: Config) -> None`: Telegram-Long-Poll-Loop + Scheduler-Loop + Agent. Akzeptiert nur cfg.chat_id.
Kommandos: /start, /status, /check (Heartbeat sofort), /brief, /pause, /weiter, /neu (Sitzung neu), /hilfe.
Normale Nachricht => agent.ask(text) => Antwort zurück. Heartbeat/Brief => Antwort nur senden, wenn nicht SILENT_TOKEN.
can_use_tool: policy.classify → AUTO allow, BLOCK deny (mit Grund), ASK → ApprovalBroker.request.
Fehler werden geloggt und kurz per Telegram gemeldet, der Prozess stirbt nicht.

### duet/cli.py
`duet init` (Token abfragen, Kopplung: wartet auf erste Nachricht an den Bot, speichert chat_id, legt Workspace an),
`duet run`, `duet status`, `duet doctor` (prüft claude CLI, Login, Python-Deps, Config, Telegram getMe, `claude mcp list`),
`duet connect <name> -- <claude mcp add args...>` (wrapper um `claude mcp add --scope user`), `duet connections`.

---

# KURSWECHSEL (gilt vor allem oben, 20.11. 20:00 UTC)

Max will es nicht nur auf Linux, sondern überall und möglichst nativ in Claude: als **Claude-Code-Plugin**
(Skills + MCP-Server + Hook), installierbar auf macOS/Windows/Linux per `/plugin install`, und als
Remote-MCP nutzbar. Proaktivität kommt nicht mehr aus einem eigenen Daemon, sondern aus Claude selbst:
- **Cloud (empfohlen, kein eigener Rechner):** Claude Code Scheduled Triggers/Routinen rufen alle 3 h den Skill
  `duet:heartbeat` und morgens `duet:brief` auf. Die Routine hat Max' Konnektoren (Composio, GitHub ...).
- **Lokal (jedes OS):** `/loop 3h /duet:heartbeat` in einer offenen Claude-Code-Sitzung, oder OS-Scheduler
  (cron/launchd/Aufgabenplanung) mit `claude -p "/duet:heartbeat"`.
- Rückkanal zum Handy: Telegram-Bot (oder ntfy.sh als reiner Push), über den Duet-MCP-Server.

## Neues Layout (zusätzlich/ersetzend)
```
.claude-plugin/plugin.json        DevOps   (name "duet")
.claude-plugin/marketplace.json   DevOps   (Marketplace "duet-lokal", ein Plugin "duet", source "./")
.mcp.json                         DevOps   (Server "duet": command "uvx", args ["--from", "${CLAUDE_PLUGIN_ROOT}", "duet-mcp"]; env DUET_CONFIG optional)
hooks/hooks.json                  DevOps   (PreToolUse, matcher ".*" -> "uvx --from ${CLAUDE_PLUGIN_ROOT} duet-hook")
skills/heartbeat/SKILL.md         Backend  (Routine: Konten prüfen, nur Wichtiges per duet notify, Dedup per duet seen/mark_seen)
skills/brief/SKILL.md             Backend  (Morgen-Briefing -> notify)
skills/setup/SKILL.md             DevOps   (führt durch Bot anlegen, `duet-setup`, Trigger anlegen, Konnektoren prüfen)
duet/mcp_server.py                Backend  (FastMCP aus Paket `mcp`, stdio default, `--http PORT` für Remote-Nutzung)
duet/hook.py                      Security (liest Hook-JSON von stdin, nutzt policy.classify + approvals-Store)
duet/store.py                     Backend  (plattformneutraler Zustand: Datei unter Config-Dir; seen-Liste, Freigaben, Telegram-offset)
```
Entfällt: duet/app.py, duet/agent.py, deploy/duet.service, systemd-Fokus in install.sh (install.sh/ install.ps1 nur optional).
Console scripts: `duet-mcp=duet.mcp_server:main`, `duet-hook=duet.hook:main`, `duet-setup=duet.cli:main` (cli: init/doctor/status).
Deps: mcp>=2.3, httpx. Keine claude-agent-sdk-Abhängigkeit mehr.

## Config plattformneutral
Config-Dir: env `DUET_HOME`, sonst Windows `%APPDATA%\duet`, macOS `~/Library/Application Support/duet`, sonst `$XDG_CONFIG_HOME/duet` bzw. `~/.config/duet`.
`config.toml` dort. Env-Overrides für Cloud-Routinen (keine Dateien dort): `DUET_TELEGRAM_TOKEN`, `DUET_CHAT_ID`, `DUET_NTFY_TOPIC`, `DUET_TIMEZONE`.

## MCP-Tools (Server "duet")
- `notify(text, urgent=false)` -> Nachricht ans Handy (Telegram, sonst ntfy). Gibt message_id zurück.
- `ask_user(question, options: list[str] | None, timeout_min=10)` -> stellt Frage (Buttons), wartet auf Antwort (max. 15 min, QA: MCP-Tool-Timeout in .mcp.json = 20 min); gibt Antwort oder "timeout".
- `request_approval(tool_name, tool_input: dict, why: str)` -> nutzt policy.classify für summary; BLOCK => sofort abgelehnt; sonst Telegram-Buttons Freigeben/Ablehnen, wartet (timeout). Bei Freigabe wird im store eine einmalige Freigabe (sha256 von tool_name + kanonischem JSON des input, 15 min gültig) gespeichert. Rückgabe "approved"/"denied"/"timeout".
- `inbox(since_minutes=1440)` -> neue Telegram-Nachrichten von Max (für Aufträge, die er zwischendurch geschickt hat).
- `seen(keys: list[str])` -> welche schon gemeldet; `mark_seen(keys: list[str])`.
- `remember(note)` / `recall(query="")` -> einfache Notizen-Datei im Config-Dir (memory.md), für Umgebungen ohne Dateizugriff.
- `status()` -> Konfig-Zustand (Token maskiert), Kanal, letzte Meldung.

## Hook-Verhalten (duet/hook.py)
AUTO -> keine Ausgabe (normaler Ablauf). BLOCK -> permissionDecision "deny" mit Grund.
ASK -> wenn einmalige Freigabe im store passt: verbrauchen und "allow". Sonst:
  - interaktiv (Standard): "ask" (Claude Code fragt lokal nach).
  - unbeaufsichtigt (`DUET_UNATTENDED=1`, in Routinen/`claude -p` setzen): "deny" mit Grund
    "Freigabe nötig: rufe zuerst mcp duet request_approval mit genau diesem tool_name/tool_input auf und wiederhole dann."
Duet-eigene Tools (`mcp__*duet*__*`) sind immer AUTO.

---

# KURSWECHSEL 2 (19:57 UTC): Betrieb in der Cloud, nicht auf Max' Rechnern

Max' Linux-PC ist nicht immer an, den Arbeits-Mac will er nicht nutzen. Also: **Primärer Betrieb = Claude Code
Cloud-Routinen (Scheduled Triggers)** in Anthropics Cloud. Lokal (Plugin in Claude Code) bleibt als Zusatz.

Ablauf pro Lauf (Routine klont ein privates GitHub-Repo "duet-home" von Max, in dem dieses Plugin liegt bzw.
per .claude/settings.json `enabledPlugins`/`extraKnownMarketplaces` eingebunden ist):
1. Routine "Duet Heartbeat": stündlich 07–22 Uhr Ortszeit, Prompt `/duet:heartbeat`.
2. Routine "Duet Briefing": täglich 07:30, Prompt `/duet:brief`.
3. Skill liest zuerst `inbox` (Nachrichten, die Max dem Bot seit dem letzten Lauf geschickt hat) und erledigt sie
   als Aufträge, prüft dann Konten (Composio/GitHub/... Konnektoren der Routine), meldet Wichtiges per `notify`.
4. Freigaben: `request_approval` wartet höchstens `DUET_APPROVAL_TIMEOUT_MIN` (Default 10). Timeout => Aktion
   wird als offen in `state` vermerkt (`pending_approvals`) und beim nächsten Lauf erneut vorgelegt; die
   Telegram-Button-Antwort von Max zwischen den Läufen wird über `inbox`/callback_query beim nächsten Lauf gelesen.
5. Am Ende: Zustand persistieren. **Zustand liegt im Repo** unter `state/` (DUET_STATE_DIR), die Skills
   committen und pushen `state/` am Ende jedes Laufs (`git pull --rebase --autostash origin main && git add state && (git diff --cached --quiet || git commit -m "duet: state") && git push origin HEAD:main` – QA 20.11.: ohne --autostash bricht pull bei geändertem state/ ab; diff-Klammer macht "nothing to commit" zu Exit 0; HEAD:main, weil Cloud-Läufe auf claude/-Branches laufen können).
   Telegram-offset wird per getUpdates-Bestätigung serverseitig gehalten, zusätzlich in state gespeichert.
6. Secrets als Umgebungsvariablen der Cloud-Umgebung: DUET_TELEGRAM_TOKEN, DUET_CHAT_ID, DUET_TIMEZONE,
   DUET_UNATTENDED=1, DUET_STATE_DIR=state. Netzwerk-Policy der Umgebung muss api.telegram.org (und ggf. ntfy.sh) erlauben.

Store-Pfad: `DUET_STATE_DIR` (relativ = relativ zum CWD) > Config-Dir. Alle Store-Dateien JSON, klein, diff-freundlich (sortierte Keys, eine Datei je Thema: seen.json, approvals.json, pending.json, telegram.json, memory.md).
Template-Repo: Verzeichnis `template-duet-home/` im Plugin-Repo mit CLAUDE.md (Persona/Regeln), .claude/settings.json (Plugin aktivieren + Marketplace), state/.gitkeep, README.
Antwortlatenz auf Telegram-Nachrichten = Intervall der Routine (stündlich). Das ist bewusst; Sofort-Chat bleibt Claude-App/Projects.
