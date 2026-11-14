---
project: hdgui
scope: backend
created: 2026-11-14 20
updated: 2026-11-14 20
update_reason: Add standalone root-server API startup
status: current
agent_project: hdgui
---

# Root-Server-API

`start-api-container.sh` startet den Human Design-Berechnungsdienst unabhängig von der HDGUI. Docker Engine und `curl` müssen installiert sein. Der Token muss derselbe sein, den die HDGUI verwendet. Übertrage dafür die vorhandene `.hdgui-api-token` sicher auf den Root-Server ins Projektverzeichnis, oder setze `HD_API_TOKEN` für den Skriptaufruf. Die Datei wird auf Rechte `600` gesetzt.

```bash
./start-api-container.sh
```

Der Container wird mit `unless-stopped` gestartet und nur an `127.0.0.1:9021` veröffentlicht. Für eine HDGUI auf einem anderen Host den Dienst über einen HTTPS-Reverse-Proxy erreichbar machen und in der HDGUI `HDGUI_API_URL` auf dessen HTTPS-Adresse setzen. Die HDGUI mit `--no-api` starten. Port `9021` nicht direkt öffentlich freigeben.
