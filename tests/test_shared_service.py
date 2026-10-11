"""Real SBCL -> StarLang adapter -> SWI integration and HTTP/ZMQ parity.

Required CI lane sets STARLANG_ROOT, STAR_KB_SBCL, STARLANG_SWI_EXECUTABLE.
No fake evaluator is used when these tests run.
"""
import copy
import json
import os
import signal
import threading
import time
import unittest
import urllib.request
import urllib.error

ENABLED = all(os.environ.get(k) for k in ("STARLANG_ROOT", "STAR_KB_SBCL", "STARLANG_SWI_EXECUTABLE"))
if ENABLED:
    import zmq
    from star_kb_service.contracts import PROTOCOL, Fault, encode
    from star_kb_service.engine import Engine
    from star_kb_service.projection import Snapshot
    from star_kb_service.runtime import Principal, Service
    from star_kb_service.transports import http_server, ZmqServer


def document(identifier="r1", destination="b"):
    return {"id": identifier, "rev": "1-fixture", "dtype": "relation", "dataset": "d",
            "schemaVersion": "0.10.1", "source": {"schema": "entity", "id": "a"},
            "destination": {"schema": "entity", "id": destination}, "predicate": "owns",
            "direction": "directed", "sources": [{"schema": "source", "id": "s1"}],
            "evidence": [{"schema": "evidence-record", "id": "e1"}],
            "provenance": {"collector": "fixture"}, "extensions": {"future": [1, "雪"]}}


@unittest.skipUnless(ENABLED, "real service runtime not configured (mandatory dedicated CI lane)")
class SharedServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = os.environ["STARLANG_ROOT"]
        cls.tokens = ("a" * 32, "b" * 32)

    def setUp(self):
        self.engines = {}
        for tenant, dest in (("a", "b"), ("b", "private-b")):
            snap = Snapshot(self.root, tenant, "d", [document(destination=dest)])
            self.engines[(tenant, "d")] = Engine(self.root, os.environ["STAR_KB_SBCL"],
                                                os.environ["STARLANG_SWI_EXECUTABLE"], snap)
        self.service = Service({self.tokens[0]: Principal("alice", "a", "d"),
                                self.tokens[1]: Principal("bob", "b", "d")}, self.engines)
        self.http = http_server(self.service)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.zmq = ZmqServer(self.service)
        self.counter = 0

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()
        self.zmq.close()
        self.service.close()

    def request(self, op, **fields):
        self.counter += 1
        return {"protocol": PROTOCOL, "id": str(self.counter), "operation": op, **fields}

    def call(self, req, transport="http", token=None):
        token = token or self.tokens[0]
        if transport == "http":
            request = urllib.request.Request(f"http://127.0.0.1:{self.http.server_port}/v1/kb",
                        data=encode(req), headers={"Authorization": "Bearer " + token})
            try:
                response = urllib.request.urlopen(request, timeout=60)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                return json.load(response)
        with zmq.Context() as ctx, ctx.socket(zmq.REQ) as sock:
            sock.setsockopt(zmq.LINGER, 0)
            sock.setsockopt(zmq.RCVTIMEO, 60000)
            sock.connect(f"tcp://127.0.0.1:{self.zmq.port}")
            sock.send(encode({"authorization": token, "request": req}))
            return json.loads(sock.recv())

    def query(self, node="a", **fields):
        return self.request("relation-neighbors", generation=self.engines[("a", "d")].snapshot.generation,
                 parameters={"node": node, "predicate": "owns", "direction": "out"}, **fields)

    def test_http_zmq_real_query_and_provenance_parity(self):
        req = self.query()
        first = self.call(req)
        self.assertEqual(first, self.call(req, "zmq"))
        self.assertEqual("pending", first["state"])
        next_req = self.request("next", handle=first["handle"])
        result = self.call(next_req, "zmq")
        self.assertEqual(result, self.call(next_req))
        self.assertEqual("completed", result["state"])
        self.assertEqual("b", result["answers"][0]["destination"])
        self.assertEqual(document()["evidence"], result["answers"][0]["provenance"]["evidence"])
        self.assertEqual("1-fixture", result["answers"][0]["provenance"]["revision"])
        self.assertTrue(result["engineBuild"].startswith("sha256:"))
        self.assertTrue(result["ruleDigest"].startswith("sha256:"))
        cancel = self.call(self.request("cancel", handle=first["handle"]))
        self.assertEqual("completed", cancel["state"])

    def test_auth_scope_generation_replay_and_closed_arguments(self):
        req = self.query()
        self.assertEqual("unauthorized", self.call(req, token="wrong")["error"])
        self.assertEqual("unauthorized", self.call(req, "zmq", token="wrong")["error"])
        self.assertEqual("stale_generation", self.call(req, token=self.tokens[1])["error"])
        self.assertEqual("pending", self.call(req)["state"])
        changed = copy.deepcopy(req)
        changed["parameters"]["node"] = "b"
        self.assertEqual("replay_conflict", self.call(changed)["error"])
        foreign = self.call(self.request("next", handle=req["id"]), token=self.tokens[1])
        self.assertEqual("unknown_operation", foreign["error"])
        for extra in ({"tenant": "b"}, {"goal": "halt"}, {"file": "/etc/passwd"}):
            altered = self.query()
            altered.update(extra)
            self.assertEqual("invalid_request", self.call(altered)["error"])

    def test_no_answer_cancel_deadline_and_limits(self):
        pending = self.call(self.query(node="absent"))
        result = self.call(self.request("next", handle=pending["handle"]))
        self.assertEqual("no-answer", result["state"])
        self.assertEqual([], result["answers"])
        pending = self.call(self.query())
        self.assertEqual("cancelled", self.call(self.request("cancel", handle=pending["handle"]))["state"])
        self.assertEqual("cancelled", self.call(self.request("next", handle=pending["handle"]))["state"])
        pending = self.call(self.query(timeoutMs=1))
        time.sleep(.005)
        self.assertEqual("deadline_exceeded", self.call(self.request("next", handle=pending["handle"]))["error"])
        pending = self.call(self.query(limits={"output-bytes": 1}))
        self.assertEqual("query_limit", self.call(self.request("next", handle=pending["handle"]))["error"])
        pending = self.call(self.query())
        self.assertEqual("completed", self.call(self.request("next", handle=pending["handle"]))["state"])

    def test_crash_retirement_and_trusted_snapshot_replay(self):
        engine = self.engines[("a", "d")]
        old = engine.process
        os.kill(old.pid, signal.SIGKILL)
        old.wait()
        self.assertEqual("degraded", self.call(self.request("health"))["state"])
        pending = self.call(self.query())
        failed = self.call(self.request("next", handle=pending["handle"]))
        self.assertEqual("engine_failure", failed["error"])
        pending = self.call(self.query())
        result = self.call(self.request("next", handle=pending["handle"]))
        self.assertEqual("completed", result["state"])
        self.assertEqual("r1", result["answers"][0]["id"])
        self.assertNotEqual(old.pid, engine.process.pid)
        self.assertEqual([], Engine.session_pids(old.pid))

    def test_real_stopped_worker_deadline_cleanup(self):
        engine = self.engines[("a", "d")]
        old = engine.process
        pending = self.call(self.query(timeoutMs=100))
        os.killpg(old.pid, signal.SIGSTOP)
        try:
            reply = self.call(self.request("next", handle=pending["handle"]))
            self.assertEqual("deadline_exceeded", reply["error"])
            self.assertIsNotNone(old.poll())
            self.assertEqual([], Engine.session_pids(old.pid))
        finally:
            if old.poll() is None:
                os.killpg(old.pid, signal.SIGKILL)
                old.wait()

    def test_isolation_and_concurrent_admission(self):
        req = self.query()
        req["generation"] = self.engines[("b", "d")].snapshot.generation
        pending = self.call(req, "zmq", self.tokens[1])
        result = self.call(self.request("next", handle=pending["handle"]), "http", self.tokens[1])
        self.assertEqual("private-b", result["answers"][0]["destination"])
        # Lock held by an actual stopped worker query, not a mocked engine.
        engine = self.engines[("a", "d")]
        pending = self.call(self.query(timeoutMs=200))
        os.killpg(engine.process.pid, signal.SIGSTOP)
        results = []
        thread = threading.Thread(target=lambda: results.append(self.call(self.request("next", handle=pending["handle"]))))
        thread.start()
        deadline = time.monotonic() + 1
        while not self.service.lock.locked() and time.monotonic() < deadline:
            time.sleep(.001)
        reply = self.call(self.request("health"), "zmq")
        self.assertEqual("busy", reply["error"])
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual("deadline_exceeded", results[0]["error"])

    def test_canonical_flat_boundary_unknown_fields_and_owned_snapshot(self):
        doc = document()
        snapshot = Snapshot(self.root, "a", "d", [doc])
        doc["extensions"]["future"].append("mutated")
        facts = snapshot.lossless_facts("r1")
        self.assertTrue(any(f.args[1] == "/extensions/future/1" and f.args[-1] == "雪" for f in facts))
        self.assertFalse(any("mutated" in f.args for f in facts))
        self.assertEqual(snapshot.generation, Snapshot(self.root, "a", "d", [document()]).generation)
        for bad in ({"_id": "old", "data": {}}, {**document(), "direction": "symmetric"},
                    {**document(), "schemaVersion": "0.9.0"}, {**document(), "rev": ""}):
            with self.assertRaises(Fault):
                Snapshot(self.root, "a", "d", [bad])

    def test_shutdown_and_malformed_wire(self):
        reply = self.service.handle(self.tokens[0], b'{"id":"a","id":"b"}')
        self.assertEqual("invalid_request", reply["error"])
        pending = self.call(self.query())
        processes = [e.process for e in self.engines.values()]
        self.service.close()
        self.assertTrue(all(p.poll() is not None for p in processes))
        self.assertTrue(all(not Engine.session_pids(p.pid) for p in processes))
        self.assertEqual("service_closed", self.call(self.request("next", handle=pending["handle"]))["error"])

    def test_answer_limit_and_prolog_looking_unicode_remains_data(self):
        engine = self.engines[("a", "d")]
        engine.close()
        node = "雪\"),halt.\n"
        docs = [document("r1"), document("r2")]
        for doc in docs:
            doc["source"]["id"] = node
        engine.snapshot = Snapshot(self.root, "a", "d", docs)
        engine.start()
        pending = self.call(self.query(node=node, limits={"answers": 1}))
        reply = self.call(self.request("next", handle=pending["handle"]))
        self.assertEqual("query_limit", reply["error"])
        self.assertNotIn("answers", reply)
        pending = self.call(self.query(node=node))
        reply = self.call(self.request("next", handle=pending["handle"]))
        self.assertEqual("completed", reply["state"])
        self.assertEqual([node, node], [r["source"] for r in reply["answers"]])
        self.assertIsNone(engine.process.poll())

    def test_replay_and_operation_memory_budgets(self):
        for i in range(32):
            pending = self.call(self.query())
            self.assertEqual("pending", pending["state"])
        self.assertEqual("operation_budget", self.call(self.query())["error"])
        for i in range(128 - len(self.service.replay)):
            self.assertEqual("ready", self.call(self.request("health"))["state"])
        self.assertEqual("request_budget", self.call(self.request("health"))["error"])
