"""Local service runner. No deployment and no secret values in configuration."""
import argparse
import json
import os
from pathlib import Path
import signal
import threading
from .contracts import closed
from .engine import Engine
from .projection import Snapshot
from .runtime import Principal, Service
from .transports import http_server, ZmqServer


def shutdown(service, http=None, thread=None, zmq=None):
    """Attempt every cleanup even when a listener or worker reports failure."""
    service.closed = True
    if zmq is not None:
        zmq.stop.set()
    failures = []
    actions = []
    if http is not None:
        if thread is not None:
            actions.append(http.shutdown)
        actions.append(http.server_close)
    if zmq is not None:
        actions.append(zmq.close)
    actions.append(service.close)
    if thread is not None:
        actions.append(lambda: thread.join(timeout=1))
    for action in actions:
        try:
            action()
        except Exception as exc:
            failures.append(exc)
    if failures:
        raise failures[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--http-port", type=int, default=8080)
    args = parser.parse_args()
    config = closed(json.loads(args.config.read_bytes()), ("scopes", "credentials"))
    root = os.environ["STARLANG_ROOT"]
    engines = {}
    for scope in config["scopes"]:
        closed(scope, ("tenant", "dataset", "documents"))
        key = scope["tenant"], scope["dataset"]
        if key in engines:
            raise ValueError("duplicate scope")
        snapshot = Snapshot(root, *key, scope["documents"])
        engines[key] = Engine(root, os.environ["STAR_KB_SBCL"], os.environ["STARLANG_SWI_EXECUTABLE"], snapshot)
    credentials = {}
    for item in config["credentials"]:
        closed(item, ("identity", "tenant", "dataset", "tokenEnv"))
        token = os.environ[item["tokenEnv"]]
        if token in credentials:
            raise ValueError("duplicate credential")
        credentials[token] = Principal(item["identity"], item["tenant"], item["dataset"])
    service = Service(credentials, engines)
    http = zmq = thread = None
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    try:
        http = http_server(service, args.http_port)
        zmq = ZmqServer(service)
        thread = threading.Thread(target=http.serve_forever, daemon=True)
        thread.start()
        print(json.dumps({"http": f"http://127.0.0.1:{http.server_port}/v1/kb",
                          "zmq": f"tcp://127.0.0.1:{zmq.port}"}), flush=True)
        stopped.wait()
    finally:
        shutdown(service, http, thread, zmq)


if __name__ == "__main__":
    main()
