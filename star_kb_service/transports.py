"""Loopback-only initial listeners. Remote TLS/CURVE termination is a later gate."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import threading
import io
import logging
import time
import zmq
from zmq.auth.thread import ThreadAuthenticator
from .contracts import MAX_BYTES, PROTOCOL, Fault, decode, encode, closed


def error(code):
    return {"protocol": PROTOCOL, "id": None, "state": "failed", "error": code}


class DeadlineReader(io.RawIOBase):
    """One absolute connection-read budget, including headers and body.

    A progress/idle timeout alone is not a slow-client resource bound.
    BufferedReader refills pass here, so every recv uses the remaining budget.
    """
    def __init__(self, connection, seconds=2):
        self.connection = connection
        self.deadline = time.monotonic() + seconds
        self.remaining_bytes = MAX_BYTES + 65536
    def readable(self):
        return True
    def readinto(self, buffer):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or self.remaining_bytes <= 0:
            raise TimeoutError("request read budget exhausted")
        self.connection.settimeout(remaining)
        count = self.connection.recv_into(memoryview(buffer)[:self.remaining_bytes])
        self.remaining_bytes -= count
        return count


def http_server(service, port=0):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # never log bearer credentials or private payloads
        def setup(self):
            super().setup()
            self.rfile.close()
            self.rfile = io.BufferedReader(DeadlineReader(self.connection))
            self.connection.settimeout(2)
        def do_POST(self):
            try:
                if self.path != "/v1/kb" or self.headers.get("Transfer-Encoding"):
                    raise Fault("invalid_request")
                lengths = self.headers.get_all("Content-Length", [])
                auth = self.headers.get_all("Authorization", [])
                if len(lengths) != 1 or len(auth) != 1 or not auth[0].startswith("Bearer "):
                    raise Fault("unauthorized")
                # Reject invalid credentials before waiting on any body bytes.
                service.authenticate(auth[0][7:])
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
            self.connection.settimeout(.5)
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
    """Authenticate at ZMTP/PLAIN before application frames enter the queue.

    PLAIN is loopback-only here, not encryption. Remote transport still requires
    CURVE/TLS. Single-frame is a hard protocol bound: multipart retires the socket
    instead of draining attacker-controlled frames. libzmq authenticates peers
    before their messages; its per-frame limit is not an aggregate memory limit
    for malicious *authenticated* peers. Process-level native memory isolation
    remains necessary before hostile multi-tenant remote exposure.
    """
    def __init__(self, service):
        self.service = service
        self.passwords = {p.identity: token for token, p in service.credentials.items()}
        if any(not 1 <= len(v.encode()) <= 255 for pair in self.passwords.items() for v in pair):
            raise ValueError("ZMQ PLAIN identities and secrets must be 1..255 UTF-8 bytes")
        self.stop = threading.Event()
        self.ready = threading.Event()
        self.failure = None
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        if not self.ready.wait(5) or self.failure:
            self.close()
            raise RuntimeError("ZMQ startup failed")

    def listener(self, context):
        listener = context.socket(zmq.REP)
        listener.setsockopt(zmq.LINGER, 0)
        listener.setsockopt(zmq.RCVHWM, 16)
        listener.setsockopt(zmq.SNDHWM, 16)
        listener.setsockopt(zmq.SNDTIMEO, 500)
        listener.setsockopt(zmq.MAXMSGSIZE, MAX_BYTES)
        listener.setsockopt(zmq.HANDSHAKE_IVL, 1000)
        listener.plain_server = True
        listener.zap_domain = b"star-kb"
        try:
            if not hasattr(self, "port"):
                self.port = listener.bind_to_random_port("tcp://127.0.0.1")
            else:
                deadline = time.monotonic() + 1
                while True:
                    try:
                        listener.bind(f"tcp://127.0.0.1:{self.port}")
                        break
                    except zmq.ZMQError as exc:
                        if exc.errno != zmq.EADDRINUSE or time.monotonic() >= deadline or self.stop.wait(.01):
                            raise
            return listener
        except BaseException:
            listener.close()
            raise

    def run(self):
        # Third-party ZAP debug logs may contain PLAIN passwords. Disable this
        # dedicated logger regardless of application/global log level.
        logger = logging.getLogger("star-kb.zap")
        logger.disabled = True
        try:
            with zmq.Context() as context:
                auth = ThreadAuthenticator(context, log=logger)
                listener = None
                try:
                    auth.start()
                    auth.configure_plain(domain="star-kb", passwords=self.passwords)
                    listener = self.listener(context)
                    self.ready.set()
                    while not self.stop.is_set():
                        if not listener.poll(100):
                            continue
                        frame = listener.recv(copy=False)
                        # Maximum accepted aggregate: one frame and MAX_BYTES.
                        # Never drain additional frames, even to produce an error.
                        if listener.getsockopt(zmq.RCVMORE):
                            listener.close()
                            listener = self.listener(context)
                            continue
                        try:
                            envelope = closed(decode(bytes(frame)), ("authorization", "request"))
                            principal = self.service.authenticate(envelope["authorization"])
                            if frame.get("User-Id") != principal.identity:
                                raise Fault("unauthorized")
                            reply = self.service.handle(envelope["authorization"], encode(envelope["request"]))
                        except (Fault, ValueError, UnicodeError, RecursionError, zmq.ZMQError) as exc:
                            reply = error(exc.code if isinstance(exc, Fault) else "invalid_request")
                        try:
                            listener.send(encode(reply))
                        except zmq.Again:
                            listener.close()
                            listener = self.listener(context)
                finally:
                    if listener is not None:
                        listener.close()
                    auth.stop()
        except Exception as exc:
            self.failure = type(exc).__name__
            self.ready.set()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)
        if self.thread.is_alive():
            raise RuntimeError("ZMQ shutdown deadline exceeded")
