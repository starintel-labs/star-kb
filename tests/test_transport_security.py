"""Bounded loopback ingress regression fixtures, separate from real SWI tests."""
import importlib.util
import json
import socket
import threading
import time
import unittest

AVAILABLE = importlib.util.find_spec("zmq") is not None
if AVAILABLE:
    import zmq
    from star_kb_service.contracts import Fault, encode, PROTOCOL
    from star_kb_service.runtime import Principal
    from star_kb_service.transports import http_server, ZmqServer


class IngressFixture:
    """Ingress-only fixture: never counted as a Prolog integration proof."""
    token = "s" * 32
    def __init__(self):
        self.credentials = {self.token: Principal("ingress-test", "a", "d")}
        self.calls = []
    def authenticate(self, token):
        if token not in self.credentials:
            raise Fault("unauthorized")
        return self.credentials[token]
    def handle(self, token, data):
        self.authenticate(token)
        self.calls.append(data)
        return {"protocol": PROTOCOL, "state": "ready"}


@unittest.skipUnless(AVAILABLE, "pyzmq absent; mandatory service CI installs it")
class HttpIngressSecurityTests(unittest.TestCase):
    def setUp(self):
        self.service = IngressFixture()
        self.server = http_server(self.service)
        self.accepted = threading.Event()
        accept = self.server.get_request
        def observed_accept():
            result = accept()
            self.accepted.set()
            return result
        self.server.get_request = observed_accept
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.peer = socket.create_connection(self.server.server_address, timeout=1)
    def tearDown(self):
        self.peer.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.assertFalse(self.thread.is_alive())
    def test_invalid_bearer_rejected_without_waiting_for_body(self):
        self.peer.sendall(b"POST /v1/kb HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer invalid\r\nContent-Length: 100\r\n\r\n")
        self.peer.settimeout(.5)
        self.assertIn(b"400", self.peer.recv(4096))
        self.assertEqual([], self.service.calls)
    def test_progress_cannot_extend_absolute_body_deadline_or_shutdown(self):
        self.peer.sendall(("POST /v1/kb HTTP/1.1\r\nHost: localhost\r\nAuthorization: Bearer " + self.service.token + "\r\nContent-Length: 100\r\n\r\n").encode())
        stop = threading.Event()
        def trickle():
            while not stop.is_set():
                try:
                    self.peer.sendall(b" ")
                except OSError:
                    break
                stop.wait(.1)
        sender = threading.Thread(target=trickle, daemon=True)
        sender.start()
        # shutdown must complete even while a valid peer makes steady progress.
        shutdown = threading.Thread(target=self.server.shutdown, daemon=True)
        started = time.monotonic()
        self.assertTrue(self.accepted.wait(1), "fixture connection was not accepted")
        shutdown.start()
        shutdown.join(timeout=2.8)
        finished = not shutdown.is_alive()
        stop.set()
        self.peer.close()
        sender.join(timeout=1)
        shutdown.join(timeout=3)
        self.assertTrue(finished, "trickled bytes extended the absolute read/shutdown bound")
        self.assertLess(time.monotonic() - started, 3.0)
        self.assertEqual([], self.service.calls)


    def test_slow_headers_share_the_absolute_connection_budget(self):
        self.peer.sendall(b"POST /v1/kb HTTP/1.1\r\nHost: ")
        self.assertTrue(self.accepted.wait(1))
        stop = threading.Event()
        def trickle():
            while not stop.wait(.1):
                try:
                    self.peer.sendall(b"x")
                except OSError:
                    break
        sender = threading.Thread(target=trickle, daemon=True)
        sender.start()
        shutdown = threading.Thread(target=self.server.shutdown, daemon=True)
        shutdown.start()
        shutdown.join(2.8)
        finished = not shutdown.is_alive()
        stop.set()
        self.peer.close()
        sender.join(1)
        shutdown.join(3)
        self.assertTrue(finished, "slow headers exceeded the absolute ingress budget")
        self.assertEqual([], self.service.calls)

@unittest.skipUnless(AVAILABLE, "pyzmq absent; mandatory service CI installs it")
class ZmqIngressSecurityTests(unittest.TestCase):
    def setUp(self):
        self.service = IngressFixture()
        self.server = ZmqServer(self.service)
        self.context = zmq.Context()
    def tearDown(self):
        self.context.destroy(linger=0)
        self.server.close()
        self.assertFalse(self.server.thread.is_alive())
    def peer(self, authenticated=True):
        peer = self.context.socket(zmq.REQ)
        peer.setsockopt(zmq.LINGER, 0)
        peer.setsockopt(zmq.RCVTIMEO, 250)
        peer.setsockopt(zmq.SNDTIMEO, 500)
        if authenticated:
            peer.plain_username = b"ingress-test"
            peer.plain_password = self.service.token.encode()
        peer.connect(f"tcp://127.0.0.1:{self.server.port}")
        return peer
    def payload(self):
        return encode({"authorization": self.service.token,
                       "request": {"protocol": PROTOCOL, "id": "probe", "operation": "health"}})
    def probe(self):
        # A new peer must work after a bounded offending message retires REP.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with self.peer() as peer:
                peer.send(self.payload())
                try:
                    self.assertEqual("ready", json.loads(peer.recv())["state"])
                    return
                except zmq.Again:
                    pass
        self.fail("listener did not recover within bounded probe window")
    def test_application_token_does_not_replace_transport_authentication(self):
        with self.peer(authenticated=False) as peer:
            peer.send(self.payload())
            with self.assertRaises(zmq.Again):
                peer.recv()
        self.assertEqual([], self.service.calls)
        self.probe()
    def test_multipart_count_and_aggregate_bytes_rejected_without_dispatch(self):
        # Fixtures are capped at 264 KiB/32 frames; no unbounded flood.
        for frames in ([b"x"] * 32, [b"x" * 131088] * 2):
            before = len(self.service.calls)
            with self.peer() as peer:
                peer.send_multipart(frames)
                with self.assertRaises(zmq.Again):
                    peer.recv()
            self.assertEqual(before, len(self.service.calls))
            self.probe()
    def test_transport_identity_cannot_substitute_another_application_principal(self):
        # Fixture returns a foreign identity to model mismatched app credentials.
        self.service.credentials["t" * 32] = Principal("foreign", "b", "d")
        with self.peer() as peer:
            # Handshake remains ingress-test; application token is foreign.
            peer.send(encode({"authorization": "t" * 32, "request": {}}))
            self.assertEqual("unauthorized", json.loads(peer.recv())["error"])
        self.assertEqual([], self.service.calls)
