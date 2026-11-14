---
project: hdgui
scope: guides
created: 2026-11-14 22
updated: 2026-11-14 22
update_reason: Bedienung der zusammengeführten Ansichten dokumentieren.
status: active
agent_project: hdgui
---

# Gate-Verlauf

Im Menü **Gate-Verlauf** öffnen und zwischen **Grafik** und **Tabelle** wechseln.
Zeitraum und Auswahl der Himmelskörper gelten für beide Ansichten; die Zeiten
werden in UTC eingegeben und angezeigt.

## Grafik

Die Grafik zeigt berechnete Gate-Positionen über die Zeit. Zeitraum, Detailgrad,
Navigation und Zoom bestimmen den Ausschnitt und seine Abtastpunkte. Ein
ausgewähltes Geburtsprofil kann als feste Referenz eingeblendet werden. Für eine
zugängliche Alternative lässt sich unter der Grafik eine Tabelle aufklappen.

## Tabelle

Die Tabelle zeigt für jeden ausgewählten Himmelskörper den Zeitpunkt, die
Position innerhalb des Tierkreiszeichens in Grad und Minuten, die absolute
Tierkreislänge sowie Gate und Linie. Unter **Tabellenpunkte** stehen zur Auswahl:

- **Grafik-Abtastung:** dieselben adaptiv gewählten Punkte wie in der Grafik;
  der Detailgrad beeinflusst das Raster.
- **Einzelzeitpunkt:** eine Berechnung zum eingegebenen Zeitpunkt **Von**.
- **Stündlich, alle 6 Stunden oder täglich:** feste Abstände ab **Von**. **Bis**
  wird nur einbezogen, wenn es genau auf einen solchen Punkt fällt.

Feste Reihen sind auf 160 Zeitpunkte begrenzt. Bei einer entsprechenden
Fehlermeldung den Zeitraum verkürzen oder einen größeren Abstand wählen.
Die früheren Menüeinträge **Ephemeriden** und **Zeitreihe** sind in dieser
Tabelle zusammengeführt; ihre bisherigen direkten URLs bleiben erreichbar.

## Grenzen

Tabellen und Grafik zeigen berechnete Stichproben, keine vollständige Liste
exakter Gate-Wechsel. Zwischen zwei Punkten können kurze Wechsel liegen.
Die Grafik verbindet benachbarte berechnete Punkte; Lücken bleiben offen.
Ein eingeblendetes Geburtsprofil ist eine feste Referenz und kein Transit.
