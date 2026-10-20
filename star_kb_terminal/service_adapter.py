"""Client for the engine owner's existing star.kb.service/1 HTTP operations.

No new listener, transport protocol, scheduler or actor implementation. The
existing bounded loopback HTTP client supplies deadlines and rejects redirects.
"""
import os
import http.client
import time
import uuid
from urllib.parse import quote
from star_kb_service.contracts import PROTOCOL, Fault, encode, decode, MAX_BYTES
from star_kb_service.persistence import ServerDocuments, DeadlineSocket
from .contracts import digest, Canonical


class ServiceEngine:
    simulated = False

    def __init__(self, canonical_root, url, token_env, results_url=None, results_token_env=None):
        token = os.environ.get(token_env)
        if not token:
            raise ValueError('missing engine credential environment variable')
        self.client = ServerDocuments(canonical_root, 'operator', 'operator', url, token)
        health = self.call('health')
        if health.get('state') != 'ready' or not isinstance(health.get('scope'), dict):
            raise ValueError('engine not ready or missing authenticated scope')
        self.scope = health['scope']
        self.canonical = Canonical(canonical_root)
        self.readback = None
        if results_url:
            result_token = os.environ.get(results_token_env or '')
            if not result_token:
                raise ValueError('missing canonical readback credential environment variable')
            self.readback = ServerDocuments(canonical_root, self.scope['tenant'], self.scope['dataset'],
                                            results_url, result_token)
        self.identity = 'service:' + digest({'url': url.rstrip('/'), 'scope': health['scope']})

    def call(self, operation, parameters=None):
        # Service replay is per-request; polling must not replay an old status.
        request = {'protocol': PROTOCOL, 'id': uuid.uuid4().hex, 'operation': operation}
        if parameters is not None:
            request['parameters'] = parameters
        status, reply = self.request(request)
        if (status not in (200, 400) or not isinstance(reply, dict) or reply.get('protocol') != PROTOCOL
                or reply.get('id') != request['id']):
            raise ValueError('invalid engine service reply')
        if reply.get('state') == 'failed' and 'goal' not in reply:
            raise ValueError('engine rejected operation: ' + str(reply.get('error', 'unknown')))
        return reply

    def request(self, request):
        # Existing transport returns 400 for any error key, including nullable
        # goal.error. Preserve the typed response; a 400 is never itself success.
        deadline = time.monotonic() + 12
        connection = http.client.HTTPConnection('127.0.0.1', self.client.port, timeout=12)
        response = None
        try:
            connection.connect()
            sock = connection.sock
            bounded = DeadlineSocket(sock.family, sock.type, sock.proto, fileno=sock.detach())
            bounded.deadline, bounded.remaining_bytes = deadline, MAX_BYTES + 65536
            connection.sock = bounded
            connection.request('POST', '/v1/kb', body=encode(request),
                               headers={'Authorization': 'Bearer ' + self.client.token,
                                        'Content-Type': 'application/json'})
            response = connection.getresponse()
            if response.status not in (200, 400):
                raise ValueError('engine HTTP status ' + str(response.status))
            body = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise ValueError('engine response deadline exceeded')
                block = response.read1(min(16384, MAX_BYTES + 1 - len(body)))
                if not block:
                    break
                body.extend(block)
                if len(body) > MAX_BYTES:
                    raise ValueError('engine response exceeds limit')
            return response.status, decode(bytes(body))
        except (OSError, http.client.HTTPException):
            raise ValueError('engine unavailable; last observation is not completion') from None
        finally:
            if response is not None:
                response.close()
            connection.close()

    def capabilities(self):
        value = self.call('actor-capabilities')
        if (value.get('interface') != 'star.kb.goals/1' or not isinstance(value.get('rules'), list)
                or not isinstance(value.get('actors'), dict) or not isinstance(value.get('limits'), dict)):
            raise ValueError('unsupported engine goal interface')
        # Exclude per-request envelope fields from plan identity.
        return {key: value[key] for key in ('interface', 'rules', 'actors', 'limits')}

    def submit(self, plan, request_id):
        spec = plan.get('engineGoal')
        if not spec:
            raise ValueError('HTTP engine requires plan --capability and explicit canonical sources')
        parameters = {'goal': request_id, 'sources': spec['sources'], 'capability': spec['capability']}
        if spec['actor']:
            parameters['actor'] = spec['actor']
        return self.convert(self.call('goal-submit', parameters), request_id)

    def status(self, request_id):
        return self.convert(self.call('goal-status', {'goal': request_id}), request_id)

    def advance(self, request_id):
        return self.convert(self.call('goal-advance', {'goal': request_id}), request_id)

    def results(self, request_id):
        reply = self.status(request_id)
        if self.readback is None:
            return reply
        deadline = time.monotonic() + 5
        documents, evidence = [], []
        for identifier in reply['resultIds']:
            status, doc = self.readback.request('GET', '/api/v1/documents/' + quote(identifier, safe=''), deadline)
            if status != 200:
                raise ValueError('canonical result readback unavailable')
            self.canonical.validate(doc)
            if (doc['id'] != identifier or doc['dataset'] != self.scope['dataset']
                    or not isinstance(doc.get('rev'), str) or not doc['rev']):
                raise ValueError('canonical result identity/scope/revision mismatch')
            documents.append(doc)
            evidence.append({'documentId': identifier, 'revision': doc['rev'],
                'documentDigest': digest(doc), 'readback': 'validated canonical 0.10.1',
                'sources': doc.get('sources', []), 'evidence': doc.get('evidence', []),
                'provenance': doc.get('provenance', {})})
        reply['documents'], reply['evidence'] = documents, evidence
        return reply

    def convert(self, reply, request_id):
        if reply.get('goal') != request_id or reply.get('state') not in {'pending', 'completed', 'failed'}:
            raise ValueError('invalid goal status')
        attempts = reply.get('attempts')
        results, unresolved = reply.get('results'), reply.get('unresolvedEffects')
        if (type(attempts) is not int or not 0 <= attempts <= 3 or not isinstance(results, list)
                or len(results) > 16 or not isinstance(unresolved, list) or len(unresolved) > 16
                or not isinstance(reply.get('plan'), dict)
                or any(not isinstance(x, str) for x in results + unresolved)):
            raise ValueError('invalid engine goal evidence')
        if reply['state'] == 'failed' and (not isinstance(reply.get('error'), str) or not reply['error']):
            raise ValueError('failed goal requires an explicit error')
        if reply['state'] == 'completed' and reply.get('error') is not None:
            raise ValueError('completed goal cannot contain an error')
        if reply['state'] == 'completed' and unresolved:
            raise ValueError('completed goal has unresolved effects')
        state = reply['state']
        if state == 'pending':
            state = 'accepted' if attempts == 0 else 'running'
        if state == 'failed' and reply.get('error') == 'retry_exhausted':
            state = 'budget_exhausted'
        stopped = state in {'completed', 'failed', 'budget_exhausted'}
        return {'requestId': request_id, 'engine': self.identity, 'simulated': False,
                'state': state, 'stopped': stopped,
                'reason': (reply.get('error') or 'engine goal effects acknowledged') if stopped else None,
                'progress': {'completed': int(state == 'completed'), 'total': 1},
                'attempts': attempts, 'resultIds': results, 'unresolvedEffects': unresolved,
                'evidence': [{'documentId': value, 'readback': 'not fetched by terminal'} for value in results],
                'documents': [], 'enginePlan': reply['plan'], 'actorCompletion': 'not established',
                'notice': 'Engine goal completion is effect acceptance; actor execution/completion is separate.'}
