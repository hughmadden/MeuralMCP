#!/usr/bin/env python3
"""Local replacement for the Meural cloud API.

Serves the three endpoints a Canvas frame uses, plus media, from local files.
No upstream dependency: every response is ours.

Config (INI, default /etc/meural-api/meural.conf):
    [server] bind, port, cert, key, data, gallery
"""
import configparser, hashlib, http.server, json, mimetypes, os, re, ssl
import threading, time
from urllib.parse import parse_qs
from urllib.request import urlopen

CONF = os.environ.get("MEURAL_CONF", "/etc/meural-api/meural.conf")
_c = configparser.ConfigParser()
_c.read(CONF)
S = _c["server"]

BIND = S.get("bind", "0.0.0.0")
PORT = S.getint("port", 443)
CERT = S["cert"]
KEY = S["key"]
DATA = S["data"].rstrip("/")
LOG = S.get("log", "/var/log/meural-api/access.log")

BY_ID = f"{DATA}/by-id"
QUEUES = f"{DATA}/queues"
STATUS = f"{DATA}/status"
MEDIA = f"{DATA}/media"
GALLERY = S.get("gallery_portrait", f"{DATA}/gallery.json")
GALLERY_LAND = S.get("gallery_landscape", GALLERY)

ORIENT = {}          # client ip -> orientation
ORIENT_TTL = 600


def log(lines):
    try:
        with open(LOG, "a") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


def orientation(ip):
    """Live orientation of a frame; cached briefly. Stored values go stale."""
    hit = ORIENT.get(ip)
    if hit and time.time() - hit[0] < ORIENT_TTL:
        return hit[1]
    o = ""
    try:
        with urlopen(f"http://{ip}/remote/identify/", timeout=4) as r:
            o = json.load(r).get("response", {}).get("orientation", "")
    except Exception:
        pass
    ORIENT[ip] = (time.time(), o)
    return o


def manifest(gid, ip):
    """Gallery manifest, id echoed and orientation matched to this client."""
    path = f"{BY_ID}/{gid}.json" if gid.isdigit() and os.path.exists(f"{BY_ID}/{gid}.json") else GALLERY
    g = json.load(open(path))
    if gid.isdigit():
        g["id"] = int(gid)          # older firmware will not adopt a different id
    o = orientation(ip)
    if o in ("portrait", "landscape"):
        g["orientation"] = 0 if o == "portrait" else 1
        for it in g.get("items", []):
            it["orientation"] = o
    return json.dumps(g).encode()


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _device(self, body):
        try:
            k = (parse_qs(body.decode("utf-8", "replace")).get("product_key") or [""])[0]
        except Exception:
            k = ""
        return re.sub(r"[^A-Za-z0-9._-]", "", k)[:64]

    def _poll(self, body):
        key = self._device(body)
        served = None
        if key:
            q = os.path.join(QUEUES, key + ".json")
            if not os.path.exists(q):
                # first sight of this frame: send it the current gallery, once
                try:
                    src = GALLERY_LAND if orientation(self.client_address[0]) == "landscape" else GALLERY
                    gid = json.load(open(src))["id"]
                    os.makedirs(QUEUES, exist_ok=True)
                    json.dump([{"type": "command", "command": "get_galleries",
                                "parameter": gid,
                                "body": {"id": gid, "include_items": True,
                                         "bg_download": False}}], open(q, "w"))
                    log([f"  SEED[{key}] -> {gid}"])
                except (OSError, ValueError, KeyError):
                    pass
            try:
                items = json.load(open(q))
                if items:
                    json.dump(items[1:], open(q, "w"))
                    served = items[0]
            except (OSError, ValueError):
                pass
        if served is not None:
            log([f"  SERVE[{key}] {json.dumps(served)[:300]}"])
            self._send(200, json.dumps({"data": [served], "is_ok": True}).encode())
        else:
            self._send(200, b'{"data": [], "is_ok": true}')

    def _status(self, body):
        key = self._device(body) or "unknown"
        try:
            os.makedirs(STATUS, exist_ok=True)
            with open(f"{STATUS}/{key}", "a") as f:
                f.write(time.strftime("%Y-%m-%dT%H:%M:%S ") + body.decode("utf-8", "replace") + "\n")
        except OSError:
            pass
        self._send(200, b'{"is_ok": true}')

    def _handle(self, body=b""):
        path = self.path.split("?")[0]
        log([f"=== {time.strftime('%Y-%m-%dT%H:%M:%S')} {self.client_address[0]} {self.command} {self.path}"])
        if path.startswith("/media/"):
            fp = os.path.join(MEDIA, os.path.basename(path))
            if os.path.isfile(fp):
                with open(fp, "rb") as f:
                    self._send(200, f.read(), mimetypes.guess_type(fp)[0] or "application/octet-stream")
            else:
                self._send(404, b"{}")
        elif path == "/api/v3/get_rabbit_commands":
            self._poll(body)
        elif path == "/api/v3/synchronization":
            self._status(body)
        elif path == "/api/v3/download_gallery":
            gid = ""
            try:
                gid = (parse_qs(body.decode("utf-8", "replace")).get("id") or [""])[0]
            except Exception:
                pass
            try:
                self._send(200, manifest(gid, self.client_address[0]))
            except (OSError, ValueError):
                self._send(500, b'{"is_ok": false}')
        else:
            self._send(200, b'{"is_ok": true}')

    def do_GET(self):
        self._handle()

    def do_POST(self):
        self._handle(self.rfile.read(int(self.headers.get("Content-Length") or 0)))

    def do_PUT(self):
        self._handle(self.rfile.read(int(self.headers.get("Content-Length") or 0)))


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def process_request(self, request, addr):
        # wrap in the worker: a stalled handshake must not stall accept()
        threading.Thread(target=self._serve, args=(request, addr), daemon=True).start()

    def _serve(self, request, addr):
        try:
            request.settimeout(20)
            conn = CTX.wrap_socket(request, server_side=True)
        except Exception:
            try:
                request.close()
            except Exception:
                pass
            return
        try:
            self.finish_request(conn, addr)
        except Exception:
            pass
        finally:
            try:
                self.shutdown_request(conn)
            except Exception:
                pass


CTX = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
CTX.load_cert_chain(CERT, KEY)      # chain file: leaf first
os.makedirs(MEDIA, exist_ok=True)
Server((BIND, PORT), Handler).serve_forever()
