"""Bounded canonical document client; StarIntel server owns all durable writes.

Only explicitly configured loopback servers are supported in this slice.
Credentials are supplied by the operator, never by a query payload.
"""
import copy
import hashlib
import http.client
import json
import socket
import time
from pathlib import Path
from urllib.parse import quote, urlsplit
from jsonschema import Draft202012Validator
from .contracts import Fault, MAX_BYTES, decode, encode, string
from .projection import Snapshot, SCHEMA_HASH, CORE_HASH


class DeadlineSocket(socket.socket):
    """Absolute read/write budget also covers HTTP header parsing."""
    def budget(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise Fault("deadline_exceeded")
        self.settimeout(remaining)

    def recv_into(self, buffer, nbytes=0, flags=0):
        self.budget()
        if self.remaining_bytes <= 0:
            raise Fault("store_response_limit")
        count = super().recv_into(buffer, min(nbytes or len(buffer), self.remaining_bytes), flags)
        self.remaining_bytes -= count
        return count

    def sendall(self, data, flags=0):
        self.budget()
        return super().sendall(data, flags)


class ServerDocuments:
    def __init__(self, starlang, tenant, dataset, url, token, allowed_types=("relation",)):
        self.starlang, self.tenant, self.dataset = starlang, string(tenant), string(dataset)
        parsed = urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
                or parsed.username or parsed.password or parsed.path not in ("", "/")
                or parsed.query or parsed.fragment):
            raise Fault("invalid_store_configuration")
        if not isinstance(token, str) or len(token) < 32 or any(c in token for c in "\r\n"):
            raise Fault("invalid_store_configuration")
        self.port, self.token = parsed.port or 80, token
        base = Path(starlang) / "specs/starintel/0.10.1"
        for path, digest in (("core.star", CORE_HASH), ("generated/schema.json", SCHEMA_HASH)):
            if hashlib.sha256((base / path).read_bytes()).hexdigest() != digest:
                raise Fault("contract_mismatch")
        schema = json.loads((base / "generated/schema.json").read_bytes())
        names = {"relation": "Relation", "claim": "Claim", "target": "Target", "task": "Task"}
        if not allowed_types or set(allowed_types) - names.keys():
            raise Fault("invalid_store_configuration")
        self.validators = {kind: Draft202012Validator(
            {"$ref": "#/$defs/" + names[kind], "$defs": schema["$defs"]})
            for kind in allowed_types}

    def validate(self, document, revision=False):
        if not isinstance(document, dict) or len(encode(document)) > MAX_BYTES:
            raise Fault("invalid_canonical_document")
        if document.get("dtype") not in self.validators or not self.validators[document["dtype"]].is_valid(document):
            raise Fault("invalid_canonical_document")
        if (document.get("schemaVersion") != "0.10.1"
                or document.get("dataset") != self.dataset or document.get("deleted", False)
                or (document.get("dtype") == "relation" and document.get("direction", "directed") != "directed")):
            raise Fault("unsupported_projection")
        string(document["id"])
        if revision:
            string(document.get("rev", ""))
        return copy.deepcopy(document)

    def request(self, method, path, deadline, document=None, headers=None):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise Fault("deadline_exceeded")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=remaining)
        response = None
        try:
            connection.connect()
            sock = connection.sock
            bounded = DeadlineSocket(sock.family, sock.type, sock.proto, fileno=sock.detach())
            bounded.deadline = deadline
            bounded.remaining_bytes = MAX_BYTES + 65536
            connection.sock = bounded
            connection.request(method, path, body=None if document is None else encode(document),
                               headers={"Authorization": "Bearer " + self.token,
                                        "Content-Type": "application/json", **(headers or {})})
            response = connection.getresponse()
            # read1 returns available bytes so progress cannot reset the deadline.
            body = bytearray()
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise Fault("deadline_exceeded")
                if connection.sock is not None:
                    connection.sock.settimeout(remaining)
                block = response.read1(min(16384, MAX_BYTES + 1 - len(body)))
                if not block:
                    break
                body.extend(block)
                if len(body) > MAX_BYTES:
                    raise Fault("store_response_limit")
            if response.status in (401, 403):
                raise Fault("store_unauthorized")
            if response.status == 409:
                raise Fault("durable_replay_conflict")
            if response.status not in (200, 201, 404, 412):
                raise Fault("store_failure")
            return response.status, decode(bytes(body))
        except (OSError, http.client.HTTPException):
            raise Fault("store_unavailable") from None
        finally:
            if response is not None:
                response.close()
            connection.close()

    def read(self, identifier, deadline=None, optional=False):
        deadline = deadline if deadline is not None else time.monotonic() + 5
        string(identifier)
        status, document = self.request("GET", "/api/v1/documents/" + quote(identifier, safe=""), deadline)
        if status == 404 and optional:
            return None
        if status != 200:
            raise Fault("store_failure")
        document = self.validate(document, revision=True)
        if document["id"] != identifier:
            raise Fault("store_identity_mismatch")
        return document

    def create(self, document, seconds=5):
        """Atomic create, then authorized readback; identical replay is read-only.

        A timeout may occur after commit. Retry the same ID/body to resolve it.
        Existing differing documents are never patched or merged by this client.
        """
        if type(seconds) not in (int, float) or not 0 < seconds <= 5:
            raise Fault("invalid_request")
        deadline = time.monotonic() + seconds
        document = self.validate(document)
        if "rev" in document:
            raise Fault("server_owned_revision")
        _, capabilities = self.request("GET", "/api/v1/capabilities", deadline)
        if capabilities.get("data", {}).get("features", {}).get("document_create_only") != "if-none-match-star/1":
            raise Fault("store_contract_mismatch")
        self.request("PUT", "/api/v1/documents/" + quote(document["id"], safe=""), deadline,
                     document, {"If-None-Match": "*"})
        stored = self.read(document["id"], deadline)
        content = {k: v for k, v in stored.items() if k != "rev"}
        if encode(content) != encode(document):
            raise Fault("durable_replay_conflict")
        return stored

    def queue_target(self, document, seconds=5):
        """Use the server's durable acceptance/scheduling bridge, not a plain PUT.

        The server owns Target IDs, timestamps and execution state. Its stable
        idempotency ledger resolves commit-before-ack and scheduler restart.
        Provenance travels losslessly in canonical Target options.
        """
        if type(seconds) not in (int, float) or not 0 < seconds <= 5:
            raise Fault("invalid_request")
        document = self.validate(document)
        if document["dtype"] != "target" or "rev" in document:
            raise Fault("invalid_canonical_document")
        deadline = time.monotonic() + seconds
        options = {**document.get("options", {}), "starKbDerivation": {
            "provenance": document["provenance"], "sources": document["sources"],
            "verificationStatus": "candidate", "logicalId": document["id"]}}
        request = {"actor": document["actor"], "target": document["target"],
                   "dataset": self.dataset, "idempotency_key": document["id"],
                   "delay": 1, "recurring": False, "options": options}
        status, receipt = self.request("POST", "/api/v1/targets", deadline, request)
        if status not in (200, 201) or receipt.get("status") not in ("accepted", "duplicate"):
            raise Fault("store_contract_mismatch")
        stored = self.read(string(receipt.get("target_id")), deadline)
        if (stored.get("dtype") != "target" or stored.get("actor") != document["actor"]
                or stored.get("target") != document["target"] or stored.get("options") != options):
            raise Fault("durable_replay_conflict")
        return stored

    def snapshot(self, identifiers):
        if (not isinstance(identifiers, list) or not 1 <= len(identifiers) <= 256
                or any(not isinstance(identifier, str) for identifier in identifiers)
                or len(set(identifiers)) != len(identifiers)):
            raise Fault("fact_limit")
        deadline = time.monotonic() + 5
        documents, size = [], 0
        for identifier in identifiers:
            document = self.read(identifier, deadline)
            size += len(encode(document))
            if size > MAX_BYTES:
                raise Fault("snapshot_limit")
            documents.append(document)
        return Snapshot(self.starlang, self.tenant, self.dataset, documents)


def main():
    """Operator-only source ingestion; secrets stay in environment references."""
    import argparse
    import os
    from .contracts import closed
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("document", type=Path)
    args = parser.parse_args()
    config = closed(decode(args.config.read_bytes()), ("tenant", "dataset", "url", "tokenEnv"))
    client = ServerDocuments(os.environ["STARLANG_ROOT"], config["tenant"], config["dataset"],
                             config["url"], os.environ[config["tokenEnv"]])
    print(json.dumps(client.create(decode(args.document.read_bytes())), ensure_ascii=False))


if __name__ == "__main__":
    main()
