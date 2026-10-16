"""Bounded, resumable named goals over the existing canonical server and SWI bridge.

Canonical Task records are the agenda and journal. No local database or queue is
created. An operator/actor calls advance; no autonomous polling or live ingestion
is enabled by importing this library.
"""
import hashlib
import time
from .contracts import Fault, closed, encode, string, MAX_BYTES
from .projection import Snapshot

RULE = "subsidiary-chain/1"
CAPABILITIES = (RULE, "corroborate-subsidiary/1")
MAX_SOURCES = 16
MAX_RESULTS = 16
MAX_ATTEMPTS = 3
SECONDS = 5
# This digest versions the actual composition and trust policy, independently of
# the external adapter bootstrap digest retained in each derivation.
RULE_TEXT = "organization A subsidiary-of organization B; B subsidiary-of organization C; A!=C => candidate A indirect-subsidiary-of C; source-only; depth=2; v1"
RULE_DIGEST = "sha256:" + hashlib.sha256(RULE_TEXT.encode()).hexdigest()
TRANSIENT = {"store_unavailable", "store_failure", "deadline_exceeded", "engine_failure", "busy"}


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


class ProactiveEngine:
    def __init__(self, store, engine_factory, actors=None):
        self.store, self.engine_factory = store, engine_factory
        self.actors = {}
        for actor, capabilities in (actors or {}).items():
            string(actor)
            if (not isinstance(capabilities, list) or not capabilities
                    or set(capabilities) - set(CAPABILITIES)):
                raise Fault("invalid_actor_configuration")
            self.actors[actor] = sorted(set(capabilities))
        if len(self.actors) > 32:
            raise Fault("invalid_actor_configuration")

    def capabilities(self):
        return {"interface": "star.kb.goals/1", "rules": list(CAPABILITIES),
                "actors": {actor: list(caps) for actor, caps in self.actors.items()}, "limits": {"sources": MAX_SOURCES,
                "results": MAX_RESULTS, "attempts": MAX_ATTEMPTS, "depth": 2}}

    def _id(self, goal):
        return "star-kb-goal:" + digest([self.store.tenant, self.store.dataset, string(goal, 128)])

    def _read(self, identifier, deadline, optional=False):
        return self.store.read(identifier, deadline=deadline, optional=optional)

    def _create(self, document, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Fault("deadline_exceeded")
        return self.store.create(document, seconds=min(5, remaining))

    def _task(self, identifier, payload, results=()):
        return {"id": identifier, "dataset": self.store.dataset, "dtype": "task",
                "schemaVersion": "0.10.1", "taskType": "star-kb-goal/1",
                "resultIds": list(results), "extensions": {"starKbGoal": payload}}

    def _snapshot(self, identifiers, deadline):
        if (not isinstance(identifiers, list) or not 1 <= len(identifiers) <= MAX_SOURCES
                or any(not isinstance(x, str) for x in identifiers)
                or len(set(identifiers)) != len(identifiers)):
            raise Fault("fact_limit")
        documents = [self._read(string(identifier), deadline) for identifier in sorted(identifiers)]
        # Generated/candidate conclusions must never silently feed source rules.
        if any(doc.get("provenance", {}).get("derived") or
               doc.get("provenance", {}).get("generated") or
               doc.get("provenance", {}).get("trust") not in (None, "source", "verified", "trusted") or
               doc.get("verificationStatus") not in (None, "unverified", "verified", "confirmed") for doc in documents):
            raise Fault("untrusted_source")
        return Snapshot(self.store.starlang, self.store.tenant, self.store.dataset, documents)

    def submit(self, goal_id, source_ids, capability, actor=None):
        deadline = time.monotonic() + SECONDS
        identifier = self._id(goal_id)
        string(capability)
        if actor is not None:
            string(actor)
        if capability not in CAPABILITIES:
            raise Fault("unsupported_capability")
        if actor is not None and capability not in self.actors.get(actor, []):
            raise Fault("unsupported_actor")
        if capability == CAPABILITIES[1] and actor is None:
            raise Fault("actor_required")
        snapshot = self._snapshot(source_ids, deadline)
        payload = {"goal": goal_id, "capability": capability, "actor": actor,
                   "sources": sorted(source_ids), "generation": snapshot.generation,
                   "rule": RULE, "ruleDigest": RULE_DIGEST}
        self._create(self._task(identifier, payload), deadline)
        return self._status(goal_id, deadline)

    def _plan(self, goal, deadline):
        doc = self._read(self._id(goal), deadline)
        if doc.get("dtype") != "task" or doc.get("taskType") != "star-kb-goal/1":
            raise Fault("invalid_goal_record")
        payload = closed(doc.get("extensions", {}).get("starKbGoal"),
                         ("goal", "capability", "actor", "sources", "generation", "rule", "ruleDigest"))
        if (payload["goal"] != goal or payload["rule"] != RULE
                or payload["ruleDigest"] != RULE_DIGEST or payload["capability"] not in CAPABILITIES):
            raise Fault("goal_contract_mismatch")
        string(payload["generation"])
        if payload["actor"] is not None:
            string(payload["actor"])
        sources = payload["sources"]
        if (not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES
                or any(not isinstance(value, str) for value in sources)
                or len(set(sources)) != len(sources)):
            raise Fault("invalid_goal_record")
        for source in sources:
            string(source)
        return payload

    def _journal(self, identifier, deadline, fields):
        record = self._read(identifier, deadline, True)
        if record is None:
            return None
        try:
            if (record["dtype"] != "task" or record["taskType"] != "star-kb-goal/1"
                    or record["id"] != identifier):
                raise Fault("invalid_goal_record")
            payload = closed(record["extensions"]["starKbGoal"], fields)
            results = record["resultIds"]
            if not isinstance(results, list) or len(results) > MAX_RESULTS:
                raise Fault("invalid_goal_record")
            for result in results:
                string(result)
            if "state" in payload and payload["state"] not in ("completed", "accepted"):
                raise Fault("invalid_goal_record")
            if "attempt" in payload and (type(payload["attempt"]) is not int
                    or not 1 <= payload["attempt"] <= MAX_ATTEMPTS
                    or identifier.rsplit(":", 1)[1] != str(payload["attempt"])):
                raise Fault("invalid_goal_record")
            if "error" in payload:
                string(payload["error"])
            if "effects" in payload:
                if not isinstance(payload["effects"], list) or len(payload["effects"]) > MAX_RESULTS:
                    raise Fault("invalid_goal_record")
                for effect in payload["effects"]:
                    self.store.validate(effect)
                    if (effect["dtype"] not in ("claim", "target") or "rev" in effect
                            or not isinstance(effect.get("provenance"), dict)
                            or not isinstance(effect.get("sources"), list)
                            or effect["provenance"].get("derived") is not True
                            or effect["provenance"].get("ruleDigest") != RULE_DIGEST):
                        raise Fault("invalid_goal_record")
            return record
        except (KeyError, TypeError, Fault):
            raise Fault("invalid_goal_record") from None

    def _status(self, goal, deadline):
        plan = self._plan(goal, deadline)
        identifier = self._id(goal)
        done = self._journal(identifier + ":done", deadline, ("state",))
        attempts = []
        for number in range(1, MAX_ATTEMPTS + 1):
            record = self._journal(identifier + ":attempt:" + str(number), deadline, ("attempt",))
            if record is not None:
                attempts.append(number)
        if attempts != list(range(1, len(attempts) + 1)):
            raise Fault("invalid_goal_record")
        if done and done["extensions"]["starKbGoal"]["state"] != "completed":
            raise Fault("invalid_goal_record")
        failure = self._journal(identifier + ":failed", deadline, ("error",))
        prepared = self._journal(identifier + ":effects", deadline, ("effects",))
        observed, unresolved = [], []
        for index, effect in enumerate(prepared["extensions"]["starKbGoal"]["effects"] if prepared else []):
            receipt = self._journal(identifier + ":effect:" + str(index), deadline, ("state",))
            if receipt:
                if receipt["extensions"]["starKbGoal"]["state"] != "accepted" or len(receipt["resultIds"]) != 1:
                    raise Fault("invalid_goal_record")
            result_id = receipt["resultIds"][0] if receipt else effect["id"] if effect["dtype"] == "claim" else None
            stored = self._read(result_id, deadline, True) if result_id else None
            if stored:
                if effect["dtype"] == "claim":
                    if {k: v for k, v in stored.items() if k != "rev"} != effect:
                        raise Fault("durable_replay_conflict")
                else:
                    expected = {"provenance": effect["provenance"], "sources": effect["sources"],
                                "verificationStatus": "candidate", "logicalId": effect["id"]}
                    if (stored.get("dtype") != "target" or stored.get("actor") != effect["actor"]
                            or stored.get("target") != effect["target"]
                            or stored.get("options", {}).get("starKbDerivation") != expected):
                        raise Fault("durable_replay_conflict")
                observed.append(result_id)
            else:
                unresolved.append(effect["id"])
        if done and (prepared is None or unresolved or done.get("resultIds", []) != observed):
            raise Fault("invalid_goal_record")
        return {"state": "completed" if done else "failed" if failure or len(attempts) == MAX_ATTEMPTS else "pending",
                "goal": goal, "plan": plan, "attempts": len(attempts),
                "results": done.get("resultIds", []) if done else observed,
                "unresolvedEffects": [] if done else unresolved,
                "error": failure["extensions"]["starKbGoal"]["error"] if failure else
                         "retry_exhausted" if not done and len(attempts) == MAX_ATTEMPTS else None}

    def status(self, goal_id):
        return self._status(goal_id, time.monotonic() + SECONDS)

    def advance(self, goal_id):
        deadline = time.monotonic() + SECONDS
        state = self._status(goal_id, deadline)
        if state["state"] != "pending":
            return state
        identifier, plan = self._id(goal_id), state["plan"]
        attempt = state["attempts"] + 1
        # Reservation precedes any execution. A crash burns at most one bounded
        # attempt. Concurrent callers may execute the same deterministic work;
        # conditional create prevents duplicate canonical results.
        self._create(self._task(identifier + ":attempt:" + str(attempt), {"attempt": attempt}), deadline)
        engine = None
        try:
            actor, capability = plan["actor"], plan["capability"]
            if actor is not None and capability not in self.actors.get(actor, []):
                raise Fault("unsupported_actor")
            if capability == CAPABILITIES[1] and actor is None:
                raise Fault("actor_required")
            snapshot = self._snapshot(plan["sources"], deadline)
            if snapshot.generation != plan["generation"]:
                raise Fault("stale_generation")
            engine = self.engine_factory(snapshot)
            engine.start(max(.001, deadline - time.monotonic()))
            conclusions = self._infer(engine, snapshot, deadline)
            # Validate the complete bounded batch before the first output write.
            documents = [self._output(plan, snapshot, proof, engine.metadata) for proof in conclusions]
            if len(encode(documents)) > MAX_BYTES:
                raise Fault("output_limit")
            for document in documents:
                self.store.validate(document)
            # Revision check is optimistic, not a cross-document transaction.
            if self._snapshot(plan["sources"], deadline).generation != snapshot.generation:
                raise Fault("stale_generation")
            self._create(self._task(identifier + ":effects", {"effects": documents}), deadline)
            results = []
            for index, document in enumerate(documents):
                if document["dtype"] == "target":
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise Fault("deadline_exceeded")
                    stored = self.store.queue_target(document, seconds=min(5, remaining))
                else:
                    stored = self._create(document, deadline)
                results.append(stored["id"])
                self._create(self._task(identifier + ":effect:" + str(index),
                                        {"state": "accepted"}, [stored["id"]]), deadline)
            self._create(self._task(identifier + ":done", {"state": "completed"}, results), deadline)
        except Fault as exc:
            if exc.code not in TRANSIENT or attempt == MAX_ATTEMPTS:
                self._create(self._task(identifier + ":failed", {"error": exc.code}), deadline)
            # Transient failure deliberately leaves the durable reservation. The
            # next explicit advance retries after restart without volatile state.
        finally:
            if engine is not None:
                engine.close()
        return self._status(goal_id, deadline)

    def _infer(self, engine, snapshot, deadline):
        rows = {row["id"]: row for row in snapshot.rows}
        eligible = {identifier for identifier in rows
                    if rows[identifier]["predicate"] == "subsidiary-of"
                    and snapshot.evidence(identifier)["source"]["schema"] == "organization"
                    and snapshot.evidence(identifier)["destination"]["schema"] == "organization"}
        proofs = set()
        # Query each distinct intermediate node only once; <=16 queries. The
        # existing SWI adapter supplies the right side of the typed join.
        cache = {}
        for left in sorted(eligible):
            node = rows[left]["destination"]
            if node not in cache:
                result = engine.query({"node": node, "predicate": "subsidiary-of", "direction": "out"},
                                      {"answers": 128, "output-bytes": 65536},
                                      max(.001, deadline - time.monotonic()))
                if result["state"] not in ("completed", "no-answer"):
                    raise Fault("query_limit")
                cache[node] = result["rows"]
            for right in cache[node]:
                if right["id"] in eligible and rows[left]["source"] != right["destination"]:
                    proofs.add((left, right["id"]))
                    if len(proofs) > MAX_RESULTS:
                        raise Fault("result_limit")
        return sorted(proofs)

    def _output(self, plan, snapshot, proof, metadata):
        evidence = [snapshot.evidence(identifier) for identifier in proof]
        source, destination = evidence[0]["source"], evidence[1]["destination"]
        provenance = {"derived": True, "trust": "candidate", "rule": RULE,
                      "ruleDigest": RULE_DIGEST, "generation": snapshot.generation,
                      "tenant": self.store.tenant, "evidence": evidence,
                      "engine": metadata, "depth": 2}
        identity = {"tenant": self.store.tenant, "dataset": self.store.dataset,
                    "capability": plan["capability"], "actor": plan["actor"],
                    "source": source, "destination": destination, "provenance": provenance}
        key = digest(identity)
        common = {"dataset": self.store.dataset, "schemaVersion": "0.10.1",
                  "provenance": provenance, "verificationStatus": "candidate",
                  "sources": [{"schema": "relation", "id": identifier} for identifier in proof]}
        if plan["capability"] == RULE:
            return {**common, "id": "star-kb-claim:" + key, "dtype": "claim",
                    "claim": source["id"] + " is an indirect subsidiary of " + destination["id"],
                    "claimType": "derived-relation", "predicate": "indirect-subsidiary-of",
                    "subjectRefs": [source["id"]], "objectRefs": [destination["id"]],
                    "status": "candidate", "truthStatus": "unknown"}
        return {**common, "id": "star-target:v1:corroboration-gap:" + key, "dtype": "target",
                "actor": plan["actor"], "target": source["id"], "targetType": "corroboration-gap",
                "state": "pending", "attempts": 0, "maximumAttempts": 3,
                "options": {"predicate": "indirect-subsidiary-of", "destination": destination,
                            "required_capabilities": [CAPABILITIES[1]]},
                "extensions": {"kind": "corroboration-gap", "dedupe_key": key,
                               "created_by": "star-kb", "evidence_document_ids": list(proof)}}
