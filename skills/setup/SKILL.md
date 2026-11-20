---
name: setup
description: Richtet Duet für Max komplett ohne eigenen Rechner ein. Telegram-Bot anlegen, chat_id im Browser ablesen, privates Repo duet-home auf github.com aus der Vorlage erstellen, Cloud-Umgebung mit Variablen und Netzwerkfreigabe anlegen, zwei Routinen auf claude.ai/code (Heartbeat stündlich 07–22 Uhr, Briefing 07:30) und Konnektoren prüfen. Verwenden bei "Duet einrichten", "duet setup" oder wenn Heartbeat/Briefing noch nicht laufen.
---

# Duet einrichten

Du führst Max durch die Einrichtung von Duet. Duet läuft **ausschließlich in Anthropics Cloud** als zwei
geplante Routinen (Scheduled Tasks), die bei jedem Lauf sein privates Repo `stroexd/duet-home` klonen.
**Auf Max' eigenen Rechnern muss nichts installiert sein oder laufen** – alles passiert in Telegram,
auf github.com und auf claude.ai/code (geht auch vom Handy oder Arbeits-Mac im Browser).
Telegram ist der Rückkanal aufs Handy.

Arbeitsweise:
- Deutsch, du-Form, ein Schritt nach dem anderen. Nach jedem Schritt kurz bestätigen lassen.
- Schritte im Browser oder in Telegram beschreibst du genau (wo klicken, was eintragen).
- **Den Bot-Token nie anfordern, ausgeben, in Dateien schreiben oder committen.** Max trägt ihn nur in
  die Cloud-Umgebung ein. Bittet er dich, ihn zu speichern: ablehnen und auf Schritt 4 verweisen.
- Nichts davon setzt ein lokales Terminal, `uv`, `gh` oder `git` auf Max' Rechner voraus.
- Hast du selbst Zugriff (z. B. GitHub-Konnektor/`gh` in einer Cloud-Sitzung), darfst du prüfen, was
  schon erledigt ist (Repo vorhanden? Dateien vollständig?), und das überspringen.

Feste Namen: Plugin-Repo `stroexd/duet` (öffentlich), Daten-Repo `stroexd/duet-home` (privat),
Cloud-Umgebung `Duet`, Routinen `Duet Heartbeat` und `Duet Briefing`.

## Schritt 0: Voraussetzungen

- Claude Pro, Max, Team oder Enterprise; auf **claude.ai/code** ist GitHub verbunden
  (claude.ai/code → GitHub verbinden bzw. Claude GitHub App für `stroexd/duet-home` zulassen).
- Das Plugin-Repo `stroexd/duet` ist **öffentlich**. Die Routinen laden den Duet-Code bei jedem Lauf mit
  `uvx --from git+https://github.com/stroexd/duet`. Das Repo **nicht** als zweites Repository in die
  Routine aufnehmen: Sitzungen mit mehreren Repositories laden weder `.mcp.json` noch die Hooks aus
  `.claude/settings.json` – dann fehlen MCP-Server und Freigabe-Hook.

## Schritt 1: Telegram-Bot anlegen

1. In Telegram den Chat mit **@BotFather** öffnen, `/newbot` senden.
2. Anzeigename wählen (z. B. "Duet") und einen Benutzernamen, der auf `bot` endet (z. B. `max_duet_bot`).
3. BotFather schickt den **Token** (`123456789:AA…`). Max bewahrt ihn sicher auf (Passwortmanager) und
   gibt ihn niemandem – auch nicht in diesem Chat.

## Schritt 2: chat_id ermitteln (im Browser, ohne Terminal)

1. Max öffnet in Telegram den Chat mit seinem neuen Bot, tippt **Start** und schickt danach eine
   beliebige Nachricht, z. B. `hallo`.
2. Er öffnet im Browser (Handy oder beliebiger Rechner) diese Adresse, `<TOKEN>` durch seinen Token
   ersetzt, ohne spitze Klammern:
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
3. In der Antwort sucht er `"chat":{"id":` – die Zahl dahinter (z. B. `"chat":{"id":123456789,`) ist die
   **chat_id**. Bei privaten Chats ist sie positiv. Er prüft, dass bei `"first_name"` bzw. `"username"`
   daneben sein eigener Name steht.
4. Steht dort nur `{"ok":true,"result":[]}`: noch eine Nachricht an den Bot schicken und die Seite neu
   laden. Steht dort `"ok":false` mit `Unauthorized`, ist der Token falsch kopiert.
5. Danach den Browser-Tab schließen (die URL enthält den Token; ggf. aus dem Verlauf löschen).
   Die chat_id ist kein Geheimnis, die darf Max dir sagen.

Duet akzeptiert ausschließlich Nachrichten und Button-Klicks aus genau diesem Chat.

## Schritt 3: Privates Repo `stroexd/duet-home` aus der Vorlage (auf github.com)

Cloud-Routinen installieren **keine Plugins** (auch nicht aus `enabledPlugins` in `.claude/settings.json`).
Deshalb liegt alles, was die Routine braucht, direkt im Repo – die Vorlage `template-duet-home/` im
Plugin-Repo ist bereits vollständig:
`CLAUDE.md`, `.mcp.json` (MCP-Server duet), `.claude/settings.json` (Freigabe-Hook),
`.claude/skills/duet-heartbeat/` und `.claude/skills/duet-brief/` (Befehle `/duet-heartbeat`, `/duet-brief`),
`state/.gitkeep`, `.gitignore`.

1. Auf **github.com/new**: Name `duet-home`, **Private**, Häkchen **Add a README file** (damit `main`
   existiert), **Create repository**. Keine Branch-Protection/Rulesets auf `main` anlegen: jeder Lauf
   committet seinen Zustand nach `state/` direkt auf `main`.
2. Vorlage hineinkopieren – ohne eigenen Rechner per Cloud-Sitzung: auf **claude.ai/code** eine neue
   Sitzung mit Repository `stroexd/duet-home` (Standard-Umgebung reicht) starten und schreiben:
   ```
   Klone https://github.com/stroexd/duet nach /tmp/duet und kopiere den Inhalt von
   /tmp/duet/template-duet-home/ (inklusive .claude/, .mcp.json, .gitignore, state/.gitkeep) in dieses Repo.
   Committe mit "duet-home: Vorlage" und pushe direkt auf den Branch main (kein claude/-Branch).
   ```
   Sitzung danach archivieren. (Alternative ohne Claude: Dateien einzeln auf github.com mit
   **Add file → Create new file** anlegen, Pfade wie oben, Inhalte aus `stroexd/duet/template-duet-home/`.)
3. `CLAUDE.md` in `duet-home` mit Max kurz durchgehen (wichtige Personen, was gemeldet werden soll) und
   auf github.com über den Stift-Button anpassen.

Bei einem Plugin-Update: Schritt 3.2 mit "aktualisiere .claude/skills/ und .mcp.json aus der Vorlage"
wiederholen. Der Python-Code selbst kommt bei jedem Lauf frisch aus `stroexd/duet`.

## Schritt 4: Cloud-Umgebung "Duet"

Auf **claude.ai/code** in der neuen Sitzungsansicht die Umgebungsauswahl (erstes Element unter dem
Eingabefeld) öffnen → **New environment**:

- **Name:** `Duet` (eigene, nicht geteilte Umgebung – Variablen sind für alle Nutzer der Umgebung lesbar).
- **Network access:** **Custom**. Unter **Allowed domains** `api.telegram.org` eintragen (bei ntfy
  zusätzlich `ntfy.sh`) und **Also include default list of common package managers** anhaken
  (PyPI für `uvx`). GitHub und Konnektoren laufen über eigene Proxys und brauchen keinen Eintrag.
- **Environment variables** (`.env`-Format, eine Zeile je Variable; Token und chat_id aus Schritt 1/2):
  ```
  DUET_TELEGRAM_TOKEN=123456789:AA...
  DUET_CHAT_ID=123456789
  DUET_TIMEZONE=Europe/Berlin
  DUET_UNATTENDED=1
  DUET_STATE_DIR=state
  DUET_APPROVAL_TIMEOUT_MIN=10
  ```
  Der Telegram-Token steckt in der URL, nicht in einem Header; als *Network secret* geht er daher nicht.
- **Setup script** (optional, beschleunigt jeden Lauf über den Umgebungs-Cache):
  ```
  uvx --from git+https://github.com/stroexd/duet duet-setup --version
  ```
- **Add environment**.

## Schritt 5: Zwei Routinen anlegen

Auf **claude.ai/code/routines** → neue Routine. Für beide gilt: **genau ein** Repository
`stroexd/duet-home`, Umgebung **Duet**, unter **Connectors** alle Konnektoren behalten, die Duet prüfen
soll (z. B. Gmail, Google Calendar, GitHub, Composio), alle anderen entfernen.

1. **Duet Heartbeat**
   - Zeitplan: zuerst "Hourly" wählen, nach dem Anlegen im Bearbeiten-Dialog **Custom**:
     `CRON_TZ=Europe/Berlin 5 7-22 * * *` (stündlich um :05 von 07 bis 22 Uhr; öfter als stündlich
     ist nicht erlaubt; :05 statt :00, weil volle Stunden verspätet starten können).
   - Prompt:
     ```
     /duet-heartbeat
     Sichere state/ am Ende mit genau dem Befehl aus dem Skill direkt auf den Branch main (kein claude/-Branch, kein Pull Request).
     ```
2. **Duet Briefing**
   - Zeitplan: täglich, Custom `CRON_TZ=Europe/Berlin 30 7 * * *`
   - Prompt:
     ```
     /duet-brief
     Sichere state/ am Ende mit genau dem Befehl aus dem Skill direkt auf den Branch main (kein claude/-Branch, kein Pull Request).
     ```

Andere Zeitzone: `CRON_TZ` und `DUET_TIMEZONE` gleich setzen.

## Schritt 6: Testlauf und Konnektoren prüfen

1. Bei "Duet Heartbeat" auf **Run now** klicken. Den Lauf in der History öffnen und prüfen:
   - MCP-Server `duet` ist verbunden (Tools `mcp__duet__notify`, `inbox`, …). Fehlt er: Routine hat
     mehr als ein Repository, oder PyPI/`api.telegram.org` ist in der Umgebung nicht erlaubt.
   - `status` meldet `channel: telegram`. Sonst Variablen der Umgebung prüfen.
   - Konnektoren antworten. Fehlt einer: claude.ai → Einstellungen → Connectors verbinden bzw. neu
     anmelden und in der Routine aktivieren.
   - Am Ende gibt es auf github.com einen Commit "duet: state" auf `main` (nur wenn sich etwas geändert hat).
2. Max schickt seinem Bot einen Auftrag, z. B. "Was steht morgen im Kalender?", und startet den Heartbeat
   erneut mit **Run now**. Die Antwort kommt per Telegram.
3. Freigaben testen: Max lässt Duet z. B. eine Mail senden. Es kommt eine Telegram-Nachricht mit
   **Freigeben/Ablehnen**. Der Lauf wartet bis zu 10 Minuten (`DUET_APPROVAL_TIMEOUT_MIN`, höchstens 15;
   der MCP-Server hat in `.mcp.json` ein Tool-Timeout von 20 Minuten). Ohne Antwort bleibt die Freigabe
   offen und wird beim nächsten Lauf erneut vorgelegt; ein späterer Button-Klick wird dann übernommen.

Erkläre Max zum Schluss: Antworten auf Telegram-Nachrichten kommen mit dem nächsten Lauf (höchstens
eine Stunde). Für sofortigen Chat bleibt die Claude-App. Pausieren: die Routinen auf
claude.ai/code/routines ausschalten. Token wechseln: bei @BotFather `/revoke`, neuen Token in der
Umgebung "Duet" eintragen.

Wenn ein Lauf auf eine Rückfrage wartet (Routinen laufen im Auto-Modus; nach mehreren blockierten
Aktionen kann Claude Code im Lauf um Bestätigung bitten): Lauf auf claude.ai/code öffnen und beantworten
oder abbrechen. Gesperrte Aktionen (Geld bewegen) gibt es nie.

## Optional: lokal mitnutzen (nicht nötig)

In Claude Code auf einem beliebigen Rechner:
```
/plugin marketplace add stroexd/duet
/plugin install duet@duet-lokal
```
Dann laufen Hook und MCP-Server auch lokal (Befehle `/duet:heartbeat`, `/duet:brief`), z. B. mit
`/loop 3h /duet:heartbeat` in einer offenen Sitzung. Lokal ist `DUET_UNATTENDED` nicht gesetzt:
Freigaben fragt Claude Code dann direkt im Terminal.

Nur für lokale Nutzung gibt es zusätzlich `uvx --from git+https://github.com/stroexd/duet duet-setup init`
(koppelt den Bot per Code und speichert die Konfiguration im lokalen Config-Verzeichnis) und
`duet-setup doctor`. Für den Cloud-Betrieb wird beides nicht gebraucht.
