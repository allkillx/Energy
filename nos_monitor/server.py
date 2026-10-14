"""Minimal HTTP server: static page + JSON API + background poller (standard library only)."""

from __future__ import annotations

import json
import mimetypes
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .service import Monitor

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")


def make_handler(monitor: Monitor, web_dir: str = WEB_DIR, demo: bool = False):
    web_root = os.path.realpath(web_dir)

    class Handler(BaseHTTPRequestHandler):
        server_version = "nos-monitor"

        def log_message(self, fmt, *args):  # quieter console
            if not self.path.startswith("/api/state"):
                super().log_message(fmt, *args)

        def _json(self, payload, status=200):
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlparse(self.path).path
            if path == "/api/state":
                state = monitor.state()
                state["demo"] = demo
                return self._json(state)
            if path == "/api/health":
                return self._json({"ok": True})
            if path == "/":
                path = "/index.html"
            full = os.path.realpath(os.path.join(web_root, path.lstrip("/")))
            if not full.startswith(web_root + os.sep) or not os.path.isfile(full):
                return self._json({"error": "not found"}, 404)
            ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
            with open(full, "rb") as fh:
                body = fh.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype.endswith("javascript") else ""))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/refresh":
                force = "force=1" in (urlparse(self.path).query or "")
                result = monitor.refresh(force=force)
                state = monitor.state()
                state["demo"] = demo
                state["refresh_result"] = result
                return self._json(state)
            if path == "/api/reload-locations":
                monitor.locations.reload()
                return self._json({"ok": True, "problems": monitor.locations.problems})
            return self._json({"error": "not found"}, 404)

    return Handler


def start_poller(monitor: Monitor, interval: float, stop: threading.Event) -> threading.Thread:
    def loop():
        while not stop.is_set():
            monitor.refresh(force=True)
            stop.wait(interval)

    t = threading.Thread(target=loop, name="nos-poller", daemon=True)
    t.start()
    return t


def serve(monitor: Monitor, host: str, port: int, poll_seconds: float | None, demo: bool = False):
    httpd = ThreadingHTTPServer((host, port), make_handler(monitor, demo=demo))
    stop = threading.Event()
    if poll_seconds:
        start_poller(monitor, poll_seconds, stop)
    print(f"NOS monitor: http://{host}:{httpd.server_address[1]}/  (poll every {poll_seconds or 0:.0f}s)")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()
