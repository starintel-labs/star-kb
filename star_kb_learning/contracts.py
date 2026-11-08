"""Learning sidecar contract, deliberately distinct from canonical documents."""
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from jsonschema import Draft202012Validator
from star_kb_service.projection import CORE_HASH, SCHEMA_HASH

VERSION = 'star.kb.learning/1'
KINDS = {'observation', 'attributed_claim', 'hypothesis', 'association',
         'definition', 'conclusion', 'selector', 'evidence_request', 'experiment'}
DTYPES = {'document': 'Document', 'message': 'Message', 'claim': 'Claim',
          'relation': 'Relation', 'event': 'Event', 'org': 'Org', 'user': 'User',
          'location': 'Location', 'observation': 'Observation', 'concept': 'Concept',
          'socialmpost':'Socialmpost', 'social-media-post':'SocialMediaPost', 'source':'Source'}


class LearningError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


@dataclass(frozen=True)
class Scope:
    project: str
    operation: str
    dataset: str

    def __post_init__(self):
        for value in (self.project, self.operation, self.dataset):
            if not isinstance(value, str) or not 1 <= len(value) <= 128:
                raise LearningError('invalid_scope')

    @property
    def key(self):
        return canonical([self.project, self.operation, self.dataset])


@dataclass(frozen=True)
class Limits:
    documents: int = 256
    document_bytes: int = 65536
    observations: int = 10000
    candidates: int = 32
    steps: int = 10000
    depth: int = 12
    results: int = 256
    seconds: float = 3
    queue: int = 128
    attempts: int = 3

    def __post_init__(self):
        bounds = {'documents': 2048, 'document_bytes': 262144, 'observations': 100000,
                  'candidates': 128, 'steps': 100000, 'depth': 24, 'results': 2048,
                  'queue': 1024, 'attempts': 10}
        for key, maximum in bounds.items():
            if type(getattr(self, key)) is not int or not 1 <= getattr(self, key) <= maximum:
                raise LearningError('invalid_limit:' + key)
        if type(self.seconds) not in (int, float) or not math.isfinite(self.seconds) or not 0 < self.seconds <= 10:
            raise LearningError('invalid_limit:seconds')


class CanonicalAdapter:
    """Pinned generated validator; no inference of 0.10.1 from legacy _id/data."""
    def __init__(self, starlang):
        root = Path(starlang) / 'specs/starintel/0.10.1'
        for name, expected in [('core.star', CORE_HASH), ('generated/schema.json', SCHEMA_HASH)]:
            if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
                raise LearningError('contract_mismatch')
        self.defs = json.loads((root / 'generated/schema.json').read_bytes())['$defs']
        self.validators = {k: Draft202012Validator({'$ref': '#/$defs/' + v, '$defs': self.defs})
                           for k, v in DTYPES.items()}

    def validate(self, document, scope, limits):
        raw = canonical(document)
        if len(raw.encode()) > limits.document_bytes:
            raise LearningError('document_limit')
        if not isinstance(document, dict) or document.get('schemaVersion') != '0.10.1':
            raise LearningError('explicit_adapter_required')
        validator = self.validators.get(document.get('dtype'))
        if validator is None or not validator.is_valid(document):
            raise LearningError('invalid_canonical_document')
        if document['dataset'] != scope.dataset:
            raise LearningError('scope_mismatch')
        # Project and operation live in an explicit namespaced extension, not an
        # invented top-level canonical field. Never infer scope from content.
        binding = document.get('extensions', {}).get('starKbScope')
        if binding != {'project': scope.project, 'operation': scope.operation}:
            raise LearningError('scope_mismatch')
        return json.loads(raw)
