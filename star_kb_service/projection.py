"""Explicit flat 0.10.1 -> four-field StarLang bridge, NOT the legacy projector.

Unknown/additive data remains in canonical bytes and the existing JSON-Pointer
walker. The closed generated schema controls acceptance; extensions are lossless.
"""
import hashlib
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from prolog_star_kb.projection import _walk
from .contracts import Fault, encode, string, MAX_BYTES

STARLANG_COMMIT = "bfba04a15f43215cd323e1dde2d98a7343abb3ea"
CANONICAL_COMMIT = "96d26c338078676ec1dc6928755901636c9f7ef0"
SCHEMA_HASH = "1d677d679adcf64ed2df6e9e98eff47c3f66f67f10efbe4c4b69f7fffa2def0a"
CORE_HASH = "e09205e71fcd0f2bf87ff7d104c5ccff298d0054764def81e00f81a6bd8639fc"


class Snapshot:
    def __init__(self, starlang, tenant, dataset, documents):
        self.tenant, self.dataset = string(tenant), string(dataset)
        base = Path(starlang) / "specs/starintel/0.10.1"
        for name, expected in [("core.star", CORE_HASH), ("generated/schema.json", SCHEMA_HASH)]:
            if hashlib.sha256((base / name).read_bytes()).hexdigest() != expected:
                raise Fault("contract_mismatch")
        schema = json.loads((base / "generated/schema.json").read_bytes())
        validator = Draft202012Validator({"$ref": "#/$defs/Relation", "$defs": schema["$defs"]})
        if not isinstance(documents, list) or len(documents) > 256:
            raise Fault("fact_limit")
        raw = encode(documents)
        if len(raw) > MAX_BYTES:
            raise Fault("snapshot_limit")
        documents = json.loads(raw)  # own every value
        self._documents = {}
        rows = []
        for doc in documents:
            if not validator.is_valid(doc):
                raise Fault("invalid_canonical_document")
            # Deliberately narrow first semantic profile. No silent interpretation
            # of symmetric/inverse/deleted or legacy records as directed facts.
            if (doc["dtype"] != "relation" or doc["schemaVersion"] != "0.10.1"
                    or doc["dataset"] != dataset or doc.get("deleted", False)
                    or doc.get("direction", "directed") != "directed"):
                raise Fault("unsupported_projection")
            if doc["id"] in self._documents:
                raise Fault("duplicate_document")
            string(doc["rev"] if "rev" in doc else "")  # revision mandatory for provenance
            row = {"id": string(doc["id"]), "source": string(doc["source"]["id"]),
                   "predicate": string(doc["predicate"]),
                   "destination": string(doc["destination"]["id"])}
            self._documents[doc["id"]] = doc
            rows.append(row)
        self.rows = sorted(rows, key=lambda r: r["id"])
        if sum(len(v.encode()) for r in rows for v in r.values()) > 131072:
            raise Fault("snapshot_limit")
        identity = {"tenant": tenant, "dataset": dataset, "schema": SCHEMA_HASH,
                    "documents": [self._documents[k] for k in sorted(self._documents)]}
        self.generation = "sha256:" + hashlib.sha256(encode(identity)).hexdigest()

    def evidence(self, relation_id):
        doc = self._documents[relation_id]
        return json.loads(encode({"documentId": doc["id"], "revision": doc["rev"],
            "documentDigest": "sha256:" + hashlib.sha256(encode(doc)).hexdigest(),
            "sources": doc.get("sources", []), "evidence": doc.get("evidence", []),
            "provenance": doc.get("provenance", {}),
            "source": doc["source"], "destination": doc["destination"]}))

    def lossless_facts(self, relation_id):
        return tuple(_walk(relation_id, self._documents[relation_id]))
