# Konzept: Human-Design-WebUI

## Ziel und Priorität

Die Hauptfunktion der WebUI ist die dynamische Darstellung des aktuellen Human-Design-Transits auf einem Rad mit Tierkreis und 64 Gates. Die Planetenpositionen bewegen sich entsprechend der gewählten Zeit in Echtzeit oder beschleunigt. Das Rad soll jederzeit zeigen, für welchen Zeitpunkt die dargestellten Positionen gelten.

Ein Geburtshoroskop mit Persönlichkeit und Design ist eine ergänzende Funktion, nicht der primäre Anwendungsfall. Die Berechnungslogik für Transits und Charts soll von `humandesign_api` kommen.

## Technischer Vorschlag

- **Webanwendung:** Python mit FastAPI
- **HTML:** Jinja2-Templates; HTMX für Formulare und gezielte Aktualisierungen
- **Visualisierung:** responsives SVG; ein kleiner JavaScript-Player aktualisiert und animiert die Transitpositionen clientseitig
- **HD-Berechnung:** `humandesign_api`, zunächst als separater lokaler Dienst und über HTTP angebunden
- **Konfiguration:** Basis-URL des Berechnungsdienstes und API-Token über Umgebungsvariablen, nicht im Quellcode

Die Trennung über HTTP hält die UI unabhängig von internen Python-Modulen der Berechnungsbibliothek. Ein Adapter in der Webanwendung übersetzt API-Antworten in ein eigenes, stabiles Datenmodell. Damit lassen sich Änderungen an der API an einer Stelle auffangen. JavaScript übernimmt nur Abspielen, Pausieren und die flüssige Bewegung zwischen berechneten Positionen; fachliche HD-Berechnungen bleiben beim Berechnungsdienst.

Vor der Integration sind Lizenzbedingungen, Antwortfelder und Transit-Fähigkeiten der konkreten `humandesign_api`-Version zu prüfen. Insbesondere muss der Dienst Positionen für frei wählbare Zeitpunkte liefern, nicht nur Tagestransite oder ein fertiges BodyGraph-Bild. Für das animierte Rad sind exakte ekliptikale Längen für die Planeten und deren Gates/Linien nötig. Falls die API keine frei wählbaren Zeitpunkte ausreichend schnell unterstützt, ist vor der Implementierung zu entscheiden, ob ihr Berechnungskern direkt eingebunden oder die Transit-Funktion anderweitig ergänzt wird; die UI soll keine Planetenbewegung aus groben Tagesdaten vortäuschen.

## Transit-Modus

1. Beim Öffnen des Transit-Modus wird der aktuelle Zeitpunkt als Startzeit verwendet. Nutzer können alternativ einen Zeitpunkt oder Zeitraum wählen.
2. Die Webanwendung fordert über den API-Adapter Transitpositionen für diesen Zeitpunkt und die folgenden Abtastzeitpunkte an.
3. Der Adapter normalisiert Planetenpositionen, Tierkreiszeichen, Gates und Linien in Transit-Datenframes.
4. Der Player zeigt die Frames auf dem SVG-Rad an und bewegt die Markierungen in der gewählten Geschwindigkeit. Zwischen Frames wird die Winkelbewegung visuell interpoliert; fachliche Positions-, Gate- und Linienwerte stammen stets aus berechneten Frames.
5. Eine zugängliche Text-/Tabellenansicht zeigt Zeitpunkt und Aktivierungen auch ohne Animation.

Der Player bietet mindestens **Abspielen/Pause**, **Echtzeit**, mehrere Beschleunigungsstufen, Zeitnavigation und **Zurück zu jetzt**. Die Anzeige nennt die simulierte Uhrzeit und Geschwindigkeit. Bei Ende verfügbarer Frames werden weitere Zeitpunkte angefordert; Ladezustand und Berechnungsfehler werden sichtbar angezeigt. Die Anzeige darf beim Wechsel der Geschwindigkeit keine Zeit rückwärts springen lassen.

Die Abtastrate muss so gewählt werden, dass relevante Positionsänderungen und Gate-/Linienwechsel rechtzeitig aktualisiert werden, ohne pro Animationsbild eine API-Anfrage auszulösen. Frames können für den sichtbaren Zeitraum zwischengespeichert werden. Über Intervall, Vorberechnung und Cache wird nach Messung der API-Latenz entschieden. Positionen werden über den 0°/360°-Übergang hinweg interpoliert, ohne den langen Weg rückwärts um den Tierkreis zu nehmen.

## Geburtschart-Modus

1. Die Nutzerin oder der Nutzer gibt lokales Geburtsdatum, Uhrzeit, IANA-Zeitzone und Geburtsort ein. Koordinaten können alternativ direkt angegeben werden.
2. Die Webanwendung validiert Eingaben und sendet sie über den API-Adapter an `humandesign_api`.
3. Die API berechnet Persönlichkeit und Design; der Adapter normalisiert Positionen, Tierkreiszeichen, Gates und Linien in das interne Modell.
4. Das Chart wird auf demselben Rad in getrennten Ringen angezeigt.

Die Design-Zeit wird von der Berechnungs-Engine geliefert. Die Webanwendung berechnet sie nicht selbst, damit keine zweite, möglicherweise abweichende Implementierung der HD-Regeln entsteht.

## Datenmodell der Webanwendung

Die Modelle sind eine interne Normalform und keine Behauptung über die exakte JSON-Struktur der API. Das API-Mapping wird nach Prüfung der gewählten Version festgelegt.

```python
from datetime import datetime
from enum import StrEnum
from pydantic import BaseModel, Field


class ActivationSource(StrEnum):
    TRANSIT = "transit"
    PERSONALITY = "personality"
    DESIGN = "design"


class BirthData(BaseModel):
    local_datetime: datetime
    timezone: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class Activation(BaseModel):
    body: str
    source: ActivationSource
    ecliptic_longitude: float = Field(ge=0, lt=360)
    zodiac_sign: str
    sign_degree: float = Field(ge=0, lt=30)
    gate: int = Field(ge=1, le=64)
    line: int = Field(ge=1, le=6)


class TransitFrame(BaseModel):
    timestamp_utc: datetime
    activations: list[Activation]


class TransitPlayback(BaseModel):
    current_time_utc: datetime
    speed_multiplier: float = Field(gt=0)
    is_playing: bool


class BirthChart(BaseModel):
    birth: BirthData
    design_datetime_utc: datetime
    activations: list[Activation]
```

`TransitFrame` beschreibt die fachlich berechneten Positionen für einen Zeitpunkt; `TransitPlayback` den flüchtigen Zustand des Players. Transit-Frames werden zunächst nicht dauerhaft gespeichert. Geburtsprofile und erzeugte Chart-SVGs werden dagegen persistent gespeichert. API-Rohdaten sollten nicht ungeprüft in Templates gelangen; die Darstellung verwendet ausschließlich die normalisierten Modelle.

## Persistenz und spätere Mehrbenutzernutzung

Umgesetzt: Ein Profil kann als „aktuell“ gewählt werden (`ui_settings.active_profile_id`, nullable Fremdschlüssel mit `SET NULL`). Persönlichkeits- und Design-Aktivierungen werden je Profil aus `/calculate` (`gates.prs` und `gates.des`) normalisiert und in `birth_activations` gespeichert, getrennt vom SVG-Cache und mit eigenem Schlüssel aus UTC-Geburtszeit, Dienst und Engine-Version. Sie werden erst bei Bedarf (Overlay im Gate-Verlauf) berechnet. Die Aktiv-/Passiv-Notizen der Gate-CSV gehören zu einem einzelnen historischen Profil und werden nie auf andere Profile übertragen. Tore (64), Kanäle (36, ungeordnete Gate-Paare) und sieben Schaltkreise liegen kanonisch in der Datenbank; jeder Kanal verweist unveränderlich auf genau einen Schaltkreis. Beschreibungen werden lokal importiert, und wiederholte Schaltkreistexte stehen nur einmal beim Schaltkreis. Bei Mehrbenutzerbetrieb muss die Auswahl des aktuellen Profils pro Benutzer gespeichert werden.


SQLAlchemy bildet die Datenzugriffsschicht, Alembic verwaltet Schema-Migrationen. Die lokale erste Version verwendet SQLite; PostgreSQL mit passendem Treiber ist für den späteren Mehrbenutzerbetrieb vorgesehen. Jede Anfrage beziehungsweise Hintergrundaufgabe erhält eine eigene Session.

Ein Geburtsprofil speichert eine UUID, Anzeigenamen, lokale Geburtszeit, IANA-Zeitzone, UTC-Zeitpunkt und Geburtsort. Ein Chart-Cache speichert das erzeugte SVG, Erstellungszeitpunkt und einen Schlüssel aus UTC-Geburtszeit, Berechnungsdienst und Engine-Version. Änderungen an berechnungsrelevanten Daten invalidieren das SVG. Ein fehlgeschlagener Refresh ersetzt keine vorhandenen Daten.

Die erste Umsetzung priorisiert Profilverwaltung und Chart-Speicherung vor der Transit-Animation. Sie ist ausschließlich lokal und ohne Benutzeranmeldung vorgesehen. Vor öffentlichem Mehrbenutzerbetrieb werden Benutzerkonten, Profil-Eigentümerschaft und serverseitige Zugriffskontrollen ergänzt. Allgemeine Transit-Frames können dann nutzerübergreifend gecacht werden; private Geburtsdaten und Player-Zustände bleiben getrennt. Export, Löschung, Sicherungen und vertrauliche Behandlung der Geburtsdaten gehören zum weiteren Ausbau.

## Aufbau des Rads

- Tierkreiszeichen und 64 Gate-Sektoren bilden die feste Orientierung des Rads.
- Im Transit-Modus werden die Planetenpositionen auf einem deutlich hervorgehobenen Ring angezeigt. Zusätzliche Ringe können Gate-/Linienangaben oder Aktivierungsdetails darstellen.
- Im Geburtschart-Modus erhalten Persönlichkeit und Design jeweils einen eigenen Ring; dieser Modus verwendet dieselbe visuelle Grammatik wie die Transit-Ansicht.
- Die Position jeder Markierung wird aus der ekliptikalen Länge abgeleitet. Die Gate-Zuordnung wird aus dem Ergebnis der Berechnungs-Engine übernommen, nicht durch eine vereinfachte gleichmäßige Aufteilung der 360 Grad neu berechnet.
- Bei eng beieinanderliegenden Markierungen werden Beschriftungen versetzt oder mit Führungslinien versehen, damit sie lesbar bleiben.
- Eine Legende unterscheidet Transit, Persönlichkeit und Design auch ohne Farbwahrnehmung, zum Beispiel durch Farbe plus Symbolform.
- Zeitpunkt, Abspielstatus und Geschwindigkeit sind direkt am Rad sichtbar.
- Eine Tabelle unter dem Rad bietet dieselben Aktivierungen textuell und ist für Tastatur- und Screenreader-Nutzung vorgesehen.

Das SVG ist responsiv und enthält beschreibende Titel beziehungsweise zugängliche Beschriftungen. Ein separat erzeugtes BodyGraph-Bild der API kann später ergänzend eingebunden werden, ersetzt aber nicht das gewünschte animierte Tierkreisrad.

## Vorgeschlagene Modulstruktur

```text
app/
  main.py
  config.py
  api_client.py       # HTTP-Aufruf an humandesign_api
  chart_adapter.py    # API-Antwort -> TransitFrame oder BirthChart
  transit_service.py  # Zeitfenster, Frame-Anfragen und Cache
  models.py
  views.py
  templates/
  static/
    css/
    js/
```

## Erste Ausbaustufe

1. Verwendete `humandesign_api`-Version, Lizenz und Nutzungsbedingungen festlegen.
2. SQLAlchemy, Alembic und persistente Geburtsprofilverwaltung aufsetzen.
3. BodyGraph-SVGs über die API erzeugen, persistent zwischenspeichern und bei geänderten Geburtsdaten invalidieren.
4. Prüfen, ob die API Transitpositionen für frei wählbare Zeitpunkte mit ausreichender Genauigkeit und Latenz liefert; Antwortschema und exakte Längen verifizieren.
5. API-Client, Adapter und Frame-Service für ein Transit-Zeitfenster implementieren.
6. SVG-Tierkreis sowie Player mit Pause, Echtzeit und Beschleunigungsstufen umsetzen.
7. Tabellarische Alternative ergänzen und Geburtsprofile als zusätzliche Rad-Ebenen einbinden.

Die Schnittstelle soll verständliche Fehler anzeigen, wenn der Berechnungsdienst nicht erreichbar ist, Eingaben ablehnt oder erforderliche Positionsdaten nicht liefert. In letzterem Fall wird nicht stillschweigend eine ungenaue Position aus dem Gate abgeleitet.
