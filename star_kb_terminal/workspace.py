"""Atomic operator workspace; canonical records are read-only imported snapshots."""
from contextlib import contextmanager
import fcntl
import json
import os
from pathlib import Path
import tempfile
from .contracts import read_json


def atomic_write(path, value):
    payload = json.dumps(value, sort_keys=True, indent=2, allow_nan=False)
    if len(payload.encode('utf-8')) > 262144:
        raise ValueError('workspace/test state exceeds 256 KiB; use a separate workspace')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.terminal-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def workspace(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(str(path) + '.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read_json(path) if path.exists() else {
            'version': 1, 'documents': {}, 'inputs': [], 'selected': None,
            'goal': None, 'plan': None, 'submissions': {}}
        if state.get('version') != 1:
            raise ValueError('unsupported workspace version')
        yield state
        atomic_write(path, state)
