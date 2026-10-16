"""Reuse the pinned producer's generated 0.10.1 document contracts."""
import hashlib
import json
from pathlib import Path
from jsonschema import Draft202012Validator
from star_kb_service.projection import SCHEMA_HASH
from star_kb_service.contracts import decode, encode


def digest(value):
    return hashlib.sha256(encode(value)).hexdigest()


class Canonical:
    def __init__(self, root):
        base = Path(root) / 'specs/starintel/0.10.1/generated'
        raw = (base / 'schema.json').read_bytes()
        if hashlib.sha256(raw).hexdigest() != SCHEMA_HASH:
            raise ValueError('canonical schema identity mismatch')
        self.schema = json.loads(raw)
        # Producer naming is deterministic: kebab-case document -> PascalCase def.
        self.types = {}
        for name, spec in self.schema['$defs'].items():
            if {'id', 'dtype', 'dataset', 'schemaVersion'} <= set(spec.get('required', [])):
                import re
                dtype = re.sub(r'(?<!^)(?=[A-Z])', '-', name).lower()
                self.types[dtype] = name

    def validate(self, doc):
        if not isinstance(doc, dict) or doc.get('schemaVersion') != '0.10.1':
            raise ValueError('expected a flat canonical 0.10.1 document')
        name = self.types.get(doc.get('dtype'))
        if not name:
            raise ValueError('unknown canonical document type')
        validator = Draft202012Validator({'$ref': '#/$defs/' + name,
                                          '$defs': self.schema['$defs']})
        if not validator.is_valid(doc):
            raise ValueError('document violates canonical ' + name + ' contract')
        if doc.get('deleted', False):
            raise ValueError('deleted document is not an active input')
        return doc


def read_json(path):
    return decode(Path(path).read_bytes())


def read_documents(path, canonical):
    raw = Path(path).read_bytes()
    if len(raw) > 262144:
        raise ValueError('document input exceeds 256 KiB')
    try:
        values = decode(raw)
    except Exception:
        values = [decode(line) for line in raw.splitlines() if line.strip()]
    if isinstance(values, dict):
        values = [values]
    if not isinstance(values, list) or len(values) > 256:
        raise ValueError('expected at most 256 documents')
    result = {}
    for value in values:
        canonical.validate(value)
        if value['id'] in result:
            raise ValueError('duplicate document id')
        result[value['id']] = value
    return result
