"""One Game Shop per port: claiming the port, asking an older copy to quit, loopback-only quit."""
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

from fastapi.testclient import TestClient

from gameshop import single
from gameshop.server import Agent, create_app
from tests.fakeps5 import free_port


def test_port_free_sees_a_bound_socket():
    port = free_port()
    assert single.port_free("127.0.0.1", port)
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))
        s.listen(1)
        assert not single.port_free("127.0.0.1", port)
    assert single.port_free("127.0.0.1", port)


def test_claim_reports_a_foreign_program(tmp_path):
    port = free_port()
    with socket.socket() as s:
        s.bind(("127.0.0.1", port))
        s.listen(1)
        c = single.claim_port("127.0.0.1", port, wait=1.0, log=lambda *_: None)
    assert not c.ok and "another program" in c.message and not c.replaced


class _OldCopy(BaseHTTPRequestHandler):
    """Looks like an older Game Shop: answers /api/state, and closes its port when told to quit."""
    quit_asked = False

    def do_GET(self):
        body = b'{"version": "0.0.1", "systems": []}'
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        _OldCopy.quit_asked = True
        self.send_response(200); self.send_header("Content-Length", "2"); self.end_headers(); self.wfile.write(b"{}")
        threading.Timer(0.3, lambda: (self.server.shutdown(), self.server.server_close())).start()

    def log_message(self, *a):
        pass


def test_claim_asks_an_old_copy_to_quit_and_waits():
    port = free_port()
    srv = HTTPServer(("127.0.0.1", port), _OldCopy)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        c = single.claim_port("127.0.0.1", port, wait=10.0, log=lambda *_: None)
    finally:
        srv.server_close()
    assert c.ok and c.replaced == "0.0.1" and _OldCopy.quit_asked


def test_quit_endpoint_refuses_the_lan(tmp_path):
    agent = Agent(tmp_path / "data")
    agent.store.config.discovery = False
    c = TestClient(create_app(agent))               # the test client is not a loopback peer
    assert c.post("/api/quit").status_code == 403
