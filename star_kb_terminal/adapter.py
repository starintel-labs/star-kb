"""Minimal engine port. The terminal never schedules or executes actors."""
from typing import Protocol

STATES = {'accepted', 'running', 'completed', 'failed', 'cancelled', 'blocked', 'budget_exhausted'}
STOPPED = STATES - {'accepted', 'running'}


class Engine(Protocol):
    identity: str
    simulated: bool

    def submit(self, plan: dict, request_id: str) -> dict: ...
    def status(self, request_id: str) -> dict: ...
    def results(self, request_id: str) -> dict: ...


def validate_reply(reply, request_id, engine, canonical):
    if not isinstance(reply, dict) or reply.get('requestId') != request_id:
        raise ValueError('engine reply identity mismatch')
    if reply.get('engine') != engine.identity or reply.get('simulated') is not engine.simulated:
        raise ValueError('engine reply provenance mismatch')
    state = reply.get('state')
    if state not in STATES or reply.get('stopped') is not (state in STOPPED):
        raise ValueError('invalid engine stopping state')
    if state in STOPPED and (not isinstance(reply.get('reason'), str) or not reply['reason']):
        raise ValueError('terminal engine state requires a reason')
    progress = reply.get('progress')
    if not isinstance(progress, dict) or any(type(progress.get(k)) is not int for k in ('completed', 'total')):
        raise ValueError('invalid engine progress')
    if not 0 <= progress['completed'] <= progress['total']:
        raise ValueError('invalid engine progress')
    if state == 'completed' and progress['completed'] != progress['total']:
        raise ValueError('completed state requires completed progress')
    if not isinstance(reply.get('documents', []), list):
        raise ValueError('engine documents must be an array')
    for doc in reply.get('documents', []):
        canonical.validate(doc)
    if not isinstance(reply.get('evidence'), list):
        raise ValueError('engine evidence must be explicit')
    return reply


class Unavailable:
    identity = 'unconfigured'
    simulated = False

    def submit(self, plan, request_id):
        raise ValueError('engine adapter unavailable; select --adapter simulated only for tests')

    def status(self, request_id):
        raise ValueError('engine adapter unavailable; cached acceptance is not completion')

    results = status
