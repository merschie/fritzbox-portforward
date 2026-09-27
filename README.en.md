# fritzbox-portforward

Automatically opens and closes IPv4 port forwards on a FRITZ!Box (UPnP) for Docker
containers that carry the label `portforward`. No FRITZ!Box credentials required.
Optionally emulates the pfSense REST API so game server managers like Palisade can
create forwards through it.

## How it works

- Reads running containers via `/var/run/docker.sock` (read-only).
- When a labelled container starts, its ports are opened immediately (Docker events);
  when it stops, they are closed.
- Every `INTERVAL` seconds (default 300) it also reconciles, so forwards survive e.g. a
  FRITZ!Box reboot.
- External port = internal port; the target is the server's LAN IP.
- It only touches its own forwards: the description starts with `pfw ` followed by the
  container name, and the target is this server's IP. Manual forwards are left alone; if a
  port is already taken by someone else, that is only logged.
- On shutdown (SIGTERM) all own forwards are closed (`CLEANUP_ON_EXIT=true`).
- The FRITZ!Box stores spaces in descriptions as dots, so forwards show up as e.g.
  `pfw.minecraft`. The service accounts for this.

## Label format

Comma-separated, protocol `tcp`, `udp` or `both`; `tcp` if omitted.

```
portforward=25565/tcp
portforward=2456-2458/udp
portforward=27015/both,27020/udp
```

Invalid entries and ranges of more than 100 ports are ignored and logged.
In Unraid templates: *Add another Path, Port, Variable, Label or Device* → *Label*,
key `portforward`, value e.g. `34197/udp`, then recreate the container.

## Quick start (docker-compose.yml)

```yaml
services:
  portforward:
    image: merschie/fritzbox-portforward:latest
    container_name: portforward
    network_mode: host
    restart: unless-stopped
    stop_grace_period: 30s
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./data:/data
    environment:
      - INTERVAL=300
      - DRY_RUN=true          # start with true, check the logs, then set to false
      - CLEANUP_ON_EXIT=true
      # - LAN_IP=192.168.x.y   # optional, detected via UPnP otherwise
      # pfSense API bridge (optional)
      # - BRIDGE_ENABLED=true
      # - BRIDGE_API_KEY=${BRIDGE_API_KEY}
```

`network_mode: host` is required so UPnP discovery works and the FRITZ!Box sees the
server's own IP.

## Settings

| Variable          | Default     | Meaning                                                          |
|-------------------|-------------|------------------------------------------------------------------|
| `INTERVAL`        | `300`       | Seconds between periodic reconciles                              |
| `DRY_RUN`         | `false`     | `true`: only log what would be done                              |
| `CLEANUP_ON_EXIT` | `true`      | Close own forwards on shutdown                                   |
| `LAN_IP`          | auto        | Override the target IP                                           |
| `LEASE_DURATION`  | `0`         | Lease in seconds (0 = unlimited), renewed on every reconcile     |
| `LOG_LEVEL`       | `INFO`      | e.g. `DEBUG`                                                     |
| `BRIDGE_ENABLED`  | `false`     | Enable the pfSense API bridge                                    |
| `BRIDGE_LISTEN`   | `127.0.0.2` | Bridge listen address                                            |
| `BRIDGE_PORT`     | `443`       | Bridge port                                                      |
| `BRIDGE_API_KEY`  | –           | API key (min. 16 characters), e.g. `openssl rand -hex 24`        |
| `DATA_DIR`        | `/data`     | Bridge rules and self-signed certificate                         |

## pfSense API bridge (e.g. for Palisade)

Emulates the part of the pfSense REST API (jaredhendrickson13 package, `/api/v2`) used for
port forwards (`firewall/nat/port_forward(s)`, `firewall/apply`, `status/interfaces`) and
applies the rules via UPnP.

- Listens on `https://127.0.0.2:443` by default: loopback only, reachable from the host and
  host-network containers, not from the LAN. Doesn't collide with a web UI on 127.0.0.1:443.
  Self-signed certificate.
- Requests need the header `X-API-Key` = `BRIDGE_API_KEY`.
- Rules are stored in `/data/bridge_rules.json` and survive restarts. Enabled rules are
  opened, disabled/deleted ones closed. On the FRITZ!Box they are named `pfw <description>`.
- UPnP limits: WAN/IPv4 only, external port = internal port, target must be this server's
  LAN IP. Anything else is rejected with HTTP 400.
- If a label and the bridge request the same port, the label wins.

In Palisade under *Settings → Port forwarding*: router **pfSense**, host `127.0.0.2`,
your `BRIDGE_API_KEY`, target IP = the server's LAN IP.

## FRITZ!Box requirement

UPnP must be enabled, and for the server's device entry *Internet → Permit Access → edit
device* the option "Permit independent port sharing" must be set (click OK and then Apply).
Otherwise the FRITZ!Box rejects new forwards with "Action not authorized" (shown in the log).

## Testing the label parser

```sh
docker run --rm merschie/fritzbox-portforward python3 /app/test_parse.py
```

## License

MIT, see [LICENSE](LICENSE).
