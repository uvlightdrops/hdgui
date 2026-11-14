> KI-Autorenhinweis: Erstellt durch GitHub Copilot (Auto-Modus; zugrunde liegendes Modell nicht identifiziert) am 2026-11-14.

---
project: hdgui
scope: guides
created: 2026-11-14 17
updated: 2026-11-14 19
update_reason: Sprachwahl und gekennzeichnete Rückfalltexte im gemeinsamen Wissenskatalog ergänzen.
status: active
agent_project: hdgui
---

# Wissen erkunden

[Guides-Übersicht](README.md) · [Technisches UI-Konzept](../features/knowledge-ui.md) · [Praxisleitfaden](practice.de.md)

## Einstieg

**Wissen** führt in einen gemeinsamen Bereich mit vier Themen-Tabs.
Darunter wählst du zunächst eine Kategorie und dann einen einzelnen Eintrag.
Ein Tab steht also für ein Thema, nicht für jedes Tor, jeden Typ oder jede
andere Entität.

## Welcher Bereich passt?

| Tab | Was du dort findest | Beispiel |
|---|---|---|
| **Grundlagen** | Typen, Strategien, Signaturen, Nicht-Selbst-Themen, Aura, Autoritäten, HD-Profile, Himmelskörper sowie Persönlichkeit und Design | Einen Typ auswählen und seinen Katalogtext lesen |
| **BodyGraph** | Zentren, Definition, Tore, Kanäle, Schaltkreise, allgemeine Linien und spezifische Torlinien | Von einem Tor zu seinen Kanälen und deren Schaltkreis wechseln |
| **Variablen** | Variablen, Pfeilrichtungen, Color, Tone, Base, Determination / Verdauung, Umgebung, Perspektive, Motivation und Kognition | Die Begriffe einer Variablenanzeige nachschlagen |
| **Inkarnationskreuze** | Kreuz-Geometrie und tatsächlich erfasste Inkarnationskreuze | Die gespeicherte Vier-Tore-Kombination eines Kreuzes ansehen |

**Profile** unter Grundlagen meint die zwölf HD-Profilbegriffe.
Deine gespeicherten Personen und Geburtsdaten bleiben unter **Geburtsprofile**.
Ebenso beschreibt **Zentren** im Wissensbereich die Katalogbegriffe;
der **Zentren-Verlauf** bleibt eine eigene zeitbezogene Analyseansicht.

## Vom Thema zum Eintrag

Beim Öffnen von Wissen startet die Ansicht mit **Grundlagen / Typen**.
Ein Themenwechsel zeigt dessen erste Kategorie. Nach der Kategorienwahl siehst
du die verfügbaren Einträge; du entscheidest selbst, welchen du öffnest.
Auf einem kleinen Bildschirm stehen Navigation, Liste und Inhalt untereinander.

Beispiel: **BodyGraph → Tore → Tor 1**. Im Eintrag findest du die Bezeichnung,
die strukturellen Angaben und den vorhandenen Beschreibungstext. Verknüpfte
Kanäle und Zentren führen zu den entsprechenden Katalogeinträgen.
Bestehende grafische Tor- und Kanalansichten bleiben erhalten.

Für eine konkrete Torlinie gehst du über **BodyGraph → Torlinien**:
zuerst das Tor, dann eine seiner sechs Linien auswählen. Die Kategorie
**Linien** enthält dagegen die sechs allgemeinen Linienthemen.
Ein allgemeiner Linientext ersetzt keinen spezifischen Torlinientext.

Die ausgewählte Stelle ist direkt verlinkbar. Beim Teilen oder Speichern
der Adresse bleibt daher erkennbar, welches Thema, welche Kategorie und welcher
Eintrag gemeint sind. Zurück und Vorwärts im Browser folgen deinen
Navigationsschritten; ein ausgewähltes Geburtsprofil ist zum Nachschlagen
nicht erforderlich.

## Texte und Lücken erkennen

Ein Eintrag kann strukturell vorhanden sein, obwohl noch kein Beschreibungstext
geschrieben wurde. Dann erscheint **„Noch kein Beschreibungstext“**.
Eine leere Kategorie ist etwas anderes als ein Ladefehler: Ein Datenbankproblem
wird ausdrücklich gemeldet.

Die Textkennzeichnung zeigt Herkunft, Autor, gegebenenfalls Modell, Datum und
Prüfstatus. Ein als geprüft markierter KI-Text bleibt ein KI-Text; Herkunft und
Prüfung sind getrennte Angaben. Die Oberfläche ergänzt fehlende Beschreibungen
nicht automatisch durch erfundene Deutungen.

In **Settings** kannst du die Katalogsprache wählen. Ohne feste Auswahl wird
die Browsersprache berücksichtigt. Fehlt der passende Text, erscheint der
vorhandene deutsche Text mit seiner tatsächlichen Inhaltssprache und einem
Rückfallhinweis. Die Sprache ändert weder die Eintragskennung noch Berechnungen;
die Bedienoberfläche bleibt deutsch.

Bei **Himmelskörpern** bleiben strukturierte HD-Einträge und symbolische
Beschreibungen unterscheidbar. Die gemeinsame Navigation macht daraus keine
einheitliche fachliche Aussage.

**Erfasste Inkarnationskreuze** enthält nur importierte oder aus gespeicherten
Geburtsberechnungen erfasste Kombinationen, kein vollständiges Verzeichnis.
Die vier Tore werden in der Reihenfolge Persönlichkeit Sonne/Erde und
Design Sonne/Erde angezeigt. Ein nicht gelistetes Kreuz ist deshalb kein
Beleg für eine fehlerhafte persönliche Berechnung.

## Wissen ist keine persönliche Deutung

Der Wissensbereich erklärt Katalogbegriffe. Er berechnet beim Blättern kein
neues Chart und leitet aus einem Beschreibungstext keine persönliche Aussage ab.
Auf der neuen Torseite kannst du die farbige Profilmarkierung ausdrücklich
einschalten. Erst dann werden Geburtsaktivierungen geladen und gegebenenfalls
berechnet. Die bisherigen Torlinks behalten ihre automatisch eingeschaltete
Markierung. Sie ist ein zusätzlicher Profilbezug, keine allgemeine Eigenschaft
des Katalogeintrags.

Die Erklärung zum Zentren-Verlauf findest du über **Hilfe zum Zentren-Verlauf**.
Die bisherigen Tor-, Kanal- und Entitätenadressen bleiben erreichbar.

Human Design ist ein interpretatives, wissenschaftlich nicht bestätigtes
System. Insbesondere Verdauung, Umgebung und Kognition sind hier systeminterne
Begriffe, keine Ernährungs-, Wohn- oder Gesundheitsempfehlungen.
Für reflektierende Nutzung und weitere Grenzen siehe den
[Praxisleitfaden](practice.de.md).
