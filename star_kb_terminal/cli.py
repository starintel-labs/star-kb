"""Deterministic commands and a small interactive workspace/module terminal."""
import argparse
import json
import os
from pathlib import Path
import shlex
import sys
from .adapter import Unavailable, validate_reply
from star_kb_service.contracts import Fault
from .contracts import Canonical, digest, read_documents, read_json
from .inputs import goal, interpret
from .planner import candidates, plan
from .registry import Registry
from .simulator import SimulatedEngine
from .workspace import workspace


def parser():
    p = argparse.ArgumentParser(description='StarIntel Prolog operator terminal (no model required)')
    p.add_argument('--workspace', default='terminal-workspace.json')
    p.add_argument('--canonical-root', default=os.environ.get('STARLANG_ROOT'))
    p.add_argument('--registry', help='canonical 0.10.1 ActorManifest JSON/NDJSON export')
    p.add_argument('--adapter', choices=['unavailable', 'simulated'], default='unavailable')
    p.add_argument('--simulation-state', help='isolated test adapter state file')
    sub = p.add_subparsers(dest='command', required=True)
    for command in ['workspace', 'inputs', 'plan', 'submit', 'shell']:
        sub.add_parser(command)
    load = sub.add_parser('load'); load.add_argument('path')
    inp = sub.add_parser('input'); inp.add_argument('value'); inp.add_argument('--type')
    actors = sub.add_parser('actors'); actors.add_argument('--type')
    select = sub.add_parser('select'); select.add_argument('actor')
    investigate = sub.add_parser('investigate'); investigate.add_argument('value'); investigate.add_argument('--type')
    structured = sub.add_parser('goal'); structured.add_argument('path', help='structured goal JSON')
    for command in ['status', 'results']:
        s = sub.add_parser(command); s.add_argument('request_id')
    advance = sub.add_parser('simulate'); advance.add_argument('request_id'); advance.add_argument('state')
    return p


def validate_goal(value, documents, canonical):
    # Reinterpret untrusted model-produced input; only a narrow closed goal boundary.
    if (not isinstance(value, dict) or set(value) != {'protocol', 'intent', 'subject', 'limits', 'requestedText'}
            or value['protocol'] != 'star.kb.goal/1' or value['intent'] != 'investigate'
            or value['limits'] != {'maxSteps': 1}):
        raise ValueError('unsupported structured goal')
    subject = value['subject']
    if not isinstance(subject, dict) or subject.get('state') != 'interpreted':
        raise ValueError('structured goal needs an interpreted subject')
    kind = subject.get('kind')
    if kind == 'document-id' and subject.get('dtype'):
        kind = 'document:' + subject['dtype']
    checked = goal(value['requestedText'], documents, canonical, kind)
    if checked['subject'] != subject or value['requestedText'] != checked['requestedText']:
        raise ValueError('structured goal interpretation mismatch')
    return checked


def execute(args):
    if not args.canonical_root:
        raise ValueError('--canonical-root or STARLANG_ROOT is required')
    canonical = Canonical(args.canonical_root)
    registry = Registry.load(args.registry, canonical) if args.registry else Registry({})
    if args.adapter == 'simulated':
        if not args.simulation_state:
            raise ValueError('--simulation-state is required for simulated execution')
        if Path(args.simulation_state).resolve() == Path(args.workspace).resolve():
            raise ValueError('workspace and adapter state must be separate files')
        engine = SimulatedEngine(args.simulation_state)
    else:
        engine = Unavailable()
    with workspace(args.workspace) as state:
        command = args.command
        if command == 'workspace':
            return {'workspace': str(Path(args.workspace).resolve()), 'engine': engine.identity,
                    'simulated': engine.simulated, **state}
        if command == 'load':
            docs = read_documents(args.path, canonical)
            for key, doc in docs.items():
                if key in state['documents'] and state['documents'][key] != doc:
                    raise ValueError('conflicting canonical document id; use another workspace')
            state['documents'].update(docs)
            state['plan'] = None
            return {'loaded': list(docs), 'notice': 'read-only snapshots; not canonical publication'}
        if command == 'input':
            value = interpret(args.value, state['documents'], canonical, args.type)
            state['inputs'].append(value)
            return value
        if command == 'inputs':
            return {'inputs': state['inputs'], 'documentIds': list(state['documents'])}
        if command == 'actors':
            return {'registryDigest': registry.identity,
                    'candidates': candidates(registry, args.type), 'manifests': list(registry.actors.values())}
        if command == 'select':
            if args.actor not in registry.actors:
                raise ValueError('actor not found in supplied registry')
            state['selected'], state['plan'] = args.actor, None
            return {'selected': args.actor, **registry.actors[args.actor]}
        if command in {'investigate', 'goal'}:
            state['goal'] = (goal('investigate ' + args.value, state['documents'], canonical, args.type)
                if command == 'investigate' else validate_goal(read_json(args.path), state['documents'], canonical))
            state['plan'] = None
            return state['goal']
        if command in {'plan', 'submit'}:
            if not state['goal']:
                raise ValueError('create an investigate goal first')
            current = plan(state['goal'], registry, state['selected'])
            if command == 'plan':
                state['plan'] = current
                return current
            if state['plan'] != current:
                raise ValueError('plan missing or stale; inspect plan before submission')
            if current['state'] != 'planned':
                raise ValueError('blocked plan cannot be submitted')
            # Persist before I/O: restart or lost response must retry the same ID.
            request_id = digest({'workspace': str(Path(args.workspace).resolve()),
                                 'plan': current['id'], 'engine': engine.identity})
            pending = {'engine': engine.identity, 'simulated': engine.simulated,
                       'planId': current['id'], 'state': 'submission_unknown'}
            state['submissions'].setdefault(request_id, pending)
            from .workspace import atomic_write
            atomic_write(args.workspace, state)
            reply = validate_reply(engine.submit(current, request_id), request_id, engine, canonical)
            state['submissions'][request_id] = {**pending, 'state': reply['state'], 'lastObserved': reply}
            return reply
        if command in {'status', 'results', 'simulate'}:
            previous = state['submissions'].get(args.request_id)
            if not previous or previous['engine'] != engine.identity:
                raise ValueError('request does not belong to this workspace/engine')
            if command == 'simulate':
                if not engine.simulated:
                    raise ValueError('simulate requires the explicit test adapter')
                reply = engine.advance(args.request_id, args.state)
            else:
                reply = getattr(engine, command)(args.request_id)
            reply = validate_reply(reply, args.request_id, engine, canonical)
            state['submissions'][args.request_id]['lastObserved'] = reply
            state['submissions'][args.request_id]['state'] = reply['state']
            return reply
        raise ValueError('unknown command')


def emit(value):
    # ASCII JSON escapes terminal controls and untrusted evidence text.
    print(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=True))


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == 'shell':
        print('StarIntel operator terminal. Commands: workspace load input inputs actors select investigate goal plan submit status results. help / quit')
        base = ['--workspace', args.workspace, '--adapter', args.adapter]
        for key in ('canonical_root', 'registry', 'simulation_state'):
            if getattr(args, key):
                base += ['--' + key.replace('_', '-'), getattr(args, key)]
        while True:
            try:
                line = input('star-kb> ')
                if line.strip() in {'quit', 'exit'}:
                    return 0
                if not line.strip():
                    continue
                if line.strip() == 'help':
                    parser().print_help(); continue
                tokens = shlex.split(line)
                if tokens[0] == 'shell':
                    raise ValueError('nested shell is not supported')
                main(base + tokens)
            except EOFError:
                return 0
            except KeyboardInterrupt:
                print(); continue
            except (ValueError, SystemExit) as exc:
                print('command rejected: ' + str(exc), file=sys.stderr)
    try:
        emit(execute(args))
        return 0
    except (ValueError, OSError, KeyError, TypeError, Fault) as exc:
        print(json.dumps({'error': str(exc), 'completion': 'not established'}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
