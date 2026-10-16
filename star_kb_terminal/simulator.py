"""Explicit test adapter. No network, actors, model, canonical writes or discoveries.

Only advance() changes state. Polling never pretends to make execution progress.
"""
from pathlib import Path
from .workspace import workspace
from .contracts import digest
from .adapter import STOPPED


class SimulatedEngine:
    simulated = True

    def __init__(self, path):
        self.path = Path(path)
        self.identity = 'simulated:' + digest(str(self.path.resolve()))

    def submit(self, plan, request_id):
        with workspace(self.path) as data:
            runs = data.setdefault('runs', {})
            if request_id in runs:
                if runs[request_id]['planDigest'] != digest(plan):
                    raise ValueError('idempotency conflict')
            else:
                runs[request_id] = {'requestId': request_id, 'engine': self.identity,
                    'simulated': True, 'planDigest': digest(plan), 'state': 'accepted',
                    'stopped': False, 'reason': None, 'progress': {'completed': 0, 'total': len(plan['steps'])},
                    'evidence': [], 'documents': [], 'notice': 'SIMULATED; no actor executed'}
            return runs[request_id]

    def status(self, request_id):
        with workspace(self.path) as data:
            if request_id not in data.get('runs', {}):
                raise ValueError('unknown request; engine has no record (do not infer completion)')
            return data['runs'][request_id]

    results = status

    def advance(self, request_id, state):
        if state not in STOPPED | {'running'}:
            raise ValueError('invalid simulated state')
        with workspace(self.path) as data:
            run = data.get('runs', {}).get(request_id)
            if not run:
                raise ValueError('unknown request')
            if run['stopped'] and run['state'] != state:
                raise ValueError('terminal state is immutable')
            run.update(state=state, stopped=state in STOPPED,
                       reason='explicit test transition; no actor execution' if state in STOPPED else None)
            run['progress']['completed'] = run['progress']['total'] if state == 'completed' else 0
            return run
