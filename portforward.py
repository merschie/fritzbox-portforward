#!/usr/bin/env python3
"""Oeffnet/schliesst UPnP-IPv4-Portfreigaben fuer Docker-Container mit Label "portforward"."""

import logging
import os
import queue
import re
import signal
import sys
import threading
import time

LABEL = "portforward"
DESC_PREFIX = "pfw "
MAX_RANGE = 100
DESC_MAXLEN = 64

log = logging.getLogger("portforward")


def env_bool(name, default):
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


_ENTRY_RE = re.compile(r"^(\d+)(?:-(\d+))?(?:/(tcp|udp|both))?$")


def parse_label(value, container="?"):
    """Gibt eine sortierte Liste von (port, "TCP"/"UDP") zurueck; ungueltige Eintraege werden geloggt."""
    result = set()
    for raw in (value or "").split(","):
        entry = raw.strip().lower()
        if not entry:
            continue
        m = _ENTRY_RE.match(entry)
        if not m:
            log.warning("%s: ungueltiger Eintrag %r ignoriert", container, raw.strip())
            continue
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else start
        proto = m.group(3) or "tcp"
        if not (1 <= start <= 65535 and 1 <= end <= 65535) or end < start:
            log.warning("%s: ungueltiger Portbereich %r ignoriert", container, raw.strip())
            continue
        if end - start + 1 > MAX_RANGE:
            log.warning("%s: Bereich %r hat %d Ports (max. %d), ignoriert",
                        container, raw.strip(), end - start + 1, MAX_RANGE)
            continue
        protos = ("TCP", "UDP") if proto == "both" else (proto.upper(),)
        for port in range(start, end + 1):
            for p in protos:
                result.add((port, p))
    return sorted(result)


def description(container_name):
    return (DESC_PREFIX + container_name)[:DESC_MAXLEN]


def norm_desc(desc):
    """Die Fritzbox speichert Leerzeichen (und evtl. weitere Sonderzeichen) als '.',
    aus "pfw minecraft" wird "pfw.minecraft". Verglichen wird deshalb nur normalisiert."""
    return re.sub(r"[^0-9A-Za-z_-]", ".", desc or "")


class Gateway:
    """Duenne Huelle um miniupnpc; entdeckt das IGD bei Bedarf neu (z. B. nach Fritzbox-Neustart)."""

    def __init__(self, lan_ip_override=None):
        self.lan_ip_override = lan_ip_override
        self.upnp = None
        self.lan_ip = lan_ip_override
        self.external_ip = None

    def ensure(self):
        if self.upnp is not None:
            return
        import miniupnpc
        u = miniupnpc.UPnP()
        u.discoverdelay = 2000
        if u.discover() == 0:
            raise RuntimeError("kein UPnP-Geraet gefunden")
        u.selectigd()
        self.upnp = u
        self.lan_ip = self.lan_ip_override or u.lanaddr
        self.external_ip = u.externalipaddress()
        log.info("IGD gefunden, externe IP %s, Ziel-IP %s", self.external_ip, self.lan_ip)

    def reset(self):
        self.upnp = None

    def mappings(self):
        """Alle Freigaben als dict {(port, proto): (internal_ip, internal_port, desc)}."""
        out = {}
        i = 0
        while True:
            try:
                entry = self.upnp.getgenericportmapping(i)
            except Exception:
                break
            if entry is None:
                break
            ext_port, proto, (in_ip, in_port), desc = entry[0], entry[1], entry[2], entry[3]
            out[(int(ext_port), proto.upper())] = (in_ip, int(in_port), desc or "")
            i += 1
        return out

    def add(self, port, proto, desc, lease):
        self.upnp.addportmapping(port, proto, self.lan_ip, port, desc, "", lease)

    def delete(self, port, proto):
        self.upnp.deleteportmapping(port, proto)


class Service:
    def __init__(self):
        self.interval = int(os.environ.get("INTERVAL", "300"))
        self.dry_run = env_bool("DRY_RUN", False)
        self.cleanup_on_exit = env_bool("CLEANUP_ON_EXIT", True)
        self.lease = int(os.environ.get("LEASE_DURATION", "0"))
        self.gw = Gateway(os.environ.get("LAN_IP") or None)
        self.docker = None
        self.wakeup = queue.Queue()
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.bridge = None
        if env_bool("BRIDGE_ENABLED", False):
            api_key = os.environ.get("BRIDGE_API_KEY", "").strip()
            if len(api_key) < 16:
                raise SystemExit("BRIDGE_ENABLED=true braucht BRIDGE_API_KEY (mind. 16 Zeichen)")
            from pfsense_bridge import Bridge
            self.bridge = Bridge(self, api_key,
                                 os.environ.get("BRIDGE_LISTEN", "127.0.0.2"),
                                 int(os.environ.get("BRIDGE_PORT", "443")),
                                 os.environ.get("DATA_DIR", "/data"), MAX_RANGE)

    # --- Docker ---------------------------------------------------------

    def connect_docker(self):
        import docker
        # version="auto": Docker 29 lehnt die alte Default-API-Version von python3-docker ab.
        self.docker = docker.DockerClient(base_url="unix:///var/run/docker.sock", version="auto")

    def desired(self):
        """{(port, proto): desc} fuer alle laufenden Container mit Label."""
        want = {}
        for c in self.docker.containers.list(filters={"label": LABEL}):
            name = c.name
            for key in parse_label(c.labels.get(LABEL, ""), name):
                if key in want:
                    log.warning("%s: %d/%s bereits von %r angefordert, ignoriert",
                                name, key[0], key[1], want[key][len(DESC_PREFIX):])
                    continue
                want[key] = description(name)
        if self.bridge:
            for key, descr in sorted(self.bridge.store.wanted(MAX_RANGE).items()):
                if key in want:
                    log.info("Bridge-Regel %d/%s bereits per Label geoeffnet (%s)",
                             key[0], key[1], want[key][len(DESC_PREFIX):])
                    continue
                want[key] = description(descr)
        return want

    def watch_events(self):
        while not self.stop.is_set():
            try:
                for ev in self.docker.events(decode=True, filters={"type": "container",
                                                                    "event": ["start", "die"]}):
                    if self.stop.is_set():
                        return
                    attrs = ev.get("Actor", {}).get("Attributes", {})
                    if LABEL in attrs:
                        log.info("Event: %s %s", attrs.get("name", "?"), ev.get("status") or ev.get("Action"))
                        self.wakeup.put(True)
            except Exception as e:
                log.error("Docker-Events unterbrochen: %s; neuer Versuch in 5 s", e)
                self.stop.wait(5)

    # --- Abgleich -------------------------------------------------------

    def is_own(self, info):
        in_ip, _, desc = info
        return norm_desc(desc).startswith(norm_desc(DESC_PREFIX)) and in_ip == self.gw.lan_ip

    def act(self, verb, fn, *args):
        """Fuehrt eine UPnP-Aktion aus; True bei Erfolg. Fehler betreffen nur diesen einen Port."""
        prefix = "[DRY_RUN] wuerde " if self.dry_run else ""
        log.info("%s%s", prefix, verb)
        if self.dry_run:
            return True
        try:
            fn(*args)
            return True
        except Exception as e:
            hint = ""
            if "not authorized" in str(e).lower():
                hint = (" - an der Fritzbox fuer dieses Geraet 'Selbststaendige Portfreigaben"
                        " erlauben' aktivieren")
            log.error("fehlgeschlagen: %s: %s%s", verb, e, hint)
            return False

    def reconcile(self):
        with self.lock:
            try:
                want = self.desired()
            except Exception as e:
                log.error("Container konnten nicht gelesen werden: %s", e)
                return
            try:
                self.gw.ensure()
                have = self.gw.mappings()
            except Exception as e:
                log.error("UPnP nicht erreichbar: %s", e)
                self.gw.reset()
                return

            own = {k: v for k, v in have.items() if self.is_own(v)}
            added = removed = failed = 0
            for (port, proto), desc in sorted(want.items()):
                cur = have.get((port, proto))
                if cur is not None and not self.is_own(cur):
                    log.warning("%d/%s ist bereits fremd belegt (%s -> %s:%d), nicht angefasst",
                                port, proto, cur[2] or "ohne Beschreibung", cur[0], cur[1])
                    continue
                if cur is not None and norm_desc(cur[2]) == norm_desc(desc) and self.lease == 0:
                    continue
                if self.act("oeffnen %d/%s -> %s:%d (%s)" % (port, proto, self.gw.lan_ip, port, desc),
                            self.gw.add, port, proto, desc, self.lease):
                    added += 1
                else:
                    failed += 1
            for (port, proto), info in sorted(own.items()):
                if (port, proto) not in want:
                    if self.act("schliessen %d/%s (%s)" % (port, proto, info[2]),
                                self.gw.delete, port, proto):
                        removed += 1
                    else:
                        failed += 1
            if failed:
                self.gw.reset()  # beim naechsten Abgleich IGD neu suchen
            log.info("Abgleich: %d angefordert, %d eigene vorhanden, %d geoeffnet, %d geschlossen, "
                     "%d fehlgeschlagen%s", len(want), len(own), added, removed, failed,
                     " (DRY_RUN)" if self.dry_run else "")

    def cleanup(self):
        with self.lock:
            try:
                self.gw.ensure()
                own = [k for k, v in self.gw.mappings().items() if self.is_own(v)]
            except Exception as e:
                log.error("Aufraeumen nicht moeglich: %s", e)
                return
            for port, proto in sorted(own):
                self.act("schliessen %d/%s (Beenden)" % (port, proto), self.gw.delete, port, proto)

    # --- Hauptschleife --------------------------------------------------

    def run(self):
        log.info("Start: INTERVAL=%ds DRY_RUN=%s CLEANUP_ON_EXIT=%s LEASE_DURATION=%d BRIDGE=%s",
                 self.interval, self.dry_run, self.cleanup_on_exit, self.lease, bool(self.bridge))

        def on_signal(signum, _frame):
            log.info("Signal %d empfangen, beende", signum)
            self.stop.set()
            self.wakeup.put(None)

        signal.signal(signal.SIGTERM, on_signal)
        signal.signal(signal.SIGINT, on_signal)

        self.connect_docker()
        threading.Thread(target=self.watch_events, daemon=True).start()

        first = True
        while not self.stop.is_set():
            self.reconcile()
            if first and self.bridge:
                self.bridge.start()  # nach dem ersten Abgleich, dann sind LAN- und WAN-IP bekannt
            first = False
            try:
                self.wakeup.get(timeout=self.interval)
                time.sleep(1)  # kurze Entprellung, mehrere Events auf einmal abarbeiten
                while not self.wakeup.empty():
                    self.wakeup.get_nowait()
            except queue.Empty:
                pass

        if self.cleanup_on_exit:
            self.cleanup()
        log.info("Beendet")


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(message)s", stream=sys.stdout)
    Service().run()


if __name__ == "__main__":
    main()
