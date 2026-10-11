"""Fixed synthetic baseline; no optimization or remote/cluster performance claim."""
import json
import os
from pathlib import Path
import resource
import statistics
import sys
import time
import threading
import urllib.request
import zmq
from importlib.metadata import version
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from star_kb_service.contracts import PROTOCOL, encode
from star_kb_service.engine import Engine
from star_kb_service.projection import Snapshot
from star_kb_service.runtime import Principal, Service
from star_kb_service.transports import http_server, ZmqServer

root = os.environ["STARLANG_ROOT"]
docs = [{"id": f"r{i:03}", "rev": "1", "dtype": "relation", "dataset": "benchmark",
         "schemaVersion": "0.10.1", "source": {"schema": "entity", "id": "a"},
         "destination": {"schema": "entity", "id": f"b{i}"}, "predicate": "owns"} for i in range(32)]
start = time.perf_counter()
snapshot = Snapshot(root, "benchmark", "benchmark", docs)
projection_ms = (time.perf_counter() - start) * 1000
engine = Engine(root, os.environ["STAR_KB_SBCL"], os.environ["STARLANG_SWI_EXECUTABLE"], snapshot)
start = time.perf_counter()
service = Service({"x" * 32: Principal("bench", "benchmark", "benchmark")}, {("benchmark", "benchmark"): engine})
cold_ms = (time.perf_counter() - start) * 1000
latencies = []
transport_latencies = {"http": [], "zmq": []}
http = http_server(service)
http_thread = threading.Thread(target=http.serve_forever, daemon=True)
http_thread.start()
zmq_server = ZmqServer(service)
try:
    for i in range(20):
        start = time.perf_counter()
        req = {"protocol": PROTOCOL, "id": f"q{i}", "operation": "relation-neighbors",
               "generation": snapshot.generation, "parameters": {"node": "a", "predicate": "owns", "direction": "out"}}
        pending = service.handle("x" * 32, encode(req))
        result = service.handle("x" * 32, encode({"protocol": PROTOCOL, "id": f"n{i}",
                               "operation": "next", "handle": pending["handle"]}))
        assert result["state"] == "completed" and len(result["answers"]) == 32, result
        latencies.append((time.perf_counter() - start) * 1000)
    for transport in ("http", "zmq"):
        for i in range(5):
            start = time.perf_counter()
            def call(request):
                if transport == "http":
                    with urllib.request.urlopen(urllib.request.Request(
                        f"http://127.0.0.1:{http.server_port}/v1/kb", data=encode(request),
                        headers={"Authorization": "Bearer " + "x" * 32}), timeout=10) as response:
                        return json.load(response)
                with zmq.Context() as ctx, ctx.socket(zmq.REQ) as socket:
                    socket.setsockopt(zmq.LINGER, 0)
                    socket.setsockopt(zmq.RCVTIMEO, 10000)
                    socket.connect(f"tcp://127.0.0.1:{zmq_server.port}")
                    socket.send(encode({"authorization": "x" * 32, "request": request}))
                    return json.loads(socket.recv())
            pending = call({"protocol": PROTOCOL, "id": f"{transport}q{i}",
                            "operation": "relation-neighbors", "generation": snapshot.generation,
                            "parameters": {"node": "a", "predicate": "owns", "direction": "out"}})
            result = call({"protocol": PROTOCOL, "id": f"{transport}n{i}", "operation": "next",
                           "handle": pending["handle"]})
            assert result["state"] == "completed" and len(result["answers"]) == 32, result
            transport_latencies[transport].append((time.perf_counter() - start) * 1000)
finally:
    http.shutdown()
    http.server_close()
    http_thread.join()
    zmq_server.close()
    service.close()
print(json.dumps({"workload": "32 directed synthetic canonical relations; 20 direct + 5 HTTP + 5 ZMQ sequential query+next pairs (fresh clients)",
    "projectionMs": projection_ms, "coldWorkerMs": cold_ms, "warmMedianMs": statistics.median(latencies),
    "warmMaxMs": max(latencies), "warmQueriesPerSecond": 20000 / sum(latencies),
    "loopbackMedianMs": {k: statistics.median(v) for k, v in transport_latencies.items()},
    "engine": engine.metadata, "python": sys.version.split()[0],
    "dependencies": {name: version(name) for name in ("pyzmq", "jsonschema")},
    "pythonMaxRssKiB": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    "childMaxRssKiB": resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
    "limits": {"facts": 256, "answers": 128, "requestBytes": 262144, "outputBytes": 65536},
    "adapterCommit": "bfba04a15f43215cd323e1dde2d98a7343abb3ea",
    "note": "Linux RSS high-water marks, not aggregate cluster memory. Fresh-client loopback RPC only; no remote or cluster claim."}, indent=2))
