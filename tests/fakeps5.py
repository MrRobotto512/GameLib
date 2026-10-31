"""A fake PS5 for tests: an anonymous FTP server (pyftpdlib) and a DPI v2 HTTP stub on localhost."""
from __future__ import annotations

import http.server
import socket
import threading
from pathlib import Path

from pyftpdlib.authorizers import DummyAuthorizer
from pyftpdlib.handlers import FTPHandler
from pyftpdlib.servers import ThreadedFTPServer


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeFTP:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        auth = DummyAuthorizer()
        auth.add_anonymous(str(root), perm="elradfmwMT")
        handler = FTPHandler
        handler.authorizer = auth
        handler.banner = "etaHEN fake"
        self.port = free_port()
        self.server = ThreadedFTPServer(("127.0.0.1", self.port), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={"blocking": True}, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.server.close_all()


class FakeDPI:
    """Records the URLs posted to /upload and answers like etaHEN's DPI v2."""
    def __init__(self):
        self.urls: list[str] = []
        self.fetch = True        # when True, GETs the url itself like the console would
        self.fetched: list[int] = []
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):  # quiet
                pass

            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"<html>etaHEN DPI v2</html>")

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(n).decode()
                url = ""
                for part in body.split("--"):
                    if 'name="url"' in part:
                        url = part.split("\r\n\r\n", 1)[1].strip()
                outer.urls.append(url)
                if outer.fetch and url:
                    import urllib.request
                    with urllib.request.urlopen(url, timeout=5) as r:
                        outer.fetched.append(len(r.read()))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"SUCCESS: queued")

        self.port = free_port()
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), H)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *a):
        self.server.shutdown()
