"""One authentication, scope, lifecycle and replay boundary for both transports."""
import copy
from dataclasses import dataclass
import hashlib
import hmac
import threading
import time
from .contracts import PROTOCOL, Fault, closed, decode, encode, integer, string
from .projection import SCHEMA_HASH, STARLANG_COMMIT


@dataclass(frozen=True)
class Principal:
    identity: str
    tenant: str
    dataset: str


class Service:
    def __init__(self, credentials, engines):
        # Credentials arrive from operator environment/keyring only. Scope is
        # supplied by trusted catalog configuration, never the request body.
        if not credentials or len(credentials) > 64 or not engines or len(engines) > 16:
            raise ValueError("bounded credentials and scoped engines required")
        self.credentials = dict(credentials)
        identities = set()
        for token, principal in self.credentials.items():
            if len(token) < 32 or principal.identity in identities:
                raise ValueError("unique principals and tokens of at least 32 characters required")
            identities.add(principal.identity)
            if (principal.tenant, principal.dataset) not in engines:
                raise ValueError("unknown credential scope")
        self.engines = dict(engines)
        self.replay = {}
        self.operations = {}
        self.lock = threading.Lock()
        self.closed = False
        try:
            for engine in self.engines.values():
                engine.start()
        except BaseException:
            self.close()
            raise

    def authenticate(self, token):
        if not isinstance(token, str) or len(token) > 4096:
            raise Fault("unauthorized")
        for secret, principal in self.credentials.items():
            if hmac.compare_digest(secret.encode(), token.encode()):
                return principal
        raise Fault("unauthorized")

    def handle(self, token, data):
        request_id = None
        try:
            principal = self.authenticate(token)
            request = decode(data)
            closed(request, ("protocol", "id", "operation"), ("generation", "parameters", "limits", "handle", "timeoutMs"))
            request_id = string(request["id"], 128)
            if request["protocol"] != PROTOCOL:
                raise Fault("protocol_mismatch")
            if not self.lock.acquire(blocking=False):
                raise Fault("busy")
            try:
                if self.closed:
                    raise Fault("service_closed")
                key = principal.identity, request_id
                digest = hashlib.sha256(encode(request)).hexdigest()
                if key in self.replay:
                    old_digest, reply = self.replay[key]
                    if old_digest != digest:
                        raise Fault("replay_conflict")
                    return copy.deepcopy(reply)
                if sum(p == principal.identity for p, _ in self.replay) >= 128:
                    raise Fault("request_budget")
                try:
                    result = self.dispatch(principal, request)
                    reply = {"protocol": PROTOCOL, "id": request_id, **result}
                except Fault as exc:
                    reply = {"protocol": PROTOCOL, "id": request_id, "state": "failed", "error": exc.code}
                self.replay[key] = digest, copy.deepcopy(reply)
                return reply
            finally:
                self.lock.release()
        except Fault as exc:
            return {"protocol": PROTOCOL, "id": request_id, "state": "failed", "error": exc.code}
        except (UnicodeError, TypeError, RecursionError):
            return {"protocol": PROTOCOL, "id": None, "state": "failed", "error": "invalid_request"}

    def dispatch(self, principal, req):
        engine = self.engines[(principal.tenant, principal.dataset)]
        snapshot = engine.snapshot
        op = req["operation"]
        common = ("protocol", "id", "operation")
        if op == "health":
            closed(req, common)
            live = engine.process is not None and engine.process.poll() is None
            return {"state": "ready" if live else "degraded", "generation": snapshot.generation,
                    "scope": {"tenant": principal.tenant, "dataset": principal.dataset},
                    "adapterCommit": STARLANG_COMMIT, "schemaDigest": SCHEMA_HASH,
                    "durable": False, **engine.metadata}
        if op == "relation-neighbors":
            closed(req, (*common, "generation", "parameters"), ("limits", "timeoutMs"))
            if req["generation"] != snapshot.generation:
                raise Fault("stale_generation")
            params = closed(req["parameters"], ("node", "predicate", "direction"))
            for value in params.values():
                string(value)
            if params["direction"] not in ("out", "in", "both"):
                raise Fault("invalid_request")
            limits = closed(req.get("limits", {}), (), ("answers", "output-bytes"))
            limits = {"answers": integer(limits.get("answers", 128), 1, 128),
                      "output-bytes": integer(limits.get("output-bytes", 65536), 1, 65536)}
            timeout = integer(req.get("timeoutMs", 5000), 1, 5000)
            if sum(p == principal.identity for p, _ in self.operations) >= 32:
                raise Fault("operation_budget")
            # Principal namespace + request id is a stable replay identity, not
            # an authority-bearing token. Every subsequent operation reauths.
            handle = req["id"]
            self.operations[(principal.identity, handle)] = {
                "state": "pending", "deadline": time.monotonic() + timeout / 1000,
                "parameters": params, "limits": limits, "generation": snapshot.generation}
            return {"state": "pending", "handle": handle, "generation": snapshot.generation}
        if op not in ("next", "cancel", "status"):
            raise Fault("unsupported_operation")
        closed(req, (*common, "handle"))
        handle = string(req["handle"], 128)
        operation = self.operations.get((principal.identity, handle))
        if operation is None:
            raise Fault("unknown_operation")
        if operation["state"] == "pending":
            if op == "cancel":
                operation["state"] = "cancelled"
            elif time.monotonic() >= operation["deadline"]:
                operation.update(state="failed", error="deadline_exceeded")
            elif op == "next":
                try:
                    result = engine.query(operation["parameters"], operation["limits"],
                                          operation["deadline"] - time.monotonic())
                    answers = [{**row, "provenance": snapshot.evidence(row["id"])} for row in result["rows"]]
                    result = {"state": result["state"], "answers": answers,
                              "generation": snapshot.generation, "rule": "relation-neighbors/1",
                              "derived": False, **engine.metadata}
                    if len(encode(result)) > operation["limits"]["output-bytes"]:
                        raise Fault("output_limit")
                    operation.update(result)
                except Fault as exc:
                    operation.update(state="failed", error=exc.code)
        return {k: copy.deepcopy(v) for k, v in operation.items()
                if k not in ("deadline", "parameters", "limits")}

    def close(self):
        with self.lock:
            self.closed = True
            for operation in self.operations.values():
                if operation["state"] == "pending":
                    operation["state"] = "cancelled"
            for engine in self.engines.values():
                engine.close()
