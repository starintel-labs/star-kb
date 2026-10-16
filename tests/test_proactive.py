"""Proactive policy regressions with real schema validation and a memory store.

The query double is intentionally not evidence of Prolog execution. The store
models create-only replay and lost acknowledgements, not disk durability.
"""
import copy
import json
import os
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

ROOT = os.environ.get("STARLANG_ROOT")
if ROOT:
    from star_kb_service.contracts import Fault, encode
    from star_kb_service.persistence import ServerDocuments
    from star_kb_service.proactive import ProactiveEngine


def relation(identifier, source, destination, **changes):
    return {"id": identifier, "rev": "1-fixture", "dtype": "relation", "dataset": "d",
            "schemaVersion": "0.10.1", "source": {"schema": "organization", "id": source},
            "destination": {"schema": "organization", "id": destination},
            "predicate": "subsidiary-of", "direction": "directed",
            "sources": [{"schema": "source", "id": "synthetic-source"}],
            "evidence": [{"schema": "evidence-record", "id": "synthetic-evidence"}],
            "provenance": {"collector": "synthetic-fixture"}, **changes}


if ROOT:
    class MemoryDocuments(ServerDocuments):
        """Production validators with an explicitly simulated persistence boundary."""
        def __init__(self, documents):
            super().__init__(ROOT, "tenant-a", "d", "http://127.0.0.1:1", "x" * 32,
                             allowed_types=("relation", "task", "claim", "target"))
            self.documents = {d["id"]: copy.deepcopy(d) for d in documents}
            self.created = []
            self.lose_ack_dtype = None
            self.queued = []

        def read(self, identifier, deadline=None, optional=False):
            if identifier not in self.documents:
                if optional:
                    return None
                raise Fault("store_not_found")
            return self.validate(self.documents[identifier], revision=True)

        def create(self, document, seconds=5):
            document = self.validate(document)
            identifier = document["id"]
            if identifier in self.documents:
                actual = {k: v for k, v in self.documents[identifier].items() if k != "rev"}
                if encode(actual) != encode(document):
                    raise Fault("durable_replay_conflict")
            else:
                self.documents[identifier] = {**copy.deepcopy(document), "rev": "1-created"}
                self.created.append(identifier)
                if self.lose_ack_dtype == document["dtype"]:
                    self.lose_ack_dtype = None
                    raise Fault("store_unavailable")
            return self.read(identifier)

        def queue_target(self, document, seconds=5):
            self.validate(document)
            if document["id"] not in self.queued:
                self.queued.append(document["id"])
            options = {**document.get("options", {}), "starKbDerivation": {
                "provenance": document["provenance"], "sources": document["sources"],
                "verificationStatus": "candidate", "logicalId": document["id"]}}
            stored = {"id": "server-target-" + str(self.queued.index(document["id"]) + 1),
                      "dtype": "target", "schemaVersion": "0.10.1", "dataset": self.dataset,
                      "actor": document["actor"], "target": document["target"],
                      "state": "pending", "options": options}
            return self.create(stored, seconds)


class QueryDouble:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.metadata = {"engineBuild": "sha256:fixture", "ruleDigest": "sha256:fixture"}
        self.closed = False

    def start(self, seconds=45):
        pass

    def query(self, parameters, limits, seconds):
        rows = [row for row in self.snapshot.rows
                if row["source"] == parameters["node"]
                and row["predicate"] == parameters["predicate"]]
        return {"state": "completed", "rows": rows}

    def close(self):
        self.closed = True


@unittest.skipUnless(ROOT, "pinned generated schema required")
class ProactivePolicyTests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryDocuments([relation("ab", "a", "b"), relation("bc", "b", "c")])
        self.workers = []

    def factory(self, snapshot):
        worker = QueryDouble(snapshot)
        self.workers.append(worker)
        return worker

    def engine(self, **kwargs):
        return ProactiveEngine(self.store, self.factory, **kwargs)

    def results(self, dtype):
        return [d for d in self.store.documents.values() if d["dtype"] == dtype]

    def test_candidate_claim_has_typed_proof_and_original_provenance(self):
        engine = self.engine()
        self.assertEqual("pending", engine.submit("chain", ["ab", "bc"], "subsidiary-chain/1")["state"])
        done = engine.advance("chain")
        self.assertEqual("completed", done["state"])
        claim, = self.results("claim")
        self.assertEqual([claim["id"]], done["results"])
        self.assertEqual(["a"], claim["subjectRefs"])
        self.assertEqual(["c"], claim["objectRefs"])
        self.assertEqual("indirect-subsidiary-of", claim["predicate"])
        self.assertEqual("candidate", claim["verificationStatus"])
        self.assertEqual("unknown", claim["truthStatus"])
        proof = claim["provenance"]
        self.assertTrue(proof["derived"])
        self.assertEqual("tenant-a", proof["tenant"])
        self.assertEqual("d", claim["dataset"])
        self.assertEqual(["ab", "bc"], [e["documentId"] for e in proof["evidence"]])
        for evidence in proof["evidence"]:
            self.assertEqual("1-fixture", evidence["revision"])
            self.assertEqual("organization", evidence["source"]["schema"])
            self.assertEqual([{"schema": "evidence-record", "id": "synthetic-evidence"}], evidence["evidence"])
            self.assertEqual({"collector": "synthetic-fixture"}, evidence["provenance"])
        self.assertTrue(all(worker.closed for worker in self.workers))

    def test_replay_restart_and_reordered_sources_do_not_duplicate(self):
        first = self.engine()
        first.submit("chain", ["bc", "ab"], "subsidiary-chain/1")
        done = first.advance("chain")
        before = copy.deepcopy(self.store.documents)
        restarted = self.engine()
        self.assertEqual(done, restarted.status("chain"))
        self.assertEqual(done, restarted.advance("chain"))
        self.assertEqual(done, restarted.submit("chain", ["ab", "bc"], "subsidiary-chain/1"))
        self.assertEqual(before, self.store.documents)
        restarted.submit("second-goal", ["ab", "bc"], "subsidiary-chain/1")
        self.assertEqual(done["results"], restarted.advance("second-goal")["results"])
        self.assertEqual(1, len(self.results("claim")))

    def test_lost_create_acknowledgement_recovers_after_restart(self):
        first = self.engine()
        first.submit("chain", ["ab", "bc"], "subsidiary-chain/1")
        self.store.lose_ack_dtype = "claim"
        retry = first.advance("chain")
        self.assertEqual("pending", retry["state"])
        self.assertEqual(1, retry["attempts"])
        self.assertEqual(1, len(self.results("claim")))
        done = self.engine().advance("chain")
        self.assertEqual("completed", done["state"])
        self.assertEqual(2, done["attempts"])
        self.assertEqual(1, len(self.results("claim")))

    def test_no_answer_and_typed_mismatch_never_write_claims(self):
        for source in ({"schema": "organization", "id": "unconnected"},
                       {"schema": "person", "id": "b"}):
            with self.subTest(source=source):
                self.setUp()
                self.store.documents["bc"]["source"] = source
                engine = self.engine()
                engine.submit("chain", ["ab", "bc"], "subsidiary-chain/1")
                result = engine.advance("chain")
                self.assertEqual("completed", result["state"])
                self.assertEqual([], result["results"])
                self.assertEqual([], self.results("claim"))

    def test_changed_revision_fails_closed_before_output(self):
        engine = self.engine()
        engine.submit("chain", ["ab", "bc"], "subsidiary-chain/1")
        self.store.documents["bc"]["rev"] = "2-changed"
        result = engine.advance("chain")
        self.assertEqual("failed", result["state"])
        self.assertEqual("stale_generation", result["error"])
        self.assertEqual([], self.results("claim"))
        self.assertEqual([], self.workers)

    def test_revision_changed_during_query_prevents_output(self):
        store = self.store
        class RacingQuery(QueryDouble):
            def query(self, parameters, limits, seconds):
                result = super().query(parameters, limits, seconds)
                store.documents["bc"]["rev"] = "2-raced"
                return result
        engine = ProactiveEngine(self.store, RacingQuery)
        engine.submit("chain", ["ab", "bc"], "subsidiary-chain/1")
        result = engine.advance("chain")
        self.assertEqual("failed", result["state"])
        self.assertEqual("stale_generation", result["error"])
        self.assertEqual([], self.results("claim"))

    def test_scope_and_candidate_sources_are_rejected(self):
        for change, error in (({"dataset": "foreign"}, "unsupported_projection"),
                              ({"provenance": {"derived": True}}, "untrusted_source"),
                              ({"provenance": {"generated": True}}, "untrusted_source"),
                              ({"provenance": {"trust": "candidate"}}, "untrusted_source"),
                              ({"verificationStatus": "rejected"}, "untrusted_source"),
                              ({"verificationStatus": "candidate"}, "untrusted_source")):
            with self.subTest(change=change):
                self.setUp()
                self.store.documents["bc"].update(change)
                with self.assertRaisesRegex(Fault, error):
                    self.engine().submit("chain", ["ab", "bc"], "subsidiary-chain/1")
                self.assertEqual([], self.store.created)

    def test_actor_allowlist_and_canonical_target(self):
        engine = self.engine(actors={"fixture-actor": ["corroborate-subsidiary/1"]})
        with self.assertRaisesRegex(Fault, "actor_required"):
            engine.submit("missing", ["ab", "bc"], "corroborate-subsidiary/1")
        with self.assertRaisesRegex(Fault, "unsupported_actor"):
            engine.submit("wrong", ["ab", "bc"], "corroborate-subsidiary/1", "unknown")
        engine.submit("target", ["ab", "bc"], "corroborate-subsidiary/1", "fixture-actor")
        done = engine.advance("target")
        self.assertEqual("completed", done["state"])
        target, = self.results("target")
        self.assertEqual("fixture-actor", target["actor"])
        self.assertEqual("a", target["target"])
        self.assertEqual("pending", target["state"])
        self.assertEqual(["corroborate-subsidiary/1"], target["options"]["required_capabilities"])
        self.assertEqual({"schema": "organization", "id": "c"}, target["options"]["destination"])
        self.assertEqual(["ab", "bc"], [ref["id"] for ref in target["options"]["starKbDerivation"]["sources"]])

    def test_actor_revocation_is_checked_after_restart(self):
        engine = self.engine(actors={"fixture-actor": ["corroborate-subsidiary/1"]})
        engine.submit("target", ["ab", "bc"], "corroborate-subsidiary/1", "fixture-actor")
        failed = self.engine().advance("target")
        self.assertEqual("unsupported_actor", failed["error"])
        self.assertEqual([], self.results("target"))

    def test_target_lost_acknowledgement_retries_without_duplicate_acceptance(self):
        actors = {"fixture-actor": ["corroborate-subsidiary/1"]}
        engine = self.engine(actors=actors)
        engine.submit("target", ["ab", "bc"], "corroborate-subsidiary/1", "fixture-actor")
        self.store.lose_ack_dtype = "target"
        self.assertEqual("pending", engine.advance("target")["state"])
        done = self.engine(actors=actors).advance("target")
        self.assertEqual("completed", done["state"])
        self.assertEqual(1, len(self.store.queued))
        self.assertEqual(1, len(self.results("target")))

    def test_retry_budget_survives_restart(self):
        engine = self.engine()
        engine.submit("chain", ["ab", "bc"], "subsidiary-chain/1")
        def unavailable(snapshot):
            raise Fault("engine_failure")
        for attempt in range(1, 4):
            result = ProactiveEngine(self.store, unavailable).advance("chain")
            self.assertEqual(attempt, result["attempts"])
            self.assertEqual("failed" if attempt == 3 else "pending", result["state"])
        before = copy.deepcopy(self.store.documents)
        self.assertEqual(result, self.engine().advance("chain"))
        self.assertEqual(before, self.store.documents)

    def test_malformed_journals_fail_with_domain_error(self):
        for suffix in (":done", ":attempt:1", ":failed"):
            for payload in ({}, {"unexpected": "field"}):
                with self.subTest(suffix=suffix, payload=payload):
                    self.setUp()
                    engine = self.engine()
                    engine.submit("chain", ["ab", "bc"], "subsidiary-chain/1")
                    plan, = self.results("task")
                    corrupt = {**copy.deepcopy(plan), "id": plan["id"] + suffix,
                               "extensions": {"starKbGoal": payload}}
                    self.store.documents[corrupt["id"]] = corrupt
                    with self.assertRaisesRegex(Fault, "invalid_goal_record"):
                        engine.status("chain")

    def test_noncontiguous_attempt_journal_is_rejected(self):
        engine = self.engine()
        engine.submit("chain", ["ab", "bc"], "subsidiary-chain/1")
        plan, = self.results("task")
        corrupt = {**copy.deepcopy(plan), "id": plan["id"] + ":attempt:2",
                   "extensions": {"starKbGoal": {"attempt": 2}}}
        self.store.documents[corrupt["id"]] = corrupt
        with self.assertRaisesRegex(Fault, "invalid_goal_record"):
            engine.status("chain")

    def test_final_uncertain_commit_remains_visible_after_exhaustion(self):
        def unavailable(snapshot):
            raise Fault("engine_failure")
        actors = {"fixture-actor": ["corroborate-subsidiary/1"]}
        for dtype, capability, actor in (("claim", "subsidiary-chain/1", None),
                                         ("target", "corroborate-subsidiary/1", "fixture-actor")):
            with self.subTest(dtype=dtype):
                self.setUp()
                engine = self.engine(actors=actors)
                engine.submit("chain", ["ab", "bc"], capability, actor)
                for _ in range(2):
                    self.assertEqual("pending", ProactiveEngine(
                        self.store, unavailable, actors=actors).advance("chain")["state"])
                self.store.lose_ack_dtype = dtype
                failed = engine.advance("chain")
                self.assertEqual("failed", failed["state"])
                self.assertEqual(3, failed["attempts"])
                committed, = self.results(dtype)
                logical = committed["options"]["starKbDerivation"]["logicalId"] if dtype == "target" else committed["id"]
                self.assertTrue(committed["id"] in failed["results"] or logical in failed["unresolvedEffects"])
                self.assertEqual(failed, self.engine(actors=actors).status("chain"))

    def test_effect_receipt_requires_result_ids(self):
        engine = self.engine()
        engine.submit("chain", ["ab", "bc"], "subsidiary-chain/1")
        engine.advance("chain")
        receipt, = [doc for key, doc in self.store.documents.items() if key.endswith(":effect:0")]
        receipt.pop("resultIds")
        with self.assertRaisesRegex(Fault, "invalid_goal_record"):
            engine.status("chain")

    def test_prepared_target_requires_derivation_fields(self):
        for field in ("provenance", "sources"):
            with self.subTest(field=field):
                self.setUp()
                engine = self.engine(actors={"fixture-actor": ["corroborate-subsidiary/1"]})
                engine.submit("target", ["ab", "bc"], "corroborate-subsidiary/1", "fixture-actor")
                engine.advance("target")
                prepared, = [doc for key, doc in self.store.documents.items() if key.endswith(":effects")]
                prepared["extensions"]["starKbGoal"]["effects"][0].pop(field)
                with self.assertRaisesRegex(Fault, "invalid_goal_record"):
                    engine.status("target")

    def test_completed_journal_cannot_advertise_forged_result(self):
        engine = self.engine()
        engine.submit("chain", ["ab", "bc"], "subsidiary-chain/1")
        engine.advance("chain")
        done, = [doc for key, doc in self.store.documents.items() if key.endswith(":done")]
        done["resultIds"] = ["unrelated-document"]
        with self.assertRaisesRegex(Fault, "invalid_goal_record"):
            engine.status("chain")

    def test_service_wire_goal_operations_replay_and_scope(self):
        from star_kb_service.contracts import PROTOCOL
        from star_kb_service.runtime import Principal, Service
        agenda = self.engine()
        service = Service({"a" * 32: Principal("alice", "tenant-a", "d"),
                           "b" * 32: Principal("bob", "tenant-b", "d")},
                          {("tenant-a", "d"): QueryDouble(self.store.snapshot(["ab", "bc"])),
                           ("tenant-b", "d"): QueryDouble(self.store.snapshot(["ab", "bc"]))},
                          proactive={("tenant-a", "d"): agenda})
        self.addCleanup(service.close)
        def request(identifier, operation, parameters=None, token="a" * 32):
            body = {"protocol": PROTOCOL, "id": identifier, "operation": operation}
            if parameters is not None:
                body["parameters"] = parameters
            return service.handle(token, encode(body))
        self.assertEqual("unauthorized", request("auth", "actor-capabilities", token="wrong")["error"])
        self.assertEqual("proactive_unconfigured", request(
            "scope", "goal-status", {"goal": "chain"}, token="b" * 32)["error"])
        self.assertEqual("star.kb.goals/1", request("caps", "actor-capabilities")["interface"])
        parameters = {"goal": "chain", "sources": ["ab", "bc"], "capability": "subsidiary-chain/1"}
        self.assertEqual("pending", request("submit", "goal-submit", parameters)["state"])
        self.assertEqual("invalid_request", request("inject", "goal-submit", {
            **parameters, "tenant": "tenant-b"})["error"])
        done = request("advance", "goal-advance", {"goal": "chain"})
        self.assertEqual("completed", done["state"])
        self.assertEqual(done, request("advance", "goal-advance", {"goal": "chain"}))
        self.assertEqual(done["results"], request("status", "goal-status", {"goal": "chain"})["results"])

    def test_bounds_and_conflicting_goal_replay(self):
        engine = self.engine()
        for sources in ([], ["ab", "ab"], [str(i) for i in range(17)]):
            with self.assertRaisesRegex(Fault, "fact_limit"):
                engine.submit("invalid", sources, "subsidiary-chain/1")
        engine.submit("chain", ["ab", "bc"], "subsidiary-chain/1")
        with self.assertRaisesRegex(Fault, "durable_replay_conflict"):
            engine.submit("chain", ["ab"], "subsidiary-chain/1")

    def test_result_bound_rejects_entire_batch_before_any_output(self):
        docs = [relation("left" + str(i), "a" + str(i), "middle") for i in range(4)]
        docs += [relation("right" + str(i), "middle", "c" + str(i)) for i in range(5)]
        self.store = MemoryDocuments(docs)
        engine = self.engine()
        engine.submit("fanout", [d["id"] for d in docs], "subsidiary-chain/1")
        result = engine.advance("fanout")
        self.assertEqual("failed", result["state"])
        self.assertEqual("result_limit", result["error"])
        self.assertEqual([], self.results("claim"))


class CanonicalHttpFixture:
    """Isolated protocol fixture, not a replacement StarIntel persistence server."""
    def __init__(self, documents):
        self.documents = {d["id"]: copy.deepcopy(d) for d in documents}
        self.targets = {}
        self.requests = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, status, body):
                payload = encode(body)
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def authorized(self):
                if self.headers.get("Authorization") != "Bearer " + "x" * 32:
                    self.reply(403, {})
                    return False
                fixture.requests.append((self.command, self.path))
                return True

            def do_GET(self):
                if not self.authorized():
                    return
                if self.path == "/api/v1/capabilities":
                    self.reply(200, {"data": {"features": {"document_create_only": "if-none-match-star/1"}}})
                    return
                identifier = unquote(self.path.removeprefix("/api/v1/documents/"))
                if identifier not in fixture.documents:
                    self.reply(404, {})
                else:
                    self.reply(200, fixture.documents[identifier])

            def do_PUT(self):
                if not self.authorized():
                    return
                if self.headers.get("If-None-Match") != "*":
                    self.reply(403, {})
                    return
                document = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                identifier = unquote(self.path.removeprefix("/api/v1/documents/"))
                if identifier in fixture.documents:
                    self.reply(412, {})
                else:
                    fixture.documents[identifier] = {**document, "rev": "1-fixture-server"}
                    self.reply(201, {})

            def do_POST(self):
                if not self.authorized():
                    return
                if self.path != "/api/v1/targets":
                    self.reply(404, {})
                    return
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                key = body["idempotency_key"]
                duplicate = key in fixture.targets
                if not duplicate:
                    identifier = "server-target-" + str(len(fixture.targets) + 1)
                    fixture.targets[key] = identifier
                    fixture.documents[identifier] = {
                        "id": identifier, "rev": "1-fixture-server", "dtype": "target",
                        "schemaVersion": "0.10.1", "dataset": body["dataset"],
                        "actor": body["actor"], "target": body["target"], "state": "pending",
                        "options": body["options"]}
                self.reply(200 if duplicate else 201, {
                    "status": "duplicate" if duplicate else "accepted", "target_id": fixture.targets[key]})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


@unittest.skipUnless(ROOT, "pinned generated schema required")
class ProactiveHttpTests(unittest.TestCase):
    def setUp(self):
        self.fixture = CanonicalHttpFixture([relation("ab", "a", "b"), relation("bc", "b", "c")])
        self.addCleanup(self.fixture.close)

    def store(self):
        return ServerDocuments(ROOT, "tenant-a", "d", f"http://127.0.0.1:{self.fixture.server.server_port}",
                               "x" * 32, allowed_types=("relation", "task", "claim", "target"))

    def test_target_bridge_returns_server_id_and_replays_durable_acceptance(self):
        actors = {"fixture-actor": ["corroborate-subsidiary/1"]}
        engine = ProactiveEngine(self.store(), QueryDouble, actors=actors)
        engine.submit("first", ["ab", "bc"], "corroborate-subsidiary/1", "fixture-actor")
        done = engine.advance("first")
        self.assertEqual("completed", done["state"])
        self.assertEqual(["server-target-1"], done["results"])
        target = self.fixture.documents["server-target-1"]
        self.assertTrue(target["options"]["starKbDerivation"]["provenance"]["derived"])
        self.assertEqual("candidate", target["options"]["starKbDerivation"]["verificationStatus"])
        restarted = ProactiveEngine(self.store(), QueryDouble, actors=actors)
        self.assertEqual(done, restarted.status("first"))
        restarted.submit("second", ["ab", "bc"], "corroborate-subsidiary/1", "fixture-actor")
        self.assertEqual(done["results"], restarted.advance("second")["results"])
        self.assertEqual(1, len(self.fixture.targets))
        self.assertEqual(2, self.fixture.requests.count(("POST", "/api/v1/targets")))
        self.assertFalse(any(method == "PUT" and "target" in path
                             for method, path in self.fixture.requests))

    @unittest.skipUnless(all(os.environ.get(k) for k in ("STAR_KB_SBCL", "STARLANG_SWI_EXECUTABLE")),
                         "real SBCL and SWI runtime required")
    def test_real_prolog_source_chain_canonical_claim_and_restart(self):
        from star_kb_service.engine import Engine
        workers = []
        def factory(snapshot):
            worker = Engine(ROOT, os.environ["STAR_KB_SBCL"], os.environ["STARLANG_SWI_EXECUTABLE"], snapshot)
            workers.append(worker)
            return worker
        engine = ProactiveEngine(self.store(), factory)
        engine.submit("real", ["ab", "bc"], "subsidiary-chain/1")
        done = engine.advance("real")
        self.assertEqual("completed", done["state"], done)
        claim, = [d for d in self.fixture.documents.values() if d["dtype"] == "claim"]
        self.assertEqual(["a"], claim["subjectRefs"])
        self.assertEqual(["c"], claim["objectRefs"])
        self.assertEqual([claim["id"]], done["results"])
        self.assertTrue(claim["provenance"]["engine"]["engineBuild"].startswith("sha256:"))
        self.assertEqual(done, ProactiveEngine(self.store(), factory).advance("real"))
        self.assertTrue(all(worker.process is None for worker in workers))
