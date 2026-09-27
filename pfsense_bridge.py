"""Emuliert den Teil der pfSense-REST-API (jaredhendrickson13, /api/v2), den Palisade nutzt.

Regeln werden nur gespeichert; der Abgleich in portforward.py setzt sie per UPnP um.
Wie bei pfSense ist die id der Index in der Regelliste (verschiebt sich beim Loeschen).
"""

import hmac
import json
import logging
import os
import re
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

log = logging.getLogger("portforward.bridge")

PROTOS = {"tcp": ("TCP",), "udp": ("UDP",), "tcp/udp": ("TCP", "UDP")}
_PORT_RE = re.compile(r"^(\d+)(?:[:-](\d+))?$")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def parse_ports(spec, max_range):
    m = _PORT_RE.match(str(spec).strip())
    if not m:
        raise ApiError(400, "destination_port %r nicht unterstuetzt (nur Port oder Bereich)" % spec)
    start = int(m.group(1))
    end = int(m.group(2) or start)
    if not (1 <= start <= end <= 65535):
        raise ApiError(400, "ungueltiger Port %r" % spec)
    if end - start + 1 > max_range:
        raise ApiError(400, "Bereich %r groesser als %d Ports" % (spec, max_range))
    return start, end


class RuleStore:
    def __init__(self, path, max_range):
        self.path = path
        self.max_range = max_range
        self.lock = threading.Lock()
        self.rules = []
        try:
            with open(path) as f:
                self.rules = json.load(f)
            log.info("%d Bridge-Regeln aus %s geladen", len(self.rules), path)
        except FileNotFoundError:
            pass

    def _save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.rules, f, indent=2)
        os.replace(tmp, self.path)

    def listed(self):
        with self.lock:
            return [dict(r, id=i) for i, r in enumerate(self.rules)]

    def _validate(self, rule, lan_ip):
        if rule.get("interface", "wan") != "wan":
            raise ApiError(400, "nur interface 'wan' unterstuetzt")
        if rule.get("ipprotocol", "inet") != "inet":
            raise ApiError(400, "nur IPv4 (ipprotocol 'inet') unterstuetzt")
        if rule["protocol"] not in PROTOS:
            raise ApiError(400, "protocol muss tcp, udp oder tcp/udp sein")
        start, end = parse_ports(rule["destination_port"], self.max_range)
        if parse_ports(rule["local_port"], self.max_range) != (start, end):
            raise ApiError(400, "local_port muss gleich destination_port sein (UPnP: extern = intern)")
        if lan_ip and rule["target"] != lan_ip:
            raise ApiError(400, "target muss die LAN-IP dieses Servers sein (%s)" % lan_ip)

    def create(self, body, lan_ip):
        rule = {
            "interface": body.get("interface", "wan"),
            "ipprotocol": body.get("ipprotocol", "inet"),
            "protocol": str(body.get("protocol", "tcp")).lower(),
            "source": body.get("source", "any"),
            "destination": body.get("destination", "wan:ip"),
            "destination_port": str(body.get("destination_port", "")),
            "target": str(body.get("target", "")),
            "local_port": str(body.get("local_port") or body.get("destination_port", "")),
            "descr": str(body.get("descr", "")),
            "associated_rule_id": body.get("associated_rule_id", ""),
            "disabled": bool(body.get("disabled", False)),
        }
        self._validate(rule, lan_ip)
        with self.lock:
            self.rules.append(rule)
            self._save()
            return dict(rule, id=len(self.rules) - 1)

    def _index(self, rule_id):
        try:
            i = int(rule_id)
        except (TypeError, ValueError):
            raise ApiError(400, "id fehlt oder ist ungueltig")
        if not 0 <= i < len(self.rules):
            raise ApiError(404, "Regel %s existiert nicht" % rule_id)
        return i

    def update(self, body, lan_ip):
        editable = ("protocol", "destination_port", "target", "local_port", "descr", "disabled")
        with self.lock:
            i = self._index(body.get("id"))
            rule = dict(self.rules[i])
            for k in editable:
                if k in body:
                    rule[k] = bool(body[k]) if k == "disabled" else str(body[k]).lower() if k == "protocol" else str(body[k])
            self._validate(rule, lan_ip)
            self.rules[i] = rule
            self._save()
            return dict(rule, id=i)

    def delete(self, rule_id):
        with self.lock:
            i = self._index(rule_id)
            rule = self.rules.pop(i)
            self._save()
            return dict(rule, id=i)

    def wanted(self, max_range):
        """{(port, proto): descr} aller aktiven Regeln."""
        out = {}
        with self.lock:
            for r in self.rules:
                if r.get("disabled"):
                    continue
                start, end = parse_ports(r["destination_port"], max_range)
                for port in range(start, end + 1):
                    for p in PROTOS[r["protocol"]]:
                        out.setdefault((port, p), r["descr"] or "bridge")
        return out


class Bridge:
    def __init__(self, service, api_key, listen, port, data_dir, max_range):
        self.service = service
        self.api_key = api_key
        self.listen = listen
        self.port = port
        self.data_dir = data_dir
        self.store = RuleStore(os.path.join(data_dir, "bridge_rules.json"), max_range)

    def _certificate(self):
        crt = os.path.join(self.data_dir, "bridge.crt")
        key = os.path.join(self.data_dir, "bridge.key")
        if not (os.path.exists(crt) and os.path.exists(key)):
            log.info("Erzeuge selbstsigniertes Zertifikat fuer die Bridge")
            subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "3650",
                            "-subj", "/CN=portforward-bridge", "-keyout", key, "-out", crt],
                           check=True, capture_output=True)
            os.chmod(key, 0o600)
        return crt, key

    def start(self):
        bridge = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "pfSense-bridge"

            def log_message(self, fmt, *args):
                log.debug("%s %s", self.address_string(), fmt % args)

            def _send(self, status, data=None, message=""):
                payload = json.dumps({
                    "code": status, "status": "ok" if status < 400 else "error",
                    "response_id": "SUCCESS" if status < 400 else "ERROR",
                    "message": message, "data": data if data is not None else [],
                }).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                if not n:
                    return {}
                try:
                    body = json.loads(self.rfile.read(n))
                except ValueError:
                    raise ApiError(400, "ungueltiges JSON")
                if not isinstance(body, dict):
                    raise ApiError(400, "JSON-Objekt erwartet")
                return body

            def _handle(self, method):
                try:
                    if not hmac.compare_digest(self.headers.get("X-API-Key", ""), bridge.api_key):
                        raise ApiError(401, "API-Key fehlt oder ist falsch")
                    url = urlparse(self.path)
                    status, data = bridge.dispatch(method, url.path.rstrip("/"), parse_qs(url.query),
                                                   self._body() if method != "GET" else {})
                    self._send(status, data)
                except ApiError as e:
                    if e.status == 401:
                        log.warning("Bridge: abgewiesener Zugriff von %s", self.client_address[0])
                    else:
                        log.warning("Bridge: %s %s -> %d %s", method, self.path, e.status, e)
                    self._send(e.status, message=str(e))
                except Exception as e:
                    log.exception("Bridge: Fehler bei %s %s", method, self.path)
                    self._send(500, message=str(e))

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_PATCH(self):
                self._handle("PATCH")

            def do_DELETE(self):
                self._handle("DELETE")

        crt, key = self._certificate()
        httpd = ThreadingHTTPServer((self.listen, self.port), Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(crt, key)
        httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        log.info("pfSense-Bridge lauscht auf https://%s:%d (%d Regeln)",
                 self.listen, self.port, len(self.store.rules))

    def dispatch(self, method, path, query, body):
        lan_ip = self.service.gw.lan_ip
        if method == "GET" and path == "/api/v2/firewall/nat/port_forwards":
            return 200, self.store.listed()
        if method == "GET" and path == "/api/v2/status/interfaces":
            ext = self.service.gw.external_ip
            return 200, [{"name": "wan", "descr": "WAN", "ipaddr": ext}] if ext else []
        if path == "/api/v2/firewall/nat/port_forward":
            if method == "POST":
                rule = self.store.create(body, lan_ip)
                log.info("Bridge: Regel %d angelegt: %s/%s (%s)", rule["id"],
                         rule["destination_port"], rule["protocol"], rule["descr"])
                return 200, rule
            if method == "PATCH":
                rule = self.store.update(body, lan_ip)
                log.info("Bridge: Regel %d geaendert: %s/%s disabled=%s (%s)", rule["id"],
                         rule["destination_port"], rule["protocol"], rule["disabled"], rule["descr"])
                return 200, rule
            if method == "DELETE":
                rule = self.store.delete(body.get("id", (query.get("id") or [None])[0]))
                log.info("Bridge: Regel %d geloescht: %s/%s (%s)", rule["id"],
                         rule["destination_port"], rule["protocol"], rule["descr"])
                return 200, rule
        if method == "POST" and path == "/api/v2/firewall/apply":
            self.service.wakeup.put(True)
            return 200, {"applied": True}
        raise ApiError(404, "Endpunkt %s %s nicht unterstuetzt" % (method, path))
