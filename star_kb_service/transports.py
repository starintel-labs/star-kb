"""Loopback-only initial listeners. Remote TLS/CURVE termination is a later gate."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import zmq
from .contracts import MAX_BYTES, PROTOCOL, Fault, decode, encode, closed


def error(code):
    return {"protocol": PROTOCOL, "id": None, "state": "failed", "error": code}


def http_server(service, port=0):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # never log bearer credentials or private payloads
        def setup(self):
            super().setup()
            self.connection.settimeout(2)
        def do_POST(self):
            try:
                if self.path != "/v1/kb" or self.headers.get("Transfer-Encoding"):
                    raise Fault("invalid_request")
                lengths = self.headers.get_all("Content-Length", [])
                auth = self.headers.get_all("Authorization", [])
                if len(lengths) != 1 or len(auth) != 1 or not auth[0].startswith("Bearer "):
                    raise Fault("unauthorized")
                length = int(lengths[0])
                if not 0 < length <= MAX_BYTES:
                    raise Fault("request_limit")
                data = self.rfile.read(length)
                if len(data) != length:
                    raise Fault("invalid_request")
                reply = service.handle(auth[0][7:], data)
            except Fault as exc:
                reply = error(exc.code)
            except (ValueError, OSError):
                reply = error("invalid_request")
            data = encode(reply)
            self.send_response(200 if "error" not in reply else 400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(data)
            except OSError:
                pass
    return HTTPServer(("127.0.0.1", port), Handler)


class ZmqServer:
    def __init__(self, service):
        self.service = service
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        if not self.ready.wait(5):
            raise RuntimeError("ZMQ startup failed")

    def run(self):
        with zmq.Context() as context:
            with context.socket(zmq.REP) as socket:
                socket.setsockopt(zmq.LINGER, 0)
                socket.setsockopt(zmq.RCVHWM, 16)
                socket.setsockopt(zmq.SNDHWM, 16)
                socket.setsockopt(zmq.SNDTIMEO, 1000)
                socket.setsockopt(zmq.MAXMSGSIZE, MAX_BYTES)
                self.port = socket.bind_to_random_port("tcp://127.0.0.1")
                self.ready.set()
                while not self.stop.is_set():
                    if not socket.poll(100):
                        continue
                    # Single-frame protocol. Multipart is drained with constant
                    # memory and rejected rather than joined into an unbounded buffer.
                    payload = socket.recv()
                    multipart = socket.getsockopt(zmq.RCVMORE)
                    while socket.getsockopt(zmq.RCVMORE):
                        socket.recv()
                    try:
                        if multipart:
                            raise Fault("invalid_request")
                        envelope = closed(decode(payload), ("authorization", "request"))
                        reply = self.service.handle(envelope["authorization"], encode(envelope["request"]))
                    except (Fault, ValueError, UnicodeError, RecursionError) as exc:
                        reply = error(exc.code if isinstance(exc, Fault) else "invalid_request")
                    try:
                        socket.send(encode(reply))
                    except zmq.Again:
                        break

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)
