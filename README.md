# ABUS Secvest für Home Assistant

Custom Integration zur Anbindung einer ABUS Secvest Alarmanlage an Home Assistant.

Diese Integration ist nicht offiziell von ABUS und nicht mit ABUS verbunden.

Dies ist ein Fork von [dkmouk/secvest-ha](https://github.com/dkmouk/secvest-ha). Vielen Dank an dkmouk für die Basis dieser Integration, und an Jochen aka Birdy aus dem alarmforum.de, der dkmouk seine Dateien zur Verfügung gestellt hat und dessen Ideen darin eingeflossen sind.

## Funktionen

- Alarmzentrale für Scharf, Teilscharf und Unscharf
- Melder und Zonen als Binary Sensors
- Übersicht offener Melder
- RSSI-/Funksignalwerte für Funkmelder
- Batterie-, Funk-, Sabotage- und Supervisionsstatus
- Interne Überwachung und Ausblendbarkeit der Melder
- Fault-Abfrage und Zuordnung von Meldungen zu einzelnen Zonen
- Ausgänge der Secvest als Diagnoseinformationen
- Buttons für Aktualisierung und Fault-Quittierung
- Einrichtung und Optionen über die Home-Assistant-Oberfläche
- Einstellbare Abfrageintervalle
- Lokales Polling ohne Cloud
- Deutsch- und englischsprachige Oberfläche
- Diagnoseexport zur Fehlersuche

## Neu in Version 0.3.12

Der Log-Lesestand (zuletzt verarbeiteter Zeitstempel, plus `log_history` und `mode_change_history`) wird jetzt über HAs Storage-Mechanismus persistiert und beim Start der Integration wiederhergestellt. Dadurch wird das Ereignisprotokoll nach einem Neustart oder Reload immer lückenlos ab dem zuletzt in HA gespeicherten Eintrag weitergelesen, statt bei jedem Start erneut ohne Filter die letzten bis zu 600 Einträge abzufragen (langsamer) bzw. dazwischen liegende Einträge zu verpassen, falls in der Zwischenzeit mehr als 600 Einträge angefallen sind.

## Neu in Version 0.3.11

Die Benutzer-Zuordnung über das Ereignisprotokoll funktioniert jetzt nachträglich und für jeden erkannten Scharf-/Unscharf-Wechsel, nicht mehr nur für über HA ausgelöste Schaltungen: Jeder Wechsel (egal ob über HA oder am Bedienteil ausgelöst) wird beim Erkennen in einer neuen Historie `mode_change_history` registriert, und sobald ein passender Log-Eintrag mit Benutzerbezug verfügbar ist - auch erst bei einem späteren planmäßigen Poll -, wird er nachträglich zugeordnet (zeitlich nächster noch nicht verwendeter Eintrag, max. 2 Minuten Abstand). Das neue Attribut `mode_change_history` am Alarm-Panel zeigt diese Historie (Zeitstempel, von/nach welchem Rohzustand, und sobald bekannt: Benutzer) direkt an der Entität, die den Zustand repräsentiert.

Außerdem behoben: Beim Scharfschalten über die Alarmzentrale in HA (nicht über den Service) wurde bei der Live-Zonenprüfung versehentlich ein komplett neues `SecvestData`-Objekt ohne die Log-/Benutzer-Felder erzeugt - dadurch gingen `log_history`, `mode_change_history` und der letzte bekannte Benutzer bei jedem Scharfschalten verloren. Jetzt wird korrekt nur das Nötige aktualisiert, der Rest bleibt erhalten.

## Neu in Version 0.3.10

Schaltet man Scharf/Unscharf über Home Assistant (Service `secvest_set_mode` bzw. die Alarmzentrale), wird jetzt zusätzlich direkt im Anschluss das Ereignisprotokoll abgefragt, statt bis zu 5 Minuten auf den nächsten planmäßigen `/logs/`-Poll zu warten (läuft im Hintergrund, verzögert die Schaltung selbst nicht). Mit kurzen Wiederholungen (bis zu 4× im Abstand von 1,5s), da die Firmware den Log-Eintrag nicht immer sofort bereitstellt.

Damit bei zwei kurz aufeinanderfolgenden Vorgängen (z. B. HA-Schaltung und fast gleichzeitig jemand am Bedienteil) nicht versehentlich ein älterer, nicht zugehöriger Eintrag zugeordnet wird, wird nur ein Log-Eintrag mit Zeitstempel ab dem Moment der Schaltung akzeptiert. Außerdem merkt sich der Sensor "Secvest Letzter Benutzer" jetzt die letzten bis zu 20 Log-Einträge mit Benutzerbezug als Attribut `history`, statt nur den jeweils letzten zu überschreiben - so geht bei schnell aufeinanderfolgenden Vorgängen keine Zuordnung mehr verloren.

Ein wirklich zeitgleicher zweiter Vorgang (z. B. exakt im selben Moment am Bedienteil) lässt sich ohne eigene Vorgangs-ID in der Secvest-Firmware nicht hundertprozentig trennen - beide Einträge landen aber unabhängig davon vollständig in der Historie.

## Neu in Version 0.3.9

Die umfangreiche Wireless-Abfrage (RSSI, Batterie, Sabotage, Supervision je Funkmelder) läuft nicht mehr im festen Minuten-Intervall, sondern nur noch **einmal täglich zu einer frei konfigurierbaren Uhrzeit** (Option "Umfangreiche Abfrage / RSSI, Uhrzeit", Standard 03:00) sowie zusätzlich jederzeit über den neuen Button "Secvest RSSI/Wireless jetzt abfragen". Hintergrund: Anders als Zonen/Fehler/Alarmzustand ist das kein leichtgewichtiger REST-Call, sondern ein kompletter Web-UI-Login-Scrape (CSRF-Token, Session, Formular-Parsen) - und RSSI/Batterie ändern sich ohnehin zu träge, um das öfter als täglich zu rechtfertigen. Das schont die Anlage deutlich, ohne bei Bedarf (z. B. nach Einbau eines neuen Funkmelders) auf den nächsten Tag warten zu müssen.

Einzige bekannte Einschränkung: Ob eine Sabotage-Erkennung zusätzlich auch über die schnelle Fehler-Abfrage (`/faults/`, 3 Sek.) gemeldet wird, ist nicht verifiziert - falls nicht, würde eine Manipulation zwischen den täglichen Abfragen bis zu 24 h unbemerkt bleiben, sofern nicht manuell per Button nachgefragt wird.

## Neu in Version 0.3.8

Neuer Diagnose-Sensor "Secvest Letzter Benutzer": fragt zusätzlich den Endpoint `/logs/` ab (eigenes Intervall über die neue Option "Ereignisprotokoll / Benutzer-Zuordnung", Standard 5 Minuten; inkrementell per `$filter=timestamp ge <n>`, damit nicht jedes Mal das komplette Log mit bis zu 600 Einträgen geladen wird) und zeigt den Benutzer-Slot bzw. Benutzernamen des letzten protokollierten Vorgangs mit Benutzerbezug. Bestätigt mit echten Log-Beispielen funktioniert das für Scharf-/Unscharfschalten (z. B. `"Ben 001 TB 1 deaktiv"`). Ob auch das Quittieren eines Fehlers oder Alarms einen Benutzer ins Log schreibt, ist bisher **nicht** mit echten Daten belegt — das korrigiert die Aussage aus Version 0.3.7, dass eine Benutzer-Identifikation generell nicht möglich sei; sie war nur für die damals abgefragten Endpoints (`state`/`zones/`/`faults/`/`output/`) richtig.

Zusätzlich beim Abgleich mit der externen Doku ([thomasdanz/secvest-api](https://github.com/thomasdanz/secvest-api)) gefunden und behoben: `set_mode()` prüfte bisher nicht, ob die Secvest den angeforderten Zustand tatsächlich übernommen hat. Bei einem von der Firmware stillschweigend ignorierten Wechsel (z. B. direkt von Teilscharf auf Scharf, ohne zuvor unscharf zu schalten) hätte HA das fälschlich als Erfolg gemeldet; jetzt wird der Soll-/Ist-Zustand verglichen und bei Abweichung ein Fehler ausgelöst.

Das offizielle Secvest-App sendet laut derselben Doku `acknowledged` aktiv per PUT, um einen Alarm zu quittieren — technisch also anfragbar, hier aber bewusst (noch) nicht als sendbarer Befehl freigegeben, da das eine neue, sicherheitsrelevante Fähigkeit wäre.

## Neu in Version 0.3.7

Neues Attribut `triggered_zone_names` am Alarm-Panel: zeigt, solange `alarm_memory` aktiv ist, die Namen der Zonen, für die gerade ein Fault vom Type `5000` mit `affects-zone` vorliegt (z. B. `["Wohnen-BM"]` oder `["Bad-Fenster"]`) — anhand von zwei echten Testalarmen (Bewegungsmelder und Fensterkontakt) gegen reale Log-Daten verifiziert.

Fault-Type `5000` bedeutet laut externer Reverse-Engineering-Doku ([thomasdanz/secvest-api](https://github.com/thomasdanz/secvest-api), [ha-secvest Issue #95](https://github.com/thomasdanz/ha-secvest/issues/95)) generell "Zone offen, blockiert Scharfschalten" — nicht alarmspezifisch, aber stabil genug, um andere, unabhängige Fault-Typen (z. B. `1170` = Repeater-Batterie niedrig) zuverlässig auszuschließen. Ein-/Ausgangszonen erzeugen laut derselben Doku bewusst keinen solchen Fault, auch wenn sie kurz offen waren (bestätigt auch in unserem eigenen Test: die Eingangstür selbst erzeugte nie einen Fault).

Eine Benutzer-Identifikation beim Quittieren eines Alarms war mit den damals abgefragten Endpoints nicht möglich — siehe Version 0.3.8 für die Erweiterung über `/logs/`.

## Neu in Version 0.3.5

Anpassung der deutschen Beschriftungen auf dem "Secvest Mode (DE)"-Sensor, basierend auf zwei real ausgewerteten Testalarmen:

- `unset-alarm`: "Alarm" → **"Eingangsweg verlassen"** — tritt auf, wenn man während der Eingangsverzögerung den vorgesehenen Eingangsweg verlässt (z. B. nicht direkt zum Bedienteil geht), statt erst beim Ablauf der Verzögerung
- `partset`: "Teilscharf" → **"Scharf (intern)"**
- `partset-alarm`: "Alarm (teilscharf)" → **"Alarm (intern)"**

Die Zuordnung zu den Home-Assistant-Standardzuständen (`armed_home`/`triggered`/etc.) ist davon nicht betroffen, nur die deutsche Anzeige.

## Neu in Version 0.3.4

- **Debug-Logging der Rohantworten:** Jede Antwort der Secvest (Zonen, Fehler, Alarmzustand, Ausgänge sowie alle Zwischenschritte der umfangreichen Web-Abfrage) wird jetzt bei aktiviertem Debug-Log für `custom_components.secvest.api` als vollständiger Rohtext mitprotokolliert. Aktivieren in der `configuration.yaml`:
  ```yaml
  logger:
    logs:
      custom_components.secvest.api: debug
  ```
  Keine neue Integrations-Option nötig — rein über die Standard-HA-Log-Konfiguration (inkl. deren Rotation/Aufbewahrung) steuerbar.
- Rohwert `unset-alarm` wird jetzt ebenfalls als `triggered`/"Alarm" behandelt (bisher `pending`/"Eingangsverzögerung") — entspricht der Beobachtung, dass dieser Wert bereits während eines laufenden Alarms auftritt.

## Neu in Version 0.3.3

- **Warteschlange statt Versatz:** Der feste 1-Sekunden-Versatz aus 0.3.2 löste nur die ursprüngliche Kollision (Zonen/Fehler, gleiches Intervall), erzeugte aber durch unterschiedliche Intervalle (Alarmzustand = 5s) neue periodische Kollisionen (alle 15s). Jetzt wird eine Abfrage, die auf das gemeinsame Lock trifft, nicht mehr übersprungen, sondern wartet, bis die andere Ebene fertig ist — eine Aktualisierung kann sich dadurch kurz verzögern, geht aber nie mehr verloren. Der Versatz entfällt, da er dafür nicht mehr nötig ist.
- Die Diagnose-Sensoren *Übersprungen* je Ebene sind damit hinfällig und wurden durch **Wartezeit (Ø)** je Ebene ersetzt (gleitender Durchschnitt der letzten 20 Werte, in ms) — die alten Sensoren bleiben als verwaiste Entities in der HA-Registry zurück und können manuell entfernt werden.
- **Umfangreiche Abfrage ohne Web-Credentials:** Läuft jetzt gar nicht mehr an, wenn kein Web-Benutzer/Passwort (RSSI) in den Optionen gesetzt ist, statt alle paar Minuten ins Leere zu laufen. Die zugehörigen Diagnose-Sensoren zeigen in diesem Fall "nicht verfügbar" statt dauerhaft "Unbekannt".
- Übersetzung korrigiert: Rohwert `acknowledged` heißt auf dem Secvest-Mode-(DE)-Sensor jetzt **"Alarmabbruch"** statt "Unscharf (Alarm nicht quittiert)".

## Neu in Version 0.3.2

- **Scheduling-Fix:** Die vier Abfrage-Ebenen starten jetzt mit 1 Sekunde Versatz zueinander. Bisher liefen z. B. Zonen- und Fehler-Abfrage bei gleichem Intervall exakt synchron und haben sich dauerhaft gegenseitig um das gemeinsame Lock blockiert ("Abfrage ausgesetzt, Gerät beschäftigt" bei jedem zweiten Tick) — das war kein Zeichen echter Überlastung, sondern ein reiner Scheduling-Kollisionsartefakt.
- **Neue Diagnose-Sensoren** (Kategorie "Diagnose" in der Geräte-Ansicht), je Abfrage-Ebene (Zonen, Fehler, Alarmzustand, umfangreiche Abfrage):
  - *Antwortzeit (Ø)* – gleitender Durchschnitt der letzten 20 tatsächlich ausgeführten Abfragen in Millisekunden
  - *Übersprungen* – Anzahl Ticks seit dem letzten Neustart, die ausgesetzt wurden, weil das Gerät durch eine andere Ebene beschäftigt war

## Neu in Version 0.3.1

Die Alarmzentrale bildet jetzt zusätzlich zu Scharf/Teilscharf/Unscharf auch Alarm- und Quittierungszustände auf die Home-Assistant-Standardzustände ab:

| Rohwert (Secvest) | Home Assistant | Bedeutung (DE) |
|---|---|---|
| `unset` | `disarmed` | Unscharf |
| `partset` | `armed_home` | Teilscharf |
| `set` | `armed_away` | Scharf |
| `unset-alarm` | `triggered` | Alarm |
| `set-alarm` | `triggered` | Alarm |
| `partset-alarm` | `triggered` | Alarm (teilscharf) |
| `acknowledged` | `disarmed` | Alarmabbruch |

Zusätzlich am Alarm-Panel verfügbar: `raw_mode` (unveränderter Rohwert) und `alarm_memory` (true, solange ein Alarm ausgelöst und noch nicht zurückgesetzt ist). Unbekannte Rohwerte ergeben weiterhin `unbekannt`/`None` und werden einmalig als Warnung geloggt.

**Hinweis:** Diese Zuordnung wurde anhand eines einzelnen echten Alarms und dem Secvest-Logbuch abgeleitet, nicht aus offizieller Dokumentation. `partset-alarm` wurde dabei nicht beobachtet (nur analog zu `set-alarm` angenommen), und für die Ausgangsverzögerung ist bisher kein eigener Rohwert bekannt. Rückmeldungen zu abweichenden Werten sind willkommen.

## Neu in Version 0.3.0

- Verbindung bleibt dauerhaft offen (Keep-Alive) statt bei jeder Abfrage neu aufzubauen; TLS-Handshake der Secvest-API entfällt dadurch bei den meisten Abfragen (~6-7s → <100ms)
- Zonen, Fehler, Alarmzustand und die umfangreiche Wireless-/RSSI-Abfrage laufen jetzt unabhängig voneinander mit je eigenem, einstellbarem Intervall
- Ausgänge werden optional (Ein/Aus-Schalter) im Zonen-Takt mitgefragt
- Nur eine Abfrage gleichzeitig: läuft bereits eine, wird der nächste Tick eines anderen Intervalls übersprungen statt eingereiht (keine Überlastung)
- Automatischer Reconnect nach 10 Sekunden (einstellbar) bei Verbindungsabbruch, bestehender Schutzpause-Mechanismus bleibt als Eskalationsstufe bei wiederholten Fehlern erhalten
- Host, Benutzername, Passwort und Benutzer-Code lassen sich jetzt nachträglich über den Home-Assistant-Reconfigure-Dialog ändern (bisher nur bei der Ersteinrichtung möglich)

## Neu in Version 0.2.22

- RSSI-/Funksignalwerte für Secvest-Funkmelder hinzugefügt
- Optionalen separaten Web-Benutzer für RSSI-Abfragen ergänzt
- Anmeldung an der Secvest-Weboberfläche einschließlich Session und CSRF unterstützt
- Web-Session wird wiederverwendet, um wiederholte Anmeldungen zu vermeiden
- Letzte gültige RSSI-Werte bleiben bei kurzzeitigen Verbindungsfehlern erhalten
- Parallele Web-Anmeldungen werden verhindert
- Leere und nicht belegte Funkzonen werden ausgefiltert
- Diagnoseinformationen für Web-Session und RSSI erweitert
- Stabilität der normalen REST- und Zonenabfragen verbessert
- Deutsche und englische Konfigurationstexte überarbeitet

## Installation über HACS

1. HACS in Home Assistant öffnen.
2. Zu **Integrationen** wechseln.
3. Das Drei-Punkte-Menü öffnen und **Benutzerdefinierte Repositories** wählen.
4. Diese Repository-URL eintragen:

   ```text
   https://github.com/claro24/secvest-ha
   ```

5. Als Kategorie **Integration** auswählen.
6. **ABUS Secvest** installieren.
7. Home Assistant neu starten.
8. Die Integration über **Einstellungen > Geräte & Dienste > Integration hinzufügen > ABUS Secvest** hinzufügen.

## Manuelle Installation

Den Ordner der Integration nach Home Assistant kopieren:

```text
custom_components/secvest/
```

Danach Home Assistant neu starten.

## Grundkonfiguration

Beispiel für den Host:

```text
https://192.168.2.22:4433
```

Bei neueren Secvest-Firmware-Versionen muss im Feld **Benutzername / Code** gegebenenfalls der Benutzer-Code eingetragen werden.

Die vier Abfrage-Intervalle werden über die Integrations-Optionen eingestellt (Standardwerte):

- Zonen-Intervall: `3` Sekunden
- Fehler-Intervall: `3` Sekunden
- Alarmzustand-Intervall: `5` Sekunden
- Umfangreiche Abfrage / RSSI: `5` Minuten

Dank dauerhaft offener Verbindung sind auch kurze Intervalle im Sekundenbereich unproblematisch (siehe "Neu in Version 0.3.0"). Reagiert die Secvest dennoch instabil oder träge, die Werte erhöhen. Host, Benutzername, Passwort und Benutzer-Code können nachträglich über das Drei-Punkte-Menü der Integration → **Neu konfigurieren** geändert werden.

## RSSI und Funkstatus

Für das Auslesen der RSSI-Werte verwendet die Integration zusätzlich die lokale Secvest-Weboberfläche.

Unter den Optionen der Integration können dafür separate Zugangsdaten eingetragen werden:

- **Web-Benutzer / Code (nur RSSI)**
- **Web-Passwort (nur RSSI)**

Es wird empfohlen, dafür einen separaten, eingeschränkten Secvest-Benutzer zu verwenden. Ohne diese Web-Zugangsdaten bleibt die RSSI-Abfrage deaktiviert. Die normalen Alarm-, Zonen- und Fault-Funktionen arbeiten weiterhin über die REST-Schnittstelle.

Die Integration stellt unter anderem folgende Attribute pro Melder bereit:

- `rssi`
- `rssi_current`
- `rssi_previous`
- `rssi_bargraph`
- `battery_ok`
- `rf_ok`
- `supervision_ok`
- `sabotage_ok`
- `inner`
- `omittable`
- `omitted`
- `fault_labels`

## Dashboard-Beispiel

![Secvest Zonenübersicht](images/dashboard_0.2.22.png)

Die Beispielkarte zeigt:

- offene und geschlossene Melder
- Zonenname und Zonennummer
- Meldertyp
- RSSI-Wert
- Batterie- und Funkstatus
- interne Überwachung
- Sabotagestatus
- aktuelle Fault-Meldungen

Die vollständige YAML-Konfiguration findest du hier:

[Zonenübersicht herunterladen](dashboards/zonenuebersicht.yaml)

<details>
<summary>Hinweise zur Dashboard-Karte</summary>

Die Karte wird als manuelle Markdown-Karte angelegt:

1. Dashboard bearbeiten.
2. **Karte hinzufügen** wählen.
3. **Manuell** auswählen.
4. Den Inhalt aus `dashboards/zonenuebersicht.yaml` einfügen.

Die Darstellung verwendet HTML innerhalb einer Home-Assistant-Markdown-Karte.

</details>

## Diagnose

Für die Fehlersuche steht der Dienst `secvest.dump_diagnostics` zur Verfügung.

Die erzeugte Datei enthält unter anderem:

- aktuellen Modus
- Zonen und Melder
- Faults
- Ausgänge
- RSSI-Werte
- Informationen zur Web-Session
- Fehler der einzelnen Abfragen

Diagnosedateien können sensible Informationen über die Alarmanlage enthalten und sollten nicht öffentlich veröffentlicht werden.

## Brand- und Logo-Dateien

Für aktuelle Home-Assistant-Versionen können lokale Brand-Dateien direkt mitgeliefert werden:

```text
custom_components/secvest/brand/
```

Unterstützte Dateien:

- `icon.png`
- `icon@2x.png`
- `dark_icon.png`
- `dark_icon@2x.png`
- `logo.png`
- `logo@2x.png`
- `dark_logo.png`
- `dark_logo@2x.png`

## Hinweise

Alarmanlagen sind sicherheitsrelevante Systeme. Bitte alle Steuerbefehle, Automationen und Dashboards gründlich testen.

Die Nutzung erfolgt auf eigene Verantwortung.

---

# ABUS Secvest for Home Assistant

Custom integration for connecting an ABUS Secvest alarm system to Home Assistant.

This integration is unofficial and is not affiliated with ABUS.

This is a fork of [dkmouk/secvest-ha](https://github.com/dkmouk/secvest-ha). Many thanks to dkmouk for the foundation of this integration, and to Jochen, aka Birdy, from alarmforum.de, who shared his files with dkmouk and whose ideas were incorporated into it.

## Features

- Alarm control panel for arm, part-arm and disarm
- Detectors and zones as binary sensors
- Open-zone overview
- RSSI/radio signal values for wireless detectors
- Battery, RF, tamper and supervision status
- Internal monitoring and detector omission information
- Fault polling and assignment of messages to individual zones
- Secvest outputs as diagnostic information
- Buttons for refresh and fault acknowledgement
- Setup and options through the Home Assistant interface
- Configurable polling intervals
- Local polling without a cloud service
- German and English interface
- Diagnostic export for troubleshooting

## New in Version 0.3.12

The log read position (last processed timestamp, plus `log_history` and `mode_change_history`) is now persisted via HA's storage mechanism and restored on integration startup. This means the event log is always read on without a gap from the last entry saved in HA after a restart or reload, instead of re-fetching the last up to 600 entries without a filter every time (slower) or missing entries in between if more than 600 accumulated in the meantime.

## New in Version 0.3.11

User attribution via the event log now works retroactively and for every detected armed/disarmed transition, not just ones triggered through HA: every transition (whether triggered via HA or at the keypad) gets registered the moment it's detected, in a new `mode_change_history` record, and as soon as a matching log entry with a user shows up - even later, on a subsequent scheduled poll - it gets attached after the fact (nearest not-yet-used entry, within a 2-minute window). The new `mode_change_history` attribute on the alarm panel shows this history (timestamp, from/to raw state, and the user once known) right on the entity that represents the state.

Also fixed: arming via the HA alarm panel (not the service) accidentally built a brand-new `SecvestData` object during the live zone check without the log/user fields, silently wiping `log_history`, `mode_change_history` and the last known user on every arm attempt. Now only what actually changed gets updated, everything else is preserved.

## New in Version 0.3.10

Switching armed/disarmed via Home Assistant (the `secvest_set_mode` service, or the alarm panel) now also queries the event log right afterward, instead of waiting up to 5 minutes for the next scheduled `/logs/` poll (runs in the background, doesn't delay the switch itself). With a few short retries (up to 4x, 1.5s apart), since the firmware doesn't always make the log entry available immediately.

To avoid misattributing an older, unrelated entry when two actions happen close together (e.g. an HA switch and someone at the keypad almost simultaneously), only a log entry with a timestamp at or after the moment of the switch is accepted. The "Secvest Letzter Benutzer" sensor also now keeps the last up to 20 user-tagged log entries as a `history` attribute, instead of overwriting just the latest one - so no attribution gets lost when actions happen in quick succession.

A genuinely simultaneous second action (e.g. at the exact same moment at the keypad) can't be told apart with full certainty without the Secvest firmware providing its own transaction ID - but both entries still end up fully in the history regardless.

## New in Version 0.3.9

The extensive wireless poll (RSSI, battery, sabotage, supervision per wireless sensor) no longer runs on a fixed-minute interval - it now runs **once a day at a freely configurable time** (option "Extensive poll / RSSI, time of day", default 03:00), plus on demand at any time via the new "Secvest RSSI/Wireless jetzt abfragen" button. Reason: unlike zones/faults/alarm state, this isn't a lightweight REST call but a full web-UI login scrape (CSRF token, session, form parsing) - and RSSI/battery change too slowly to justify running it more often than daily anyway. This goes much easier on the panel without having to wait until the next day when you actually need fresh data (e.g. right after installing a new wireless sensor).

One known caveat: whether a sabotage/tamper event is also reported via the fast faults poll (`/faults/`, 3 sec.) is not verified - if not, tampering between the daily polls could go unnoticed for up to 24h unless manually refreshed via the button.

## New in Version 0.3.8

New diagnostic sensor "Secvest Letzter Benutzer" (Last User): additionally polls the `/logs/` endpoint (own interval via the new "Event log / user attribution" option, default 5 minutes; fetched incrementally via `$filter=timestamp ge <n>` so the full log, up to 600 entries, isn't reloaded every time) and shows the user slot or username of the last logged action that carried a user identity. Confirmed with real log examples for arming/disarming (e.g. `"Ben 001 TB 1 deaktiv"`). Whether acknowledging a fault or an alarm also writes a user to the log is **not yet** confirmed with real data - this corrects the 0.3.7 statement that user identification wasn't possible in general; that was only true for the endpoints polled at the time (`state`/`zones/`/`faults/`/`output/`).

Also found and fixed while cross-checking against the external docs ([thomasdanz/secvest-api](https://github.com/thomasdanz/secvest-api)): `set_mode()` never verified whether the Secvest actually applied the requested state. For a transition the firmware silently ignores (e.g. going straight from armed-home to armed-away without disarming first), HA would have falsely reported success; it now compares requested vs. actual state and raises an error on mismatch.

The official Secvest app sends `acknowledged` via PUT to acknowledge an alarm, per the same docs - technically possible, but deliberately not (yet) exposed as a sendable command here, since that would be a new, security-relevant capability.

## New in Version 0.3.7

New `triggered_zone_names` attribute on the alarm panel: while `alarm_memory` is active, lists the names of zones that currently have a fault of type `5000` with `affects-zone` set (e.g. `["Wohnen-BM"]` or `["Bad-Fenster"]`) - verified against real log data from two test alarms (motion detector and window contact).

Fault type `5000` generally means "zone open, blocks arming" according to external reverse-engineering docs ([thomasdanz/secvest-api](https://github.com/thomasdanz/secvest-api), [ha-secvest issue #95](https://github.com/thomasdanz/ha-secvest/issues/95)) - not alarm-specific, but stable enough to reliably exclude unrelated fault types (e.g. `1170` = repeater battery low). Entry/exit zones deliberately never produce this fault according to the same docs, even if briefly open (also confirmed in our own test: the entry door itself never produced one).

Identifying which user acknowledged an alarm was not possible with the endpoints polled at the time - see version 0.3.8 for the `/logs/`-based extension.

## New in Version 0.3.5

German label adjustments on the "Secvest Mode (DE)" sensor, based on two real test alarms:

- `unset-alarm`: "Alarm" → **"Eingangsweg verlassen"** (left the entry path) — occurs when you leave the designated entry route during the entry delay (e.g. don't head straight to the keypad), rather than only once the delay actually expires
- `partset`: "Teilscharf" → **"Scharf (intern)"**
- `partset-alarm`: "Alarm (teilscharf)" → **"Alarm (intern)"**

The mapping to Home Assistant's standard states (`armed_home`/`triggered`/etc.) is unaffected - only the German display label changed.

## New in Version 0.3.4

- **Raw response debug logging:** every Secvest response (zones, faults, alarm state, outputs, and every step of the extensive web poll) is now logged as full raw text when debug logging is enabled for `custom_components.secvest.api`. Enable it in `configuration.yaml`:
  ```yaml
  logger:
    logs:
      custom_components.secvest.api: debug
  ```
  No new integration option needed - controlled entirely through Home Assistant's standard logger config (including its own rotation/retention).
- The `unset-alarm` raw value is now also treated as `triggered`/"Alarm" (previously `pending`/"Entry delay") - matches the observation that this value already occurs during an active alarm.

## New in Version 0.3.3

- **Queue instead of stagger:** the fixed 1-second stagger from 0.3.2 only fixed the original collision (zones/faults, same interval), but created new periodic collisions (every 15s) because of the differing alarm-state interval (5s). A poll that now hits the shared lock no longer gets skipped - it waits until the other tier is done, so an update may be briefly delayed but is never lost anymore. The stagger is gone since it's no longer needed for that.
- The per-tier *Skipped* diagnostic sensors are therefore obsolete and have been replaced with **Wait time (avg)** per tier (rolling average of the last 20 values, in ms) - the old sensors remain as orphaned entities in the HA registry and can be removed manually.
- **Extensive poll without web credentials:** no longer runs at all if no web user/password (RSSI) is set in the options, instead of polling into the void every few minutes. The related diagnostic sensors now show "unavailable" instead of a permanent "unknown".
- Fixed translation: the `acknowledged` raw value now shows as **"Alarmabbruch"** (alarm aborted) on the Secvest Mode (DE) sensor instead of "Disarmed, alarm not yet acknowledged".

## New in Version 0.3.2

- **Scheduling fix:** the four polling tiers now start with a 1-second stagger between them. Previously, e.g. the zones and faults polls (same default interval) ran perfectly in sync and permanently contended for the shared lock ("poll skipped, device busy" on every other tick) — not a sign of real overload, just a scheduling collision artifact.
- **New diagnostic sensors** (under the "Diagnostic" section of the device view), per polling tier (zones, faults, alarm state, extensive poll):
  - *Response time (avg)* – rolling average of the last 20 actually executed requests, in milliseconds
  - *Skipped* – number of ticks since the last restart that were skipped because the device was busy with another tier

## New in Version 0.3.1

The alarm panel now also maps alarm and acknowledgement states, not just armed/disarmed, onto Home Assistant's standard states:

| Raw value (Secvest) | Home Assistant | Meaning |
|---|---|---|
| `unset` | `disarmed` | Disarmed |
| `partset` | `armed_home` | Armed home |
| `set` | `armed_away` | Armed away |
| `unset-alarm` | `triggered` | Triggered |
| `set-alarm` | `triggered` | Triggered |
| `partset-alarm` | `triggered` | Triggered (armed home) |
| `acknowledged` | `disarmed` | Alarm aborted |

Also newly available on the alarm panel: `raw_mode` (the unmodified raw value) and `alarm_memory` (true while an alarm has been triggered and not yet cleared). Unknown raw values still show as `unknown`/`None` and are logged once as a warning.

**Note:** This mapping was derived from a single real alarm event and the Secvest's own log, not from official documentation. `partset-alarm` was not observed (only assumed analogous to `set-alarm`), and no dedicated raw value for the exit delay is known yet. Feedback on deviating values is welcome.

## New in Version 0.3.0

- Connection now stays open (keep-alive) instead of being rebuilt on every request; the Secvest API's TLS handshake is skipped for most polls (~6-7s → <100ms)
- Zones, faults, alarm state and the extensive wireless/RSSI poll now run independently, each with its own configurable interval
- Outputs can optionally (toggle) be fetched alongside zones
- Only one request in flight at a time: if one is already running, the next tick of another interval is skipped rather than queued (avoids overload)
- Automatic reconnect after 10 seconds (configurable) on connection loss; the existing cooldown mechanism remains as an escalation step for repeated failures
- Host, username, password and user code can now be changed afterwards via the Home Assistant reconfigure dialog (previously only possible during initial setup)

## New in Version 0.2.22

- Added RSSI/radio signal values for Secvest wireless detectors
- Added optional separate web credentials for RSSI polling
- Added support for Secvest web authentication, sessions and CSRF
- Web sessions are reused to avoid repeated logins
- Last valid RSSI values are retained during temporary connection errors
- Parallel web logins are prevented
- Empty and unused wireless zone entries are filtered out
- Extended diagnostic information for web sessions and RSSI
- Improved stability of regular REST and zone polling
- Updated German and English configuration texts

## Installation with HACS

1. Open HACS in Home Assistant.
2. Go to **Integrations**.
3. Open the three-dot menu and choose **Custom repositories**.
4. Add this repository URL:

   ```text
   https://github.com/claro24/secvest-ha
   ```

5. Select **Integration** as the category.
6. Install **ABUS Secvest**.
7. Restart Home Assistant.
8. Add the integration via **Settings > Devices & services > Add integration > ABUS Secvest**.

## Manual Installation

Copy the integration folder to:

```text
custom_components/secvest/
```

Then restart Home Assistant.

## Basic Configuration

Example host:

```text
https://192.168.2.22:4433
```

With newer Secvest firmware versions, the **Username / Code** field may need to contain the user code.

The four polling intervals are configured in the integration options (defaults):

- Zones interval: `3` seconds
- Faults interval: `3` seconds
- Alarm state interval: `5` seconds
- Extensive poll / RSSI: `5` minutes

Thanks to the persistent connection, short intervals in the single-digit-second range are not a problem (see "New in Version 0.3.0"). If the Secvest still reacts unstably or slowly, increase the values. Host, username, password and user code can be changed afterwards via the integration's three-dot menu → **Reconfigure**.

## RSSI and RF Status

The integration additionally uses the local Secvest web interface to read RSSI values.

Separate credentials can be entered in the integration options:

- **Web user / code (RSSI only)**
- **Web password (RSSI only)**

Using a separate restricted Secvest user is recommended. RSSI polling remains disabled without these web credentials. The regular alarm, zone and fault functions continue to use the REST interface.

The integration provides attributes including:

- `rssi`
- `rssi_current`
- `rssi_previous`
- `rssi_bargraph`
- `battery_ok`
- `rf_ok`
- `supervision_ok`
- `sabotage_ok`
- `inner`
- `omittable`
- `omitted`
- `fault_labels`

## Dashboard Example

![Secvest zone overview](images/dashboard_0.2.22.png)

The example card displays:

- open and closed detectors
- zone name and number
- detector type
- RSSI value
- battery and RF status
- internal monitoring
- tamper status
- current fault messages

The complete YAML configuration is available here:

[Download zone overview YAML](dashboards/zonenuebersicht.yaml)

<details>
<summary>Dashboard card instructions</summary>

Add the card as a manual Markdown card:

1. Edit the dashboard.
2. Select **Add card**.
3. Select **Manual**.
4. Paste the contents of `dashboards/zonenuebersicht.yaml`.

The layout uses HTML inside a Home Assistant Markdown card.

</details>

## Diagnostics

The `secvest.dump_diagnostics` service is available for troubleshooting.

The generated file includes:

- current mode
- zones and detectors
- faults
- outputs
- RSSI values
- web session information
- errors from individual requests

Diagnostic files may contain sensitive information about the alarm system and should not be published publicly.

## Brand Images

Local brand images can be shipped with current Home Assistant versions in:

```text
custom_components/secvest/brand/
```

Supported files:

- `icon.png`
- `icon@2x.png`
- `dark_icon.png`
- `dark_icon@2x.png`
- `logo.png`
- `logo@2x.png`
- `dark_logo.png`
- `dark_logo@2x.png`

## Notes

Alarm systems are safety-relevant devices. Please test all control actions, automations and dashboards carefully.

Use at your own risk.
