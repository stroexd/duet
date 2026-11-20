# duet

Ein proaktiver Assistent nach dem Vorbild von Anthropics **Claude Duet**, gebaut als Claude-Code-Plugin.
Wie Duet prüft er deine verbundenen Konten regelmäßig, schickt morgens ein Briefing, meldet sich nur bei
Wichtigem und fragt vor jeder Aktion nach außen per Button auf dem Handy (hier: Telegram) um Freigabe.

Anders als Duet:

- Kein Dauerprozess und keine eigene Cloud-VM mit Browser. Duet läuft als **Claude Code Cloud-Routinen**
  (stündlicher Heartbeat, Briefing um 07:30) und nutzt die Konnektoren der Routine (Composio, GitHub ...).
- Antworten auf deine eigenen Telegram-Nachrichten kommen erst **beim nächsten Routine-Lauf** (höchstens
  eine Stunde). Für sofortigen Chat bleibt die Claude-App.
- Auf deinen eigenen Rechnern muss nichts laufen.

## Wie es funktioniert

- **Routinen** auf claude.ai/code: "Duet Heartbeat" und "Duet Briefing" klonen bei jedem Lauf das private
  Repo `stroexd/duet-home`.
- **Skills** `/duet-heartbeat` und `/duet-brief` (in `duet-home/.claude/skills/`, lokal als Plugin
  `/duet:heartbeat`, `/duet:brief`): erst `inbox` lesen und Aufträge erledigen, dann Konten prüfen,
  Wichtiges per `notify` melden.
- **MCP-Server `duet`** (`uvx --from git+https://github.com/stroexd/duet duet-mcp`) mit den Tools
  `notify`, `ask_user`, `request_approval`, `pending_approvals`, `inbox`, `seen`, `mark_seen`,
  `remember`, `recall`, `status`.
- **Freigabe-Hook** `duet-hook` (PreToolUse) prüft jeden Tool-Aufruf. In Routinen (`DUET_UNATTENDED=1`)
  wird eine Aktion ohne Telegram-Freigabe abgelehnt.
- **Zustand** (gesehene Meldungen, Freigaben, Telegram-offset, Notizen) liegt als JSON in `state/` von
  `duet-home`. Jeder Lauf committet und pusht ihn direkt auf `main`.

```
 Telegram (Handy)                  claude.ai/code Routine (stündlich 07-22, 07:30)
 +-------------+   Bot-API        +------------------------------------------------+
 | Nachrichten | <--------------> | Klon von stroexd/duet-home                     |
 | Buttons     |                  |  /duet-heartbeat, /duet-brief  (Skills)        |
 +-------------+                  |  MCP "duet"  -> notify, inbox, request_approval|
                                  |  Hook "duet-hook" -> Freigabestufen            |
                                  |  Konnektoren (Composio, GitHub, Gmail ...)     |
                                  +------------------------+-----------------------+
                                                           | git push state/ -> main
                                                           v
                                              github.com/stroexd/duet-home (privat)
```

## Einrichtung (nur Browser und Telegram)

Ausführlich und interaktiv: Skill `setup` (`skills/setup/SKILL.md`). Kurzfassung:

Voraussetzung: Claude Pro, Max, Team oder Enterprise; auf claude.ai/code ist GitHub verbunden.

1. **Telegram-Bot:** bei **@BotFather** `/newbot` senden, Namen wählen (Benutzername endet auf `bot`).
   Den Token sicher aufbewahren, nie committen oder in einen Chat kopieren.
2. **chat_id:** dem neuen Bot **Start** und eine beliebige Nachricht schicken, dann im Browser
   `https://api.telegram.org/bot<TOKEN>/getUpdates` öffnen. Die Zahl hinter `"chat":{"id":` ist die
   chat_id. Tab danach schließen (die URL enthält den Token).
3. **Repos auf github.com:**
   - `stroexd/duet`: **öffentlich**, enthält dieses Plugin. Die Routinen laden den Code per
     `uvx --from git+https://github.com/stroexd/duet`.
   - `stroexd/duet-home`: **privat**, mit **Add a README file** anlegen, keine Branch-Protection auf `main`.
     Inhalt von `template-duet-home/` hineinkopieren (inklusive `.claude/`, `.mcp.json`, `.gitignore`,
     `state/.gitkeep`), z. B. über eine Cloud-Sitzung auf claude.ai/code mit Repository `stroexd/duet-home`:
     ```
     Klone https://github.com/stroexd/duet nach /tmp/duet und kopiere den Inhalt von
     /tmp/duet/template-duet-home/ (inklusive .claude/, .mcp.json, .gitignore, state/.gitkeep) in dieses Repo.
     Committe mit "duet-home: Vorlage" und pushe direkt auf den Branch main (kein claude/-Branch).
     ```
     Danach `CLAUDE.md` in `duet-home` anpassen (wichtige Personen, was gemeldet werden soll).
4. **Cloud-Umgebung** auf claude.ai/code → Umgebungsauswahl → **New environment**:
   - Name `Duet` (eigene, nicht geteilte Umgebung).
   - Network access **Custom**, Allowed domains `api.telegram.org` (bei ntfy zusätzlich `ntfy.sh`),
     **Also include default list of common package managers** anhaken (PyPI für `uvx`).
   - Environment variables:
     ```
     DUET_TELEGRAM_TOKEN=123456789:AA...
     DUET_CHAT_ID=123456789
     DUET_TIMEZONE=Europe/Berlin
     DUET_UNATTENDED=1
     DUET_STATE_DIR=state
     DUET_APPROVAL_TIMEOUT_MIN=10
     ```
   - Setup script (optional, Cache): `uvx --from git+https://github.com/stroexd/duet duet-setup --version`
5. **Zwei Routinen** auf claude.ai/code/routines, jeweils **genau ein** Repository `stroexd/duet-home`
   (nicht zusätzlich `stroexd/duet`, sonst fehlen MCP-Server und Hook), Umgebung **Duet**:

   | Routine | Zeitplan (Custom) | Prompt |
   |---|---|---|
   | Duet Heartbeat | `CRON_TZ=Europe/Berlin 5 7-22 * * *` | siehe unten |
   | Duet Briefing | `CRON_TZ=Europe/Berlin 30 7 * * *` | siehe unten |

   Heartbeat ("Hourly" wählen, nach dem Anlegen auf Custom umstellen):
   ```
   /duet-heartbeat
   Sichere state/ am Ende mit genau dem Befehl aus dem Skill direkt auf den Branch main (kein claude/-Branch, kein Pull Request).
   ```
   Briefing:
   ```
   /duet-brief
   Sichere state/ am Ende mit genau dem Befehl aus dem Skill direkt auf den Branch main (kein claude/-Branch, kein Pull Request).
   ```
   Andere Zeitzone: `CRON_TZ` und `DUET_TIMEZONE` gleich setzen.
6. **Konnektoren:** unter **Connectors** der Routine alle behalten, die Duet prüfen soll (z. B. Composio,
   GitHub, Gmail, Google Calendar), alle anderen entfernen.
7. **Testlauf:** bei "Duet Heartbeat" **Run now**. Im Lauf prüfen: MCP-Server `duet` verbunden,
   `status` meldet `channel: telegram`, Konnektoren antworten, Commit "duet: state" auf `main` (falls
   sich etwas geändert hat). Dann dem Bot einen Auftrag schicken, erneut **Run now**, Antwort kommt per
   Telegram. Pausieren: Routinen ausschalten. Token wechseln: bei @BotFather `/revoke`, neuen Token in
   der Umgebung eintragen.

Plugin-Update: Skills und `.mcp.json` in `duet-home` aus `template-duet-home/` aktualisieren. Der
Python-Code kommt bei jedem Lauf frisch aus `stroexd/duet`.

## Freigabestufen

| Stufe | Was | Beispiele |
|---|---|---|
| automatisch (AUTO) | lesen, suchen, entwerfen | `Read`, `Grep`, `WebFetch`, MCP-Tools mit Leseverb (`get`, `list`, `search`), Entwürfe (`*_draft`), Schreiben im Workspace, Duet-eigene Tools, der `state/`-Sicherungsbefehl |
| mit Freigabe (ASK) | alles, was nach außen wirkt, und alles Unbekannte | senden, antworten, posten, löschen, Termine anlegen, bezahlen/kaufen, unterschreiben, mergen, pushen, Code ausführen, Verbindungen ändern |
| nie (BLOCK) | Geld bewegen, zerstörerische Befehle | Überweisungen, Auszahlungen, Trading, Krypto-Transfers, `rm -rf /`, `mkfs` |

Freigaben sind einmalig, an Tool-Name und exakten Input gebunden und 15 Minuten gültig. Ohne Antwort
innerhalb von `DUET_APPROVAL_TIMEOUT_MIN` passiert nichts; die Aktion wird beim nächsten Lauf erneut
vorgelegt. Details und Grenzen: [docs/SICHERHEIT.md](docs/SICHERHEIT.md).

## Optional: lokal in Claude Code

Nicht nötig für den Cloud-Betrieb. In Claude Code auf macOS, Windows oder Linux:

```
/plugin marketplace add stroexd/duet
/plugin install duet@duet-lokal
```

Dann gibt es `/duet:heartbeat` und `/duet:brief`, z. B. mit `/loop 3h /duet:heartbeat`. Lokal fragt
Claude Code Freigaben direkt im Terminal. Bot lokal koppeln (Konfiguration im lokalen Config-Verzeichnis):

```
uvx --from git+https://github.com/stroexd/duet duet-setup init
```

Prüfen mit `duet-setup doctor`.

## Kosten und Grenzen

- Läuft auf deinem Pro/Max-Kontingent, ohne API-Key. Jeder Routine-Lauf verbraucht Nutzung
  (Heartbeat 16 Läufe pro Tag plus Briefing).
- Öfter als stündlich ist bei Routinen nicht möglich; Antworten auf Telegram-Nachrichten haben
  entsprechend bis zu eine Stunde Verzögerung.
- Hook und MCP-Server starten per `uvx`; die Umgebung braucht dafür PyPI-Zugriff.
- Ungetestet: echter Telegram-Betrieb und ein echter Cloud-Routine-Lauf. Die Tests nutzen einen
  nachgebauten Telegram-Server.

## Entwicklung

```
uv run --extra dev pytest -q        # alle Tests
uv run --extra dev pytest -q -m "not slow"  # ohne End-to-End-Tests (Marker slow)
python scripts/sync_template.py     # Skills nach template-duet-home/.claude/skills/ kopieren
python scripts/sync_template.py --check
```

Pakete: `duet/` (MCP-Server, Hook, Policy, Store, Telegram, CLI), `skills/`, `hooks/`, `.mcp.json`,
`template-duet-home/`. Architektur: [ARCHITECTURE.md](ARCHITECTURE.md). Lizenz: MIT.
