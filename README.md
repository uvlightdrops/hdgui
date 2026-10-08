# HDGUI

Lokale Python-WebUI mit aktuellem Transit-Tierkreis, persistenten Geburtsprofilen und gespeicherten Human-Design-BodyGraphs. Die kontinuierliche Transit-Animation bleibt das nächste Ziel.

Die Homepage `/` zeigt einen schematischen BodyGraph ausschließlich aus dem aktuellen Transit: aktive Gates und ihre Kanalhälften, vollständige Kanäle nur bei zwei aktiven Endpunkten. Zentren werden nur durch vollständige Transit-Kanäle definiert. Inaktive Gates und Kanalverbindungen werden nicht gezeichnet. Die Darstellung verwendet dieselben gecachten Positionen und Autoreload-Einstellungen wie das Tierkreisrad, ohne zusätzliche API-Berechnung. Geburtsprofile liegen separat unter `/profiles` und sind über das linke Menü erreichbar.

Unter `/transit` zeigt die Anwendung die aktuellen geozentrischen Planetenpositionen, den Tierkreis und alle 64 Gates. Das Rad wird mit bis zu 850 Pixeln Breite dargestellt. Zeitpunkt und kompakte Tabelle stehen bei ausreichend Platz daneben, andernfalls darunter, damit zusätzliche Spalten das Rad nicht verkleinern. Links ist auf allen Seiten ein senkrechtes Menü für Home, Transit, Profil anlegen und Settings verfügbar. Ein Klick auf den Zeitpunkt lädt die Seite erneut. Unter `/settings` lässt sich die Cache-Dauer auf 1m, 5m, 10m, 30m oder 1h setzen und Autoreload ein-/ausschalten. Diese Einstellungen werden in der Datenbank gespeichert und gelten für alle Betrachter. Autoreload verwendet das Cache-Intervall; offene Seiten übernehmen Änderungen erst beim nächsten Laden. Standard ist 1m mit Autoreload. Eine beschleunigte oder kontinuierliche Animation ist noch nicht enthalten. Dafür muss der lokale Berechnungsdienst laufen.

Transitdaten und das gerenderte Rad werden serverseitig für die konfigurierte Cache-Dauer ab Beginn der Berechnung im Arbeitsspeicher gecacht. Alle Browser desselben Anwendungsprozesses verwenden die gleiche Momentaufnahme; auch gleichzeitige Anfragen lösen nur einen erfolgreichen Refresh aus. Erst der nächste Seitenaufruf nach Ablauf berechnet neue Daten. Der angezeigte Zeitpunkt bleibt der tatsächliche Berechnungszeitpunkt. Bei einem fehlgeschlagenen Refresh wird ein Fehler angezeigt, nicht stillschweigend ein veralteter Transit. Neustarts leeren den Cache. Bei mehreren Uvicorn-Workern hat jeder Prozess einen eigenen Cache; für mehrere Prozesse oder Server wäre ein zentraler Cache erforderlich.

Der Adapter verwendet `/calculate` für den aktuellen UTC-Zeitpunkt und übernimmt ausschließlich `gates.prs.Planets`: Diese Positionen entsprechen dem aktuellen Transit, während Design-Positionen für diese Ansicht verworfen werden. So ist kein gespeichertes Geburtsprofil erforderlich. Der API-Endpunkt rundet ekliptikale Längen auf drei Dezimalstellen.

Die Tabelle enthält außerdem **Next Gate** und **ETA (UTC)**. Der nächste Gate-Wechsel wird aus zukünftigen API-Positionen gesucht, einschließlich Rückläufigkeit und Richtungswechseln, nicht linear extrapoliert. Die Suche verwendet adaptive Schritte zu beiden Gate-Grenzen und grenzt Wechsel auf ein Zeitintervall von höchstens 30 Sekunden ein. Sehr kurze Grenzübertritte unterhalb dieser zeitlichen Abtastauflösung können nicht aufgelöst werden. Der Suchhorizont ist 10 Jahre mit maximal 10.000 API-Anfragen pro Suchlauf; nicht gefundene Wechsel oder API-Fehler werden ausdrücklich angezeigt.

Die erste Suche kann viele API-Anfragen benötigen und läuft deshalb im Hintergrund. Währenddessen stehen Auslassungspunkte in noch nicht berechneten Zellen. Die Ergebnisse werden fortlaufend nachgeladen, auch ohne Transit-Autoreload. Alle Betrachter desselben Prozesses teilen sich die Suche und die berechneten zukünftigen Wechsel; diese bleiben über Transit-Cache-Refreshes hinweg gültig, bis der Wechsel erreicht ist. Nach Fehlern kann ein erneuter Aufruf nach 60 Sekunden die Suche wieder starten. Ergebnisse liegen zunächst nur im Arbeitsspeicher.

## Start

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/alembic upgrade head
export HDGUI_API_URL=http://127.0.0.1:9021
export HD_API_TOKEN='DEIN_LOKALER_API_TOKEN'
export HDGUI_ENGINE_VERSION=4.0.2
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Unter <http://127.0.0.1:8000> Profile anlegen, bearbeiten, löschen und Charts berechnen. Geburtsdaten lassen sich auch ohne laufenden Berechnungsdienst speichern. Der Dienst `dturkuler/humandesign_api` muss separat installiert und gestartet werden; dessen Lizenz und Einsatzbedingungen sind vor der Nutzung zu klären. Es werden keine Geburtsdaten an einen externen Dienst gesendet, sofern die konfigurierte API lokal läuft.

## Speicherung und Konfiguration

SQLAlchemy speichert Profile und SVGs standardmäßig in `data/hdgui.db`. Für Sicherungen die Anwendung stoppen und diese Datei kopieren; bei Wiederherstellung enthält sie auch die zwischengespeicherten Charts. Alembic verwaltet das Schema. Nach Updates Migrationen mit `alembic upgrade head` ausführen.

| Variable | Bedeutung |
|---|---|
| `HDGUI_DATABASE_URL` | SQLAlchemy-URL; Standard `sqlite:///data/hdgui.db` |
| `HDGUI_API_URL` | Lokaler Berechnungsdienst; Standard `http://127.0.0.1:9021` |
| `HD_API_TOKEN` | Serverseitiger Bearer-Token des Berechnungsdienstes |
| `HDGUI_ENGINE_VERSION` | Eingesetzte Engine-Version; bei Updates ändern, um alte Charts zu invalidieren |
| `HDGUI_SESSION_SECRET` | Optionaler geheimer Sitzungsschlüssel; ohne Konfiguration verfallen Formularsitzungen beim Neustart |

SVGs werden nach erfolgreicher Berechnung dauerhaft gespeichert. Wiederholtes Öffnen benötigt keine neue Berechnung. Änderungen am Geburtszeitpunkt invalidieren den Cache; Änderungen am Namen oder Ort nicht. Neue Berechnungen ersetzen das alte SVG erst nach erfolgreicher Antwort. SVGs werden als Bild beziehungsweise als Dokument mit restriktiver Content Security Policy ausgeliefert, nicht als ungeprüftes HTML eingebettet.

Die Geburtszeit wird mit der angegebenen IANA-Zeitzone nach UTC umgerechnet. Ungültige oder mehrdeutige Zeiten während einer Zeitumstellung werden ausdrücklich abgelehnt. Für mehrdeutige Zeiten kann der bekannte UTC-Zeitpunkt mit `Etc/UTC` eingegeben werden. An `/bodygraph` wird UTC mit einer Referenzposition am Nullmeridian übertragen: Der untersuchte Upstream-Endpunkt schneidet nicht-ganzzahlige Zeitzonenoffsets ab. Geozentrische HD-Charts hängen vom Zeitpunkt ab, nicht vom Geburtsort. Dieser bleibt als Profilinformation erhalten.

**Nur für lokale Einzelbenutzernutzung:** Keine Benutzeranmeldung oder Eigentümertrennung implementiert. Nicht öffentlich oder in einem gemeinsam genutzten Netz bereitstellen. Für Mehrbenutzerbetrieb sind Authentifizierung, Profil-Eigentümerschaft, Zugriffsschutz und HTTPS nachzurüsten. SQLAlchemy erlaubt PostgreSQL mit passendem Treiber; Alembic-Migrationen und getrennte Sessions bereiten diesen Ausbau vor.

## Experiment-Seiten

`/experiments/gate-walk` zeigt eine rein geometrische Gate-Wanderung in Tierkreis-Reihenfolge. Ein Punkt bewegt sich über alle 64 Gate-Positionen und zurück zum Anfang; die Spur bleibt stehen. Neutrale Zentren und schwache Gate-Markierungen bilden den Hintergrund, keine Kanäle oder Transit-Aktivierungen. Abspielen/Pause, Geschwindigkeit und Zurücksetzen sind verfügbar. Die Animation beginnt erst nach Abspielen, endet nach einer Runde und benötigt keine API-Anfragen.

Die Ideen aus `hd-tests/` sind als separate Seiten umgesetzt, ohne die alten PyEphem-/Flatlib-Berechnungen oder deren fehlende CSV-Datei zu übernehmen:

| Seite | Funktion |
|---|---|
| `/experiments/ephemeris` | Tierkreispositionen in Grad/Minuten und tägliche Ephemeriden für 1, 7 oder 30 Tage |
| `/experiments/series` | Tabelle der Transitpositionen, Gates und Linien; stündlich für 1 Tag, alle 6 Stunden für 7 Tage, täglich für 30 Tage |
| `/experiments/gate-plot` | Integrierter **Gate-Verlauf** (direkter Hauptmenü-Link): alle API-Himmelskörper, unabhängige Mehrfachauswahl, stufenloser Zoom und freie Zeitnavigation |

Die Y-Achse des Gate-Verlaufs verwendet die Tierkreis-Reihenfolge aus `app.transit.GATES` (Gate 41 bei 302° als Beginn), nicht die numerische Reihenfolge 1–64. Alle 64 Gate-Nummern sind beschriftet; beim Umlauf von Gate 60 zu Gate 41 springt die Darstellung vom oberen zum unteren Achsenende.

Ephemeriden und Tabellen-Zeitreihe bleiben unverändert: Startzeiten werden explizit in UTC eingegeben, auf volle Stunden normalisiert und erst auf Knopfdruck geladen (höchstens 31 Zeitpunkte).

### Interaktiver Gate-Verlauf

Der Gate-Verlauf lädt beim Öffnen einen Tag um den aktuellen UTC-Zeitpunkt. Sonne, Erde, Mond, Mondknoten und sämtliche Planeten können unabhängig aktiviert werden; weitere vom Dienst gelieferte Körper erscheinen automatisch. Farben/Legende und „Alle aktivieren/deaktivieren“ erleichtern Vergleiche. Die Auswahl rendert vorhandene Daten neu, ohne API-Berechnung.

Mausrad, Plus/Minus und ein logarithmischer stufenloser Zoom-Regler ändern den sichtbaren Zeitraum. Ziehen verschiebt ihn; Touch unterstützt Ziehen und Zwei-Finger-Zoom. Vor/Zurück, Jetzt und Tages-/Wochen-/Monats-/Jahresvorgaben ergänzen die Navigation. Im fokussierten Diagramm funktionieren Pfeile links/rechts, +/− und Pos1; Pfeile oben/unten lesen einzelne berechnete Zeitpunkte. Zeigen oder Tippen zeigt die nächstgelegene tatsächliche Stichprobe. Eine aufklappbare Tabelle stellt alle Einzelwerte der aktiven Körper zugänglich bereit. UTC-Start und -Ende können frei eingegeben werden (1900–2099, sichtbarer Zeitraum mindestens eine Stunde).

Zoom, Navigation, Datums- und Detailänderungen planen nach 250 ms Ruhe automatisch neue Daten. `/experiments/timeline` validiert den vollständigen Bereich und liefert sofort ein adaptives, UTC-rasterbasiertes Abtastschema, ohne Berechnungen auszulösen. Der Detailgrad begrenzt es auf 32, 64 (Standard), 128 oder maximal 160 Punkte. `/experiments/frame` berechnet jeweils genau einen Zeitpunkt und liefert immer alle Körper, unabhängig von der Anzeigeauswahl. Punkte erscheinen fortlaufend, zuerst an den Rändern und in der Mitte, danach in zunehmend feineren Zwischenräumen. Pro Browser läuft höchstens eine Punktabfrage; nach einem Ausschnittwechsel wird eine bereits laufende Abfrage abgewartet, ihr veraltetes Ergebnis aber verworfen. Nicht gestartete alte Abfragen entfallen. Fehler stoppen weitere Punktabfragen und lassen sich ausdrücklich erneut versuchen.

Alle drei Seiten teilen einen begrenzten Cache (512 Zeitpunkte, je eine Stunde Gültigkeit pro Anwendungsprozess). Gleichzeitige Betrachter teilen berechnete Punkte. Es läuft maximal eine neue Ephemeriden-Berechnung zugleich; belegte Punktabfragen liefern 503 mit `Retry-After`, statt Threads hinter langsamen Berechnungen zu blockieren. Bereits gecachte Punkte und die Seiten selbst bleiben verfügbar. Der Browser puffert ebenfalls maximal 512 Punkte für bis zu eine Stunde. Mehrere Prozesse haben separate Caches; Neustarts leeren sie.

Fortschritt, Fehler, tatsächliche Datenzeitpunkte und Rasterauflösung werden angezeigt. Linien verbinden ausschließlich benachbarte, erfolgreich geladene Abtastpunkte; der zyklische Achsensprung und ungeladene Lücken bleiben unverbunden. Es werden keine synthetischen Positionen oder exakten Gate-Wechsel behauptet. Große Zeiträume und grobe Raster können schnelle Bewegungen und kurze Wechsel übersehen; für feinere Untersuchungen hineinzoomen oder den Detailgrad erhöhen. Gate-Nummern sind Kategorien, keine physikalische Größenachse. Ohne laufenden Berechnungsdienst werden Fehler angezeigt. Neue Diagrammbibliotheken oder externe Browser-Ressourcen sind nicht erforderlich.

## Entwicklung

Der Transit-BodyGraph verwendet dieselben Layout-Geometrien wie der Profil-Renderer von `humandesign_api` (Körperumriss, Zentren, Kanalhälften und Gate-Positionen). Nur Transit-Aktivierungen werden eingezeichnet. Das übernommene Layout steht unter AGPL-3.0-or-later; Herkunft und Lizenz liegen in `app/data/NOTICE` und `app/data/LICENSE-AGPL`. Die Lizenzpflichten sind bei Bereitstellung und Weitergabe zu beachten.

```bash
.venv/bin/python -m unittest discover -s tests -v
node --check app/static/gate-timeline.js
node tests/test_gate_timeline.js
```
