# fritzbox-portforward

*English version: [README.en.md](README.en.md) · Docker image: [merschie/fritzbox-portforward](https://hub.docker.com/r/merschie/fritzbox-portforward)*

Öffnet und schließt an der Fritzbox automatisch IPv4-Portfreigaben (UPnP) für
Docker-Container, die das Label `portforward` tragen. Keine Fritzbox-Zugangsdaten nötig.

## Funktionsweise

- Liest die laufenden Container über `/var/run/docker.sock` (read-only).
- Startet ein Container mit Label, werden seine Ports sofort geöffnet (Docker-Events);
  stoppt er, werden sie geschlossen.
- Alle `INTERVAL` Sekunden (Standard 300) wird zusätzlich abgeglichen, damit Freigaben
  z. B. einen Fritzbox-Neustart überstehen.
- Externer Port = interner Port, Ziel ist die LAN-IP des Servers.
- Der Dienst fasst nur eigene Freigaben an: Beschreibung beginnt mit `pfw `, gefolgt vom
  Containernamen, und das Ziel ist die IP dieses Servers. Manuelle Freigaben bleiben unberührt;
  ist ein Port schon fremd belegt, wird das nur geloggt.
- Beim Beenden (SIGTERM) werden alle eigenen Freigaben geschlossen (`CLEANUP_ON_EXIT=true`).

## Label-Format

Kommagetrennt, Protokoll `tcp`, `udp` oder `both`; ohne Protokoll gilt `tcp`.

```
portforward=25565/tcp
portforward=2456-2458/udp
portforward=27015/both,27020/udp
```

Ungültige Einträge und Bereiche mit mehr als 100 Ports werden ignoriert und geloggt.
In Unraid-Templates: *Add another Path, Port, Variable, Label or Device* → *Label*,
Key `portforward`, Value z. B. `34197/udp`. Danach den Container neu erstellen.

## pfSense-Brücke für Palisade

Palisade kann Portfreigaben über die pfSense-REST-API anlegen. Der Dienst emuliert den Teil
dieser API, den Palisade nutzt (`/api/v2/firewall/nat/port_forward(s)`, `/api/v2/firewall/apply`,
`/api/v2/status/interfaces`), und setzt die Regeln per UPnP an der Fritzbox um.

- Lauscht auf `https://127.0.0.2:443` (Loopback, nur vom Host und von Containern mit
  Host-Netz erreichbar, nicht aus dem LAN). Palisade fragt fest Port 443 an; 127.0.0.2 kollidiert
  nicht mit Unraids nginx auf 127.0.0.1:443. Zertifikat ist selbstsigniert (Palisade akzeptiert das).
- Zugriff nur mit `X-API-Key` = `BRIDGE_API_KEY` aus der Datei `.env`.
- Regeln liegen in `data/bridge_rules.json` und überstehen Neustarts. Aktive Regeln werden beim
  Abgleich geöffnet, deaktivierte/gelöschte geschlossen. In der Fritzbox heißen sie
  `pfw <Beschreibung aus Palisade>`.
- Einschränkungen durch UPnP: nur WAN/IPv4, externer = interner Port, Ziel muss die LAN-IP dieses
  Servers sein. Andere Anfragen werden mit HTTP 400 abgelehnt.
- Fordern ein Label und Palisade denselben Port an, gilt das Label.

In Palisade unter *Settings → Port forwarding*: Router **pfSense**, Host `127.0.0.2`,
API-Key aus `.env`, Target-IP = LAN-IP des Servers.

## Einstellungen (docker-compose.yml)

| Variable          | Standard | Bedeutung                                                    |
|-------------------|----------|--------------------------------------------------------------|
| `INTERVAL`        | `300`    | Sekunden zwischen den regelmäßigen Abgleichen                |
| `DRY_RUN`         | `false`  | `true`: nur loggen, was getan würde                          |
| `CLEANUP_ON_EXIT` | `true`   | Eigene Freigaben beim Beenden schließen                      |
| `LAN_IP`          | auto     | Ziel-IP überschreiben                                        |
| `LEASE_DURATION`  | `0`      | Gültigkeit in Sekunden (0 = unbegrenzt), wird laufend erneuert |
| `LOG_LEVEL`       | `INFO`   | z. B. `DEBUG`                                                |
| `BRIDGE_ENABLED`  | `false`  | pfSense-API-Brücke einschalten                               |
| `BRIDGE_LISTEN`   | `127.0.0.2` | Adresse der Brücke                                        |
| `BRIDGE_PORT`     | `443`    | Port der Brücke                                              |
| `BRIDGE_API_KEY`  | –        | API-Key (mind. 16 Zeichen), steht in `.env`                  |

## Bedienung

Unraid hat kein `docker compose`; `compose.sh` führt Compose aus dem Image `docker:cli` aus.

```sh
cd /mnt/user/appdata/portforward   # bzw. dein Projektordner
./compose.sh up -d --build      # bauen und starten
docker logs -f portforward      # Logs
./compose.sh down               # stoppen (schließt eigene Freigaben)
docker exec portforward python3 /app/test_parse.py   # Label-Auswertung testen
```

Nach Änderungen an `docker-compose.yml` (z. B. `DRY_RUN=false`) erneut `./compose.sh up -d`.

## Voraussetzung an der Fritzbox

UPnP muss aktiv sein und für den Server muss unter *Internet → Freigaben → Gerät bearbeiten*
„Selbstständige Portfreigaben erlauben“ gesetzt sein. Sonst lehnt die Fritzbox das Anlegen ab
(Fehler im Log).
