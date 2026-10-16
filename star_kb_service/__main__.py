"""Local service runner. No deployment and no secret values in configuration."""
import argparse
from contextlib import ExitStack
import json
import os
from pathlib import Path
import signal
import threading
from .contracts import closed
from .engine import Engine
from .projection import Snapshot
from .persistence import ServerDocuments
from .proactive import ProactiveEngine
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


def configured_service(config, root):
    """Release earlier workers when a later scope or credential fails startup."""
    with ExitStack() as owned:
        engines = {}
        proactive = {}
        for scope in config["scopes"]:
            closed(scope, ("tenant", "dataset"), ("documents", "server", "proactive"))
            key = scope["tenant"], scope["dataset"]
            if key in engines:
                raise ValueError("duplicate scope")
            if ("documents" in scope) == ("server" in scope):
                raise ValueError("scope requires exactly one snapshot source")
            if "server" in scope:
                store = closed(scope["server"], ("url", "tokenEnv", "documentIds"))
                snapshot = ServerDocuments(root, *key, store["url"], os.environ[store["tokenEnv"]]).snapshot(store["documentIds"])
            else:
                snapshot = Snapshot(root, *key, scope["documents"])
            if "proactive" in scope:
                if "server" not in scope:
                    raise ValueError("proactive goals require canonical server")
                options = closed(scope["proactive"], ("actors",))
                client = ServerDocuments(root, *key, store["url"], os.environ[store["tokenEnv"]],
                                         allowed_types=("relation", "task", "claim", "target"))
                proactive[key] = ProactiveEngine(client,
                    lambda snap: Engine(root, os.environ["STAR_KB_SBCL"], os.environ["STARLANG_SWI_EXECUTABLE"], snap),
                    options["actors"])
            engines[key] = Engine(root, os.environ["STAR_KB_SBCL"], os.environ["STARLANG_SWI_EXECUTABLE"], snapshot)
            owned.callback(engines[key].close)
        credentials = {}
        for item in config["credentials"]:
            closed(item, ("identity", "tenant", "dataset", "tokenEnv"))
            token = os.environ[item["tokenEnv"]]
            if token in credentials:
                raise ValueError("duplicate credential")
            credentials[token] = Principal(item["identity"], item["tenant"], item["dataset"])
        service = Service(credentials, engines, proactive=proactive)
        owned.pop_all()
        return service


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--http-port", type=int, default=8080)
    args = parser.parse_args()
    config = closed(json.loads(args.config.read_bytes()), ("scopes", "credentials"))
    root = os.environ["STARLANG_ROOT"]
    service = configured_service(config, root)
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
