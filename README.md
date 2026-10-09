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
# Nur für eine neue/leere Datenbank; ersetzt Katalognamen und -texte:
.venv/bin/python -m app.catalog_fixture
export HDGUI_API_URL=http://127.0.0.1:9021
export HD_API_TOKEN='DEIN_LOKALER_API_TOKEN'
export HDGUI_ENGINE_VERSION=4.0.2
.venv/bin/python -m app
```

Unter <http://127.0.0.1:8001> Profile anlegen, bearbeiten, löschen und Charts berechnen. Geburtsdaten lassen sich auch ohne laufenden Berechnungsdienst speichern. Der Dienst `dturkuler/humandesign_api` wird bei Bedarf automatisch als Docker-Container gestartet (siehe unten); dessen Lizenz und Einsatzbedingungen sind vor der Nutzung zu klären. Es werden keine Geburtsdaten an einen externen Dienst gesendet, sofern die konfigurierte API lokal läuft.

Alternativ startet `./start.sh` die WebUI aus der lokalen `.venv` auf `127.0.0.1:8001`, auch beim Aufruf aus einem anderen Verzeichnis. Vor dem Start prüft es, ob die konfigurierte Adresse bereits belegt ist, und bricht dann mit einer Meldung ab, ohne einen bestehenden Prozess zu beenden. Die Prüfung berücksichtigt auch angehängte `--host`-/`--port`-Optionen; die endgültige Portbindung durch Uvicorn bleibt maßgeblich. Host und Port lassen sich mit `UVICORN_HOST` und `UVICORN_PORT` ändern; zusätzliche Uvicorn-Optionen können angehängt werden, etwa `./start.sh --reload`. Das Script führt keine Migrationen oder Datenimporte aus.

Host und Port sind beim Start über `--host` und `--port` konfigurierbar. Die obige Startanweisung verwendet Port **8001** und bindet ausschließlich an localhost. Alternativ unterstützt Uvicorn `UVICORN_HOST` und `UVICORN_PORT` als Umgebungsvariablen, wenn die entsprechenden Kommandozeilenoptionen weggelassen werden. Explizite Optionen haben Vorrang. Der separate Berechnungsdienst bleibt unter dem mit `HDGUI_API_URL` konfigurierten Port (standardmäßig 9021).

Der Python-Einstieg `.venv/bin/python -m app` übernimmt Startprüfung und Serverstart (Standard `127.0.0.1:8001`). `start.sh` ist lediglich ein kurzer Aufruf dieses Einstiegs. Alle Uvicorn-Optionen bleiben verfügbar, beispielsweise `.venv/bin/python -m app --reload --port 8001`; `UVICORN_HOST` und `UVICORN_PORT` werden ebenfalls berücksichtigt.

### Gemeinsamer Start mit dem Berechnungsdienst

`.venv/bin/python -m app` (und damit `./start.sh`) startet Berechnungsdienst und WebUI gemeinsam. Ablauf:

1. Zuerst wird geprüft, ob die WebUI-Adresse frei ist. Ist sie belegt, wird auch kein Backend gestartet.
2. Antwortet `HDGUI_API_URL` auf `GET /health` mit `status: ok`, wird der laufende Dienst unverändert weiterverwendet.
3. Sonst wird per Docker der Container `hd-api` gestartet (falls gestoppt) oder neu angelegt (falls nicht vorhanden). Das Anlegen entspricht
   `docker run -d --name hd-api -p 127.0.0.1:9021:9021 -e HD_API_TOKEN dturkuler/humandesign_api:latest`
   und setzt ein gesetztes `HD_API_TOKEN` voraus. Der Token wird nur über den Variablennamen aus der Umgebung übergeben, nie als Wert auf der Kommandozeile oder in Ausgaben. Der Host-Port folgt dem Port aus `HDGUI_API_URL`.
4. Anschließend wird bis zu 60 Sekunden (`--api-timeout`, `HDGUI_API_START_TIMEOUT`) auf `/health` gewartet. Schlägt Docker fehl, beendet sich der Container oder läuft die Wartezeit ab, startet die WebUI nicht.

Voraussetzungen: Docker im `PATH` mit Zugriff auf den Docker-Daemon. Automatisch verwaltet werden nur `http://`-URLs auf `127.0.0.1`, `localhost` oder `[::1]` ohne Pfad; andere Adressen werden abgelehnt. Für einen extern betriebenen Dienst oder ohne Docker `--no-api` (bzw. `HDGUI_NO_API=1`) verwenden; dann startet nur die WebUI. Containername und Image lassen sich mit `HDGUI_API_CONTAINER` und `HDGUI_API_IMAGE` ändern.

Der Container läuft nach dem Beenden der WebUI weiter und wird beim nächsten Start wiederverwendet; zum Stoppen `docker stop hd-api`. Ein vorhandener Container behält Token und Portzuordnung aus seiner Erstellung. Nach einer Änderung von `HD_API_TOKEN` oder Port den Container manuell entfernen (`docker rm -f hd-api`), damit er neu angelegt wird. `/health` ist ohne Anmeldung erreichbar; ob der Token passt, zeigt erst die erste Berechnung. Container in anderen Zuständen (z. B. `paused`) werden nicht verändert.

## Speicherung und Konfiguration

SQLAlchemy speichert Profile, SVGs und Geburtsaktivierungen standardmäßig in `data/hdgui.db`. Für Sicherungen die Anwendung stoppen und diese Datei kopieren; bei Wiederherstellung enthält sie auch die zwischengespeicherten Charts. Alembic verwaltet das Schema. Nach Updates Migrationen mit `alembic upgrade head` ausführen.

| Variable | Bedeutung |
|---|---|
| `HDGUI_DATABASE_URL` | SQLAlchemy-URL; Standard `sqlite:///data/hdgui.db` |
| `HDGUI_API_URL` | Lokaler Berechnungsdienst; Standard `http://127.0.0.1:9021` |
| `HD_API_TOKEN` | Serverseitiger Bearer-Token des Berechnungsdienstes |
| `HDGUI_API_CONTAINER` | Name des automatisch verwalteten Docker-Containers; Standard `hd-api` |
| `HDGUI_API_IMAGE` | Image für neu angelegte Container; Standard `dturkuler/humandesign_api:latest` |
| `HDGUI_API_START_TIMEOUT` | Wartezeit in Sekunden auf `/health`; Standard 60 (wie `--api-timeout`) |
| `HDGUI_NO_API` | `1` entspricht `--no-api`: Berechnungsdienst weder prüfen noch starten |
| `HDGUI_ENGINE_VERSION` | Eingesetzte Engine-Version; bei Updates ändern, um alte Charts zu invalidieren |
| `HDGUI_SESSION_SECRET` | Optionaler geheimer Sitzungsschlüssel; ohne Konfiguration verfallen Formularsitzungen beim Neustart |

SVGs werden nach erfolgreicher Berechnung dauerhaft gespeichert. Wiederholtes Öffnen benötigt keine neue Berechnung. Änderungen am Geburtszeitpunkt invalidieren den Cache; Änderungen am Namen oder Ort nicht. Neue Berechnungen ersetzen das alte SVG erst nach erfolgreicher Antwort. SVGs werden als Bild beziehungsweise als Dokument mit restriktiver Content Security Policy ausgeliefert, nicht als ungeprüftes HTML eingebettet.

Die Geburtszeit wird mit der angegebenen IANA-Zeitzone nach UTC umgerechnet. Ungültige oder mehrdeutige Zeiten während einer Zeitumstellung werden ausdrücklich abgelehnt. Für mehrdeutige Zeiten kann der bekannte UTC-Zeitpunkt mit `Etc/UTC` eingegeben werden. An `/bodygraph` wird UTC mit einer Referenzposition am Nullmeridian übertragen: Der untersuchte Upstream-Endpunkt schneidet nicht-ganzzahlige Zeitzonenoffsets ab. Geozentrische HD-Charts hängen vom Zeitpunkt ab, nicht vom Geburtsort. Dieser bleibt als Profilinformation erhalten.

**Nur für lokale Einzelbenutzernutzung:** Keine Benutzeranmeldung oder Eigentümertrennung implementiert. Nicht öffentlich oder in einem gemeinsam genutzten Netz bereitstellen. Für Mehrbenutzerbetrieb sind Authentifizierung, Profil-Eigentümerschaft, Zugriffsschutz und HTTPS nachzurüsten. SQLAlchemy erlaubt PostgreSQL mit passendem Treiber; Alembic-Migrationen und getrennte Sessions bereiten diesen Ausbau vor.

## Kanonische Tore und Kanäle

Migration `0003` legt `gates` (64 Tore) und `channels` (36 eindeutige, ungeordnete Gate-Paare) an. Migration `0004` ergänzt sieben kanonische Schaltkreise und ordnet jedem Kanal genau einen Schaltkreis zu. Neue Datenbanken erhalten neutrale Namen und leere Beschreibungen, ohne eine private Datei zu benötigen. Die Tor- und Kanaltopologie stammt aus einer eingefrorenen Migration, nicht aus der unvollständigen CSV-Kanalspalte. Die Schaltkreiszuordnung ist ebenfalls eingefroren: Integration umfasst die Kanäle 10–20, 10–57, 20–34 und 34–57; Zentrierung umfasst 10–34 und 25–51. `birth_activations.gate` verweist per Fremdschlüssel auf `gates`; Kanäle und definierte Zentren der Profile werden aus diesen Tabellen abgeleitet.

**Katalogtexte importieren (ausdrücklich, optional):** `app/data/catalog-default.yaml` ist der versionierte, mitinstallierte Beispielkatalog. Er enthält eigene knappe Beschreibungen, Namen für alle Tore und Kanäle sowie sieben Schaltkreise mit allen 36 kanonischen Kanälen. Aus dem Projektverzeichnis importiert der Befehl ohne Argumente zuerst `data/fixtures/catalog.yaml`, falls dort ein lokaler privater Katalog liegt; andernfalls verwendet er den mitgelieferten Katalog. Ein explizit angegebener Pfad wird genau so verwendet und fällt bei Fehlern nicht auf einen anderen Katalog zurück:

```bash
.venv/bin/alembic upgrade head
.venv/bin/python -m app.catalog_fixture
# Alternativer YAML-Katalog:
.venv/bin/python -m app.catalog_fixture data/mein-katalog.yaml
```

Migration und Import verwenden dieselbe `HDGUI_DATABASE_URL` (Standard `sqlite:///data/hdgui.db`). Das UTF-8-YAML (optional BOM) enthält `schema_version: 2`, `gates`, `circuits` und `channels`. Jedes Tor hat `number`, `name`, `description`, `center`; jeder Schaltkreis `id`, `name`, `description`; jeder Kanal `gates: [a, b]`, `circuit_id`, `name`, `description`. Alle 64 Tore, sieben Schaltkreise und 36 kanonischen Kanäle müssen genau einmal vorkommen. Zentren, Kanalendpunkte und Schaltkreiszuordnungen werden gegen die unveränderliche Datenbanktopologie geprüft. Fehlerhafte Strukturen, doppelte YAML-Schlüssel und nichtkanonische Beziehungen werden abgelehnt.

Der validierte Import ist transaktional und idempotent. Er ersetzt Namen und Beschreibungen von Toren, Kanälen und Schaltkreisen; Kanalzuordnungen und Topologie bleiben unverändert. Explizites `null` löscht optionale Gate- und Kanaltexte. Profile, SVGs, Aktivierungen und Einstellungen bleiben unberührt. Fehler brechen den gesamten Import ab. Importiere nur, wenn du diese Katalogtexte bewusst übernehmen möchtest: auch ein erneuter Import des mitgelieferten Katalogs ersetzt vorhandene eigene Texte. Migrationen und Anwendungsstart importieren keinen Katalog automatisch; nach dem Import lesen Seiten und Tooltips die Datenbank beim nächsten Seitenaufruf.

**Bestehende Installationen:** Revision `0003` kann bereits angewendet sein. `alembic upgrade head` fügt mit `0004` die Schaltkreise hinzu, ordnet Kanäle zu und bewahrt vorhandene Namen, Beschreibungen und Profile. Ein Downgrade oder eine Neuerstellung der Datenbank ist nicht nötig. Der Katalogimport ist separat und überschreibt Texte; sichere die Datenbank, bevor du ihn ausführst.

## Aktuelles Profil und Geburtsaktivierungen

Jede Profilkarte unter `/profiles` enthält „Als aktuell setzen“ (CSRF-geschütztes POST-Formular, als Link gestaltet). Das gewählte Profil ist mit „Aktuell“ markiert; oben auf der Seite und auf der Karte lässt sich die Auswahl aufheben, ein anderes Profil kann jederzeit gewählt werden. Die Auswahl wird in `ui_settings.active_profile_id` gespeichert (Fremdschlüssel mit `ON DELETE SET NULL`, für SQLite mit `PRAGMA foreign_keys=ON`). Löschen des aktuellen Profils hebt die Auswahl auf. Wie die übrigen Einstellungen gilt sie global für diese lokale Einzelbenutzer-Installation; das Setzen ruft keine API auf.

Persönlichkeits- und Design-Aktivierungen werden für jedes Profil getrennt vom SVG über `/calculate` berechnet (UTC-Geburtszeitpunkt, `Etc/UTC` am Nullmeridian wie beim Chart). Ausgewertet werden `gates.prs.Planets` und `gates.des.Planets`; fehlende Quellen, leere Listen, doppelte oder ungleiche Himmelskörper und ungültige Gate-/Linien-/Längenwerte werden als Fehler abgelehnt, nie als leere Aktivierung gespeichert. Die Ergebnisse liegen in `birth_activations` (Profil, Quelle `personality`/`design`, Körper, Länge, Gate, Linie) mit Schlüssel, Engine-Version und Berechnungszeit am Profil. Vollständige Kanäle und definierte Zentren werden aus den kanonischen Tabellen `channels` und `gates` abgeleitet. Bestehende Profile bleiben bei der Migration unverändert und erhalten ihre Aktivierungen erst bei Bedarf.

Berechnet wird nur bei Bedarf: beim Einschalten des Overlays im Gate-Verlauf oder der Profilmarkierung in der Torliste über `GET /profiles/current/overlay`. Gespeicherte Aktivierungen werden wiederverwendet. Änderungen am Geburtszeitpunkt löschen sie, eine andere `HDGUI_ENGINE_VERSION` oder API-URL führt beim nächsten Abruf zur Neuberechnung; Namens- oder Ortsänderungen behalten sie. Wird das Profil während einer Berechnung geändert oder gelöscht, wird das Ergebnis verworfen (HTTP 409). API-Fehler liefern HTTP 502 und lassen gespeicherte Aktivierungen und SVGs unverändert. Löschen eines Profils entfernt seine Aktivierungen. Die Profilkarte zeigt bereits berechnete, aktuelle Aktivierungen aufklappbar an.

Im Gate-Verlauf blendet „Aktuelles Profil einblenden“ die Aktivierungen als feste, waagrechte Bänder in den Gate-Zeilen ein: Persönlichkeit in der oberen Zeilenhälfte (hell, durchgezogene Kante), Design in der unteren (rot, gestrichelte Kante); aktivierte Gate-Nummern sind hervorgehoben. Die Bänder sind zeitunabhängig und behaupten keine Bewegung. Eine Legende und eine zugängliche Tabelle (inklusive Kanälen und Zentren) ergänzen die Grafik. Das Overlay wird pro Seitenaufruf höchstens einmal erfolgreich geladen, unabhängig von Transit-Abfragen, Zoom und Navigation; Antworten nach dem Ausschalten werden verworfen. Ein in einem anderen Tab gewechseltes aktuelles Profil wird erst nach dem Neuladen der Seite übernommen. Ohne aktuelles Profil ist der Schalter deaktiviert und verweist auf `/profiles`.

## Experiment-Seiten

`/gates` zeigt alle 64 Tore, `/channels` alle 36 Kanäle sowie die sieben Schaltkreise mit jeweils einmaliger Beschreibung und Links zu ihren Kanälen. Transit-Tooltips, BodyGraph, Tierkreis und Gate-Wanderung verwenden ausschließlich Datenbanktexte, keine mitgelieferte CSV. Gegenseitige Links verbinden Tore, harmonische Partner, Kanäle und Schaltkreise. Fehlende Texte und ein fehlendes Datenbankschema werden ausdrücklich angezeigt. Die historischen Aktiv-/Passiv-Notizen der fünften CSV-Spalte werden nicht angezeigt oder übernommen.

In der Torliste sind die aktiven Tore des aktuellen Geburtsprofils standardmäßig farbig markiert, sowohl im Nummernindex als auch in den Einträgen: Persönlichkeit türkis, Design rosa, beide violett. Textkennzeichnungen ergänzen die Farben. Eine Checkbox schaltet die Markierung aus und wieder ein, ohne bereits geladene Aktivierungen neu zu berechnen. Ohne aktuelles Profil verweist die Liste auf die Profilauswahl; Berechnungsfehler werden sichtbar angezeigt und können durch erneutes Einschalten wiederholt werden.

BodyGraph und Tierkreis der Gate-Wanderung teilen Zoom und Verschiebung: Mausrad, Ziehen oder Tastatur auf einer der beiden Ansichten steuert beide. Der Tierkreis ist gegenüber der ursprünglichen kleinen Darstellung um 40 % vergrößert. Der Zoomregler und das Zurücksetzen der Ansicht gelten ebenfalls für beide.

In der Gate-Wanderung kennzeichnet ein kleiner farbiger Kreis jedes Zentrum. Die Gate-Nummern bleiben neutral. Beim Erreichen eines Gates färbt sich sein Tierkreis-Sektor in der Farbe des zugehörigen Zentrums; besuchte Sektoren bleiben bis zum Zurücksetzen eingefärbt.

`/experiments/gate-walk` zeigt eine rein geometrische Gate-Wanderung in Tierkreis-Reihenfolge. Ein Punkt bewegt sich über alle 64 Gate-Positionen und zurück zum Anfang; die Spur bleibt stehen. Neutrale Zentren und schwache Gate-Markierungen bilden den Hintergrund, keine Kanäle oder Transit-Aktivierungen. Abspielen/Pause, Geschwindigkeit und Zurücksetzen sind verfügbar. Die Animation beginnt erst nach Abspielen, läuft endlos und benötigt keine API-Anfragen. Stop hält den Punkt an und bewahrt die Spur; Abspielen setzt die Wanderung fort. Zurücksetzen hält an und löscht Spur und Sektorfarben. Nach der ersten Runde bleibt die vollständige Spur erhalten, ohne pro Runde weitere Geometrie anzusammeln.

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
