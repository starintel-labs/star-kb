#!/usr/bin/env python3
"""Owned, synthetic Docker proof: actual server/CouchDB -> fresh SWI snapshot.

Run with STAR_KB_SERVER_ROOT and the normal STARLANG/SBCL/SWI variables.
No deployed service, production credentials, mocks or persistent test containers.
"""
import copy
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from star_kb_service.contracts import Fault, PROTOCOL, encode
from star_kb_service.persistence import ServerDocuments
from star_kb_service.engine import Engine
from star_kb_service.runtime import Service, Principal

PINNED_IMAGE = "ghcr.io/lost-rob0t/starintel-server@sha256:9f889150082971212a3b04196aca0c7d014476f6d61e1574cd909ef81b9cc97d"
IMAGE = os.environ.get("STAR_KB_TEST_SERVER_IMAGE", PINNED_IMAGE)
SERVER_FILES = ("source/databases/document-update.lisp", "source/frontends/http-document-update.lisp",
                "source/frontends/http-authorization-routes.lisp", "source/frontends/http-capabilities.lisp")


def docker(*args):
    return subprocess.check_output(["docker", *args], text=True, stderr=subprocess.PIPE, timeout=120).strip()


def http(url, method="GET", document=None, token=None, headers=None):
    request = urllib.request.Request(url, method=method,
        data=encode(document) if document is not None else None,
        headers={"Content-Type": "application/json", **({"Authorization": "Bearer " + token} if token else {}), **(headers or {})})
    try:
        response = urllib.request.urlopen(request, timeout=3)
    except urllib.error.HTTPError as exc:
        response = exc
    with response:
        return response.status, json.load(response)


class DurableReadback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = os.environ["STARLANG_ROOT"]
        cls.server_root = Path(os.environ["STAR_KB_SERVER_ROOT"]).resolve()
        cls.name = "kb-proof-" + secrets.token_hex(5)
        cls.owned = []
        cls.temp = tempfile.TemporaryDirectory(prefix="kb-durable-")
        cls.addClassCleanup(cls.cleanup)
        docker("network", "create", cls.name)
        cls.couch = cls.name + "-couch"
        cls.rabbit = cls.name + "-rabbit"
        cls.server = cls.name + "-server"
        cls.password, cls.bootstrap, cls.pepper = (secrets.token_urlsafe(32) for _ in range(3))
        def envfile(name, values):
            path = Path(cls.temp.name) / name
            path.write_text("".join(k + "=" + v + "\n" for k, v in values.items()))
            path.chmod(0o600)
            return str(path)
        cls.envfile = envfile
        couch_env = envfile("couch.env", {"COUCHDB_USER": "kbtest", "COUCHDB_PASSWORD": cls.password, "NODENAME": "127.0.0.1"})
        rabbit_env = envfile("rabbit.env", {"RABBITMQ_DEFAULT_USER": "kbtest", "RABBITMQ_DEFAULT_PASS": cls.password, "RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS": "+S 2:2 +SDcpu 1 +SDio 1", "RABBITMQ_CTL_ERL_ARGS": "+S 1:1 +SDcpu 1 +SDio 1"})
        for name, image, env, port in ((cls.couch, "couchdb:3.5.2", couch_env, "5984"),
                                      (cls.rabbit, "rabbitmq:4.3.4-alpine", rabbit_env, None)):
            args = ["create", "--name", name, "--network", cls.name, "--memory", "512m", "--cpus", "2", "--env-file", env]
            if port:
                args += ["-p", "127.0.0.1::" + port]
            docker(*args, image)
            cls.owned.append(name)
            docker("start", name)
        cls.wait_couch()
        deadline = time.monotonic() + 60
        while True:
            try:
                docker("exec", "--user", "rabbitmq", cls.rabbit, "rabbitmq-diagnostics", "-q", "--timeout", "5", "ping")
                break
            except subprocess.CalledProcessError:
                if time.monotonic() > deadline:
                    raise RuntimeError("Rabbit readiness deadline")
                time.sleep(.5)
        server_env = envfile("server.env", {
            "COUCHDB_HOST": cls.couch, "COUCHDB_DATABASE": "kbproof", "COUCHDB_USER": "kbtest",
            "COUCHDB_PASSWORD": cls.password, "STAR_AUTH_DATABASE": "kbauth",
            "RABBITMQ_ADDRESS": cls.rabbit, "RABBITMQ_USER": "kbtest", "RABBITMQ_PASSWORD": cls.password,
            "HTTP_API_LISTEN_ADDRESS": "0.0.0.0", "STAR_AUTH_MODE": "api-key",
            "STAR_AUTH_PEPPER": cls.pepper, "STAR_AUTH_BOOTSTRAP_SECRET": cls.bootstrap,
            "STAR_TENANT_DATASET_MAP": "proof-a=tenant-a,proof-b=tenant-b", "STAR_SERVER_INIT_FILE": "/kb-proof/init.lisp"})
        docker("create", "--name", cls.server, "--network", cls.name, "--memory", "768m", "--cpus", "2",
               "--env-file", server_env, "-p", "127.0.0.1::5000", IMAGE)
        cls.owned.append(cls.server)
        overlay = Path(cls.temp.name) / "overlay"
        overlay.mkdir()
        # Load exact worktree files through the existing trusted init hook.
        init = ['(load "/kb-proof/base-init.lisp")', '(setf star:*ingest-workers* 2)']
        for index, file in enumerate(SERVER_FILES):
            name = str(index) + ".lisp"
            (overlay / name).write_bytes((cls.server_root / file).read_bytes())
            init.append('(handler-bind ((warning (function muffle-warning))) (load "/kb-proof/' + name + '"))')
        (overlay / "base-init.lisp").write_bytes((cls.server_root / "docker/star-server-init.lisp").read_bytes())
        (overlay / "init.lisp").write_text("\n".join(init) + "\n")
        overlay.chmod(0o755)
        for path in overlay.iterdir():
            path.chmod(0o644)
        docker("cp", str(overlay), cls.server + ":/kb-proof")
        docker("start", cls.server)
        cls.wait_server()
        status, result = http(cls.url + "/auth/bootstrap", "POST", {"owner": "kb-proof-admin"},
                              headers={"X-Star-Bootstrap-Secret": cls.bootstrap})
        if status not in (200, 201):
            raise RuntimeError("Bootstrap failed: " + str(status))
        cls.admin = result["api_key"]
        cls.tokens = {}
        for scope in ("a", "b"):
            status, result = http(cls.url + "/auth/credentials", "POST",
                {"owner": "kb-proof-" + scope, "principal_type": "api_client",
                 "scopes": ["documents:read", "documents:write", "targets:dispatch", "actor:fixture-corroborator", "tenant:tenant-" + scope, "dataset:proof-" + scope]}, cls.admin)
            if status not in (200, 201):
                raise RuntimeError("Credential creation failed: " + str(status))
            cls.tokens[scope] = result["api_key"]

    @classmethod
    def cleanup(cls):
        for name in reversed(cls.owned):
            subprocess.run(["docker", "rm", "-fv", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=45)
        subprocess.run(["docker", "network", "rm", cls.name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=15)
        cls.temp.cleanup()

    @classmethod
    def wait_couch(cls):
        port = docker("port", cls.couch, "5984/tcp").split(":")[-1]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                if http("http://127.0.0.1:" + port + "/_up")[0] == 200:
                    return
            except (OSError, ValueError):
                pass
            time.sleep(.25)
        raise RuntimeError("CouchDB readiness deadline")

    @classmethod
    def wait_server(cls):
        port = docker("port", cls.server, "5000/tcp").split(":")[-1]
        cls.url = "http://127.0.0.1:" + port
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                if http(cls.url + "/api/v1/capabilities")[0] == 200:
                    return
            except (OSError, ValueError):
                pass
            time.sleep(.25)
        # Do not dump logs which may contain credentials or private documents.
        raise RuntimeError("Server readiness deadline; inspect owned server logs locally")

    def client(self, scope="a"):
        return ServerDocuments(self.root, "tenant-" + scope, "proof-" + scope, self.url, self.tokens[scope])

    def document(self, identifier, scope="a"):
        return {"id": identifier, "dtype": "relation", "schemaVersion": "0.10.1", "dataset": "proof-" + scope,
                "source": {"schema": "entity", "id": "subject"}, "destination": {"schema": "entity", "id": "object"},
                "predicate": "owns", "direction": "directed", "sources": [{"schema": "source", "id": "source-fixture"}],
                "evidence": [{"schema": "evidence-record", "id": "evidence-fixture"}],
                "provenance": {"collector": "synthetic-durable-proof"},
                "extensions": {"futureField": {"unicode": "雪", "nested": [False, None, 3]}}}

    def test_00_http_create_only_does_not_overwrite(self):
        source = self.document("proof:header")
        path = self.url + "/api/v1/documents/" + source["id"]
        status, _ = http(path, "PUT", source, self.tokens["a"], {"If-None-Match": "*"})
        self.assertIn(status, (200, 201))
        before = http(path, token=self.tokens["a"])[1]
        changed = copy.deepcopy(source)
        changed["predicate"] = "must-not-overwrite"
        status, _ = http(path, "PUT", changed, self.tokens["a"], {"If-None-Match": "*"})
        self.assertEqual(412, status)
        self.assertEqual(before, http(path, token=self.tokens["a"])[1])
        status, _ = http(path, "PUT", changed, self.tokens["a"], {"If-None-Match": "unsupported"})
        self.assertEqual(400, status)
        # Ordinary existing PUT remains an update, preserving its prior contract.
        status, _ = http(path, "PUT", changed, self.tokens["a"])
        self.assertEqual(200, status)
        self.assertEqual(changed["predicate"], http(path, token=self.tokens["a"])[1]["predicate"])

    def test_01_durable_restart_replay_scoped_real_query(self):
        client = self.client()
        source = self.document("proof:restart")
        stored = client.create(source)
        self.assertEqual(source, {k: v for k, v in stored.items() if k != "rev"})
        self.assertEqual(stored, client.create(source))
        before = client.snapshot([source["id"]])
        # Stop the server before restarting its real durable store, then start a
        # fresh server process. There is no KB memory state to replay into storage.
        docker("stop", "-t", "15", self.server)
        docker("restart", "-t", "15", self.couch)
        self.wait_couch()
        docker("start", self.server)
        self.wait_server()
        client = self.client()
        self.assertEqual(stored, client.read(source["id"]))
        self.assertEqual(stored, client.create(source))
        snapshot = client.snapshot([source["id"]])
        self.assertEqual(before.generation, snapshot.generation)
        engine = Engine(self.root, os.environ["STAR_KB_SBCL"], os.environ["STARLANG_SWI_EXECUTABLE"], snapshot)
        token = secrets.token_urlsafe(32)
        service = Service({token: Principal("proof", "tenant-a", "proof-a")}, {("tenant-a", "proof-a"): engine})
        try:
            request = {"protocol": PROTOCOL, "id": "durable-query", "operation": "relation-neighbors",
                       "generation": snapshot.generation,
                       "parameters": {"node": "subject", "predicate": "owns", "direction": "out"}}
            pending = service.handle(token, encode(request))
            result = service.handle(token, encode({"protocol": PROTOCOL, "id": "durable-next", "operation": "next", "handle": pending["handle"]}))
            self.assertEqual("completed", result["state"])
            evidence = result["answers"][0]["provenance"]
            self.assertEqual(stored["rev"], evidence["revision"])
            self.assertEqual(source["evidence"], evidence["evidence"])
            self.assertEqual(source["sources"], evidence["sources"])
        finally:
            service.close()

    def test_02_conflicting_replay_and_foreign_scope_never_write(self):
        client = self.client()
        source = self.document("proof:conflict")
        stored = client.create(source)
        changed = copy.deepcopy(source)
        changed["predicate"] = "different"
        with self.assertRaisesRegex(Fault, "durable_replay_conflict"):
            client.create(changed)
        self.assertEqual(stored, client.read(source["id"]))
        with self.assertRaisesRegex(Fault, "store_unauthorized"):
            self.client("b").read(source["id"])
        with self.assertRaisesRegex(Fault, "store_unauthorized"):
            self.client("b").create(self.document(source["id"], "b"))
        self.assertEqual(stored, client.read(source["id"]))

    def test_03_concurrent_insert_has_one_immutable_winner(self):
        documents = [self.document("proof:race"), self.document("proof:race")]
        documents[1]["predicate"] = "competes"
        barrier = threading.Barrier(2)
        outcomes = []
        def run(document):
            barrier.wait(timeout=3)
            try:
                outcomes.append(self.client().create(document))
            except Fault as exc:
                outcomes.append(exc.code)
        threads = [threading.Thread(target=run, args=(d,)) for d in documents]
        for thread in threads: thread.start()
        for thread in threads:
            thread.join(8)
            self.assertFalse(thread.is_alive())
        self.assertEqual(1, sum(isinstance(x, dict) for x in outcomes))
        self.assertIn("durable_replay_conflict", outcomes)
        winner = next(x for x in outcomes if isinstance(x, dict))
        self.assertEqual(winner, self.client().read("proof:race"))

    def test_05_proactive_claim_restart_and_commit_before_ack(self):
        from star_kb_service.proactive import ProactiveEngine, RULE
        client = ServerDocuments(self.root, "tenant-a", "proof-a", self.url, self.tokens["a"],
                                 allowed_types=("relation", "task", "claim", "target"))
        for identifier, source, destination in (("proof:chain-ab", "org:a", "org:b"),
                                                ("proof:chain-bc", "org:b", "org:c")):
            document = self.document(identifier)
            document.update(source={"schema": "organization", "id": source},
                            destination={"schema": "organization", "id": destination}, predicate="subsidiary-of")
            client.create(document)
        factory = lambda snapshot: Engine(self.root, os.environ["STAR_KB_SBCL"],
                                           os.environ["STARLANG_SWI_EXECUTABLE"], snapshot)
        agenda = ProactiveEngine(client, factory)
        agenda.submit("durable-chain", ["proof:chain-ab", "proof:chain-bc"], RULE)
        original = client.create
        lost = []
        def uncertain(document, seconds=5):
            result = original(document, seconds)
            if document["dtype"] == "claim" and not lost:
                lost.append(result)
                raise Fault("store_unavailable")  # committed server write, lost acknowledgment
            return result
        client.create = uncertain
        self.assertEqual("pending", agenda.advance("durable-chain")["state"])
        docker("stop", "-t", "15", self.server)
        docker("restart", "-t", "15", self.couch)
        self.wait_couch()
        docker("start", self.server)
        self.wait_server()
        fresh = ServerDocuments(self.root, "tenant-a", "proof-a", self.url, self.tokens["a"],
                                allowed_types=("relation", "task", "claim", "target"))
        resumed = ProactiveEngine(fresh, factory).advance("durable-chain")
        self.assertEqual("completed", resumed["state"])
        self.assertEqual([lost[0]["id"]], resumed["results"])
        self.assertEqual(lost[0], fresh.read(resumed["results"][0]))
        self.assertEqual("indirect-subsidiary-of", lost[0]["predicate"])
        self.assertEqual("candidate", lost[0]["status"])

    def test_06_proactive_target_uses_durable_acceptance_bridge(self):
        from star_kb_service.proactive import ProactiveEngine
        client = ServerDocuments(self.root, "tenant-a", "proof-a", self.url, self.tokens["a"],
                                 allowed_types=("relation", "task", "claim", "target"))
        for identifier, source, destination in (("proof:target-ab", "org:x", "org:y"),
                                                ("proof:target-bc", "org:y", "org:z")):
            document = self.document(identifier)
            document.update(source={"schema": "organization", "id": source},
                            destination={"schema": "organization", "id": destination}, predicate="subsidiary-of")
            client.create(document)
        capability = "corroborate-subsidiary/1"
        actors = {"fixture-corroborator": [capability]}
        factory = lambda snapshot: Engine(self.root, os.environ["STAR_KB_SBCL"],
                                           os.environ["STARLANG_SWI_EXECUTABLE"], snapshot)
        agenda = ProactiveEngine(client, factory, actors)
        sources = ["proof:target-ab", "proof:target-bc"]
        agenda.submit("durable-target", sources, capability, "fixture-corroborator")
        original = client.queue_target
        lost = []
        def uncertain(document, seconds=5):
            result = original(document, seconds)
            if not lost:
                lost.append(result)
                raise Fault("store_unavailable")
            return result
        client.queue_target = uncertain
        pending = agenda.advance("durable-target")
        self.assertEqual("pending", pending["state"])
        self.assertEqual(1, len(pending["unresolvedEffects"]))
        resumed = ProactiveEngine(client, factory, actors).advance("durable-target")
        self.assertEqual("completed", resumed["state"])
        self.assertEqual([lost[0]["id"]], resumed["results"])
        target = client.read(resumed["results"][0])
        self.assertEqual("target", target["dtype"])
        self.assertEqual("fixture-corroborator", target["actor"])
        self.assertTrue(target["options"]["starKbDerivation"]["provenance"]["derived"])
        # The isolated actor name has no live collector bound: proof covers
        # durable acceptance/scheduling, not a live investigation or execution.

    def test_04_invalid_canonical_source_rejected_before_write(self):
        source = self.document("proof:invalid")
        source["unknownTopLevel"] = "rejected"
        with self.assertRaisesRegex(Fault, "invalid_canonical_document"):
            self.client().create(source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
