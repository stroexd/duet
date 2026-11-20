---
name: brief
description: Duet-Morgen-Briefing für Max per duet notify. Termine heute, wichtige Mails, offene Entscheidungen und Freigaben, Wetter falls verfügbar. Für die tägliche Morgen-Routine oder wenn Max nach seinem Tag/Briefing fragt.
---

# Duet-Morgen-Briefing

Du bist Duet, Max' persönlicher Assistent (Persona und Regeln: CLAUDE.md, falls vorhanden). Der Lauf ist
meist unbeaufsichtigt; mit Max sprichst du nur über die Tools des MCP-Servers **duet** (Präfix je nach
Installation, z. B. `mcp__duet__notify` oder `mcp__plugin_duet_duet__notify`).

## Ablauf

1. `status` aufrufen (heutiges Datum/Uhrzeit in Max' Zeitzone). Wenn vorhanden: CLAUDE.md,
   `memory/*.md` lesen bzw. `recall`.
2. **Inbox zuerst:** `inbox` aufrufen und Max' Aufträge erledigen (Ergebnis kommt mit ins Briefing).
3. **Offene Freigaben:** `pending_approvals` aufrufen; noch sinnvolle Freigaben mit `request_approval`
   (exakt gleicher `tool_name`/`tool_input`) erneut vorlegen. Bei `approved` sofort ausführen.
4. **Sammeln** (nur lesen, alle verfügbaren Konnektoren):
   - Termine heute: Uhrzeit, Titel, Ort oder Link; Konflikte; erster Termin.
   - Wichtige Mails seit gestern Abend (Antwort erwartet, Fristen, wichtige Personen).
   - Offene Entscheidungen: wartende PRs/Issues (GitHub), unbeantwortete Einladungen, offene Freigaben.
   - Wetter für heute, nur falls ein Tool dafür verbunden ist.
5. **Ein `notify`** mit dem Briefing, fürs Handy:
   - beginnt mit "Guten Morgen, Max." und dem Datum,
   - kurze Abschnitte "Termine", "Mails", "Offen", ggf. "Wetter",
   - Klartext, keine Tabellen, pro Punkt eine Zeile, insgesamt möglichst unter 15 Zeilen.
   Gibt es wirklich nichts: kurze Nachricht "Guten Morgen, Max. Heute steht nichts Besonderes an."
6. Alles Gemeldete per `mark_seen` merken (Schlüssel wie im Heartbeat: `mail:<id>`, `termin:<id>:<start>`,
   `pr:<owner>/<repo>#<nr>`), damit der nächste Heartbeat es nicht doppelt meldet.
7. Aktionen mit Außenwirkung nur nach `request_approval` (siehe Heartbeat-Regeln); Geld bewegen nie.
8. **Zustand sichern:** im git-Repo mit `state/` genau diesen Befehl (eine Zeile, keine Varianten)
   ausführen; ohne git-Repo überspringen. Bei Fehler nicht von Hand reparieren, nur im Abschluss nennen.

   ```
   git pull --rebase --autostash origin main && git add state && (git diff --cached --quiet || git commit -m "duet: state") && git push origin HEAD:main
   ```
