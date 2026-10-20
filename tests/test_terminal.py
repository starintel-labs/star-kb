"""Synthetic operator/Prolog/adapter contract tests; no provider or model calls."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_ROOT = os.environ.get('STARLANG_ROOT', str(ROOT.parent / 'star-lang'))
SWIPL = os.environ.get('STAR_KB_TERMINAL_SWIPL', shutil.which('swipl'))
FIXTURES = Path(__file__).parent / 'fixtures/terminal'
AVAILABLE = Path(CANONICAL_ROOT, 'specs/starintel/0.10.1/generated/schema.json').exists()


if AVAILABLE:
    from star_kb_terminal.adapter import validate_reply
    from star_kb_terminal.cli import execute, parser, validate_goal
    from star_kb_terminal.contracts import Canonical
    from star_kb_terminal.inputs import goal, interpret
    from star_kb_terminal.registry import Registry
    from star_kb_terminal.simulator import SimulatedEngine
    from star_kb_terminal.workspace import atomic_write


@unittest.skipUnless(AVAILABLE and SWIPL, 'terminal requires pinned schema and SWI; Nix terminal check supplies both')
class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.workspace = self.dir / 'workspace.json'
        self.engine = self.dir / 'engine.json'
        self.registry = self.dir / 'registry.json'
        shutil.copyfile(FIXTURES / 'registry.json', self.registry)
        self.canonical = Canonical(CANONICAL_ROOT)
        self.base = ['--workspace', str(self.workspace), '--canonical-root', CANONICAL_ROOT,
                     '--registry', str(self.registry), '--adapter', 'simulated',
                     '--simulation-state', str(self.engine)]
        self.environ = patch.dict(os.environ, {'STAR_KB_TERMINAL_SWIPL': SWIPL})
        self.environ.start(); self.addCleanup(self.environ.stop)

    def run_command(self, *args):
        return execute(parser().parse_args(self.base + list(args)))

    def ready(self):
        self.run_command('select', 'fixture-domain')
        self.run_command('investigate', 'example.invalid')
        return self.run_command('plan')

    def test_inference_and_ambiguity(self):
        for text, kind in [('https://example.invalid/a', 'url'), ('example.invalid', 'domain'),
                           ('192.0.2.1', 'ip'), ('2001:db8::1', 'ip'), ('starintel:domain:fixture', 'document-id')]:
            self.assertEqual(kind, interpret(text, {}, self.canonical)['kind'])
        self.assertEqual('unresolved', interpret('Ada Example', {}, self.canonical)['state'])
        self.assertEqual('unresolved', interpret('https://user:pass@example.invalid', {}, self.canonical)['state'])
        docs = {'example.invalid': {'dtype': 'domain'}}
        self.assertEqual('ambiguous', interpret('example.invalid', docs, self.canonical)['state'])
        self.assertEqual('document-id', interpret('example.invalid', docs, self.canonical, 'document-id')['kind'])
        with self.assertRaises(ValueError):
            interpret('not an id', {}, self.canonical, 'document:person')

    def test_review_malformed_input_and_normalized_goal_roundtrip(self):
        for text in ['starintel:has spaces', 'https://example invalid/a']:
            self.assertEqual('unresolved', interpret(text, {}, self.canonical)['state'])
        for text in ['EXAMPLE.INVALID', 'example.invalid.', '2001:0db8::1']:
            value = goal('investigate ' + text, {}, self.canonical)
            self.assertEqual(value, validate_goal(value, {}, self.canonical))

    def test_review_reply_shape_and_completion_progress(self):
        self.ready(); reply = self.run_command('submit')
        engine = SimulatedEngine(self.engine)
        for documents in [{}, '', None]:
            bad = {**reply, 'documents': documents}
            with self.assertRaises(ValueError):
                validate_reply(bad, reply['requestId'], engine, self.canonical)
        bad = {**reply, 'state': 'completed', 'stopped': True, 'reason': 'test'}
        with self.assertRaises(ValueError):
            validate_reply(bad, reply['requestId'], engine, self.canonical)

    def test_prolog_registry_compatibility_and_stale_plan(self):
        rows = {x['actor']: x['compatibility'] for x in self.run_command('actors', '--type', 'domain')['candidates']}
        self.assertEqual(rows, {'fixture-domain': 'compatible', 'fixture-url': 'incompatible', 'fixture-unknown': 'unknown'})
        planned = self.ready()
        self.assertEqual('planned', planned['state'])
        registry = json.loads(self.registry.read_text())
        registry[0]['produces'] = ['document']
        atomic_write(self.registry, registry)
        with self.assertRaisesRegex(ValueError, 'stale'):
            self.run_command('submit')
        self.assertFalse(self.engine.exists())

    def test_no_selection_or_unknown_type_blocks(self):
        self.run_command('investigate', 'example.invalid')
        self.assertEqual('blocked', self.run_command('plan')['state'])
        self.run_command('select', 'fixture-domain')
        self.run_command('investigate', '192.0.2.1')
        self.assertEqual('blocked', self.run_command('plan')['state'])
        with self.assertRaises(ValueError):
            self.run_command('submit')

    def test_acceptance_reconnect_restart_and_idempotent_submit(self):
        self.ready()
        first = self.run_command('submit')
        key = first['requestId']
        self.assertEqual('accepted', first['state'])
        self.assertFalse(first['stopped'])
        self.assertEqual(first, self.run_command('submit'))
        self.assertEqual(first, self.run_command('status', key))
        # New CLI processes are a real client restart; test adapter remains explicit.
        output = self.cli('results', key)
        self.assertEqual(0, output.returncode, output.stderr)
        self.assertEqual('accepted', json.loads(output.stdout)['state'])
        self.run_command('simulate', key, 'running')
        complete = self.run_command('simulate', key, 'completed')
        self.assertTrue(complete['simulated']); self.assertEqual([], complete['evidence'])
        self.assertEqual([], complete['documents'])
        with self.assertRaisesRegex(ValueError, 'immutable'):
            self.run_command('simulate', key, 'failed')

    def test_lost_reply_reuses_id_after_restart(self):
        self.ready()
        submit = SimulatedEngine.submit
        def lost(engine, plan, key):
            submit(engine, plan, key)
            raise OSError('synthetic lost reply')
        with patch.object(SimulatedEngine, 'submit', lost):
            with self.assertRaises(OSError):
                self.run_command('submit')
        state = json.loads(self.workspace.read_text())
        key = next(iter(state['submissions']))
        self.assertEqual('submission_unknown', state['submissions'][key]['state'])
        self.assertEqual(key, self.run_command('submit')['requestId'])
        self.assertEqual(1, len(json.loads(self.engine.read_text())['runs']))

    def test_engine_state_loss_is_not_completion(self):
        self.ready(); reply = self.run_command('submit')
        self.engine.unlink()
        with self.assertRaisesRegex(ValueError, 'unknown request'):
            self.run_command('status', reply['requestId'])
        state = json.loads(self.workspace.read_text())
        self.assertEqual('accepted', state['submissions'][reply['requestId']]['lastObserved']['state'])

    def test_boundary_rejects_false_completion_and_evidence(self):
        self.ready(); reply = self.run_command('submit')
        engine = SimulatedEngine(self.engine)
        bad = copy.deepcopy(reply); bad['state'] = 'completed'
        with self.assertRaises(ValueError):
            validate_reply(bad, reply['requestId'], engine, self.canonical)
        bad = copy.deepcopy(reply); bad['documents'] = [{'_id': 'legacy'}]
        with self.assertRaises(ValueError):
            validate_reply(bad, reply['requestId'], engine, self.canonical)
        bad = copy.deepcopy(reply); bad['simulated'] = False
        with self.assertRaises(ValueError):
            validate_reply(bad, reply['requestId'], engine, self.canonical)

    def test_structured_goal_boundary_no_llm_or_arbitrary_goals(self):
        value = goal('investigate example.invalid', {}, self.canonical)
        self.assertEqual(value, validate_goal(value, {}, self.canonical))
        bad = {**value, 'requestedText': None}
        with self.assertRaises(ValueError):
            validate_goal(bad, {}, self.canonical)
        value['prolog'] = 'halt(9)'
        with self.assertRaises(ValueError):
            validate_goal(value, {}, self.canonical)
        self.assertEqual('unresolved', self.run_command('investigate', 'halt(9)')['subject']['state'])

    def test_canonical_import_preserves_extensions_and_rejects_legacy(self):
        doc = {'id': 'starintel:domain:test', 'dtype': 'domain', 'dataset': 'synthetic',
               'schemaVersion': '0.10.1', 'name': 'example.invalid',
               'extensions': {'test': {'nested': [1, True]}}}
        path = self.dir / 'doc.json'; atomic_write(path, doc)
        self.run_command('load', str(path))
        self.assertEqual(doc, self.run_command('workspace')['documents'][doc['id']])
        self.assertEqual('domain', self.run_command('input', doc['id'])['dtype'])
        doc['name'] = 'other.invalid'; atomic_write(path, doc)
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            self.run_command('load', str(path))
        atomic_write(path, {'_id': 'legacy', 'dtype': 'domain', 'data': {}})
        with self.assertRaises(ValueError):
            self.run_command('load', str(path))

    def test_registry_duplicate_actor_rejected(self):
        docs = json.loads(self.registry.read_text())
        docs[1]['actor'] = docs[0]['actor']; atomic_write(self.registry, docs)
        with self.assertRaisesRegex(ValueError, 'duplicate actor'):
            Registry.load(self.registry, self.canonical)

    def cli(self, *args, input=None):
        installed = os.environ.get('STAR_KB_TERMINAL_COMMAND')
        command = [installed] if installed else [sys.executable, '-m', 'star_kb_terminal.cli']
        pythonpath = os.environ.get('PYTHONPATH', '')
        if not installed:
            pythonpath = str(ROOT) + os.pathsep + pythonpath
        env = {**os.environ, 'PYTHONPATH': pythonpath}
        return subprocess.run(command + self.base + list(args), text=True, input=input,
                              capture_output=True, timeout=10, cwd=self.dir, env=env)

    def test_cli_shell_errors_and_restart(self):
        output = self.cli('shell', input='select fixture-domain\ninvestigate example.invalid\nplan\nquit\n')
        self.assertEqual(0, output.returncode, output.stderr)
        self.assertIn('"state": "planned"', output.stdout)
        self.assertEqual(2, self.cli('select', 'absent').returncode)
        self.assertEqual(0, self.cli('workspace').returncode)

    def test_unconfigured_engine_fails_closed(self):
        self.ready()
        self.base[self.base.index('simulated')] = 'unavailable'
        with self.assertRaisesRegex(ValueError, 'unavailable'):
            self.run_command('submit')
        self.assertFalse(self.engine.exists())


if __name__ == '__main__':
    unittest.main()


@unittest.skipUnless(AVAILABLE and SWIPL, 'terminal requires pinned schema and SWI')
class ServiceAdapterTests(unittest.TestCase):
    """Real HTTP sockets against a protocol fixture, NOT a real engine/store."""
    setUp = TerminalTests.setUp
    run_command = TerminalTests.run_command
    cli = TerminalTests.cli

    def start_fixture(self):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        import threading
        self.requests, self.runs = [], {}
        fixture = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_GET(self):
                body = json.dumps({'id': 'fixture:claim', 'rev': '1-fixture', 'dataset': 'synthetic',
                    'dtype': 'document', 'schemaVersion': '0.10.1',
                    'provenance': {'collector': 'synthetic-protocol-fixture'}}).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(body)))
                self.end_headers(); self.wfile.write(body)
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                fixture.requests.append(request)
                if self.headers.get('Authorization') != 'Bearer ' + 'x' * 32:
                    self.send_response(401); self.end_headers(); return
                op = request['operation']
                result = {'state': 'ready'}
                if op == 'health':
                    result['scope'] = {'tenant': 'synthetic', 'dataset': 'synthetic'}
                elif op == 'actor-capabilities':
                    result.update(interface='star.kb.goals/1', rules=['fixture-rule/1'],
                                  actors={}, limits={'sources': 16, 'attempts': 3})
                else:
                    key = request['parameters']['goal']
                    if op == 'goal-submit':
                        fixture.runs.setdefault(key, {'state': 'pending', 'goal': key,
                            'plan': request['parameters'], 'attempts': 0, 'results': [],
                            'unresolvedEffects': [], 'error': None})
                    if key not in fixture.runs:
                        result = {'state': 'failed', 'error': 'store_not_found'}
                    else:
                        if op == 'goal-advance':
                            fixture.runs[key].update(state='completed', attempts=1, results=['fixture:claim'])
                        result = fixture.runs[key]
                body = json.dumps({'protocol': 'star.kb.service/1', 'id': request['id'], **result}).encode()
                self.send_response(200); self.send_header('Content-Length', str(len(body)))
                self.end_headers(); self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def close():
            server.shutdown(); server.server_close(); thread.join(2)
        self.addCleanup(close)
        env = patch.dict(os.environ, {'TERMINAL_TEST_TOKEN': 'x' * 32}); env.start(); self.addCleanup(env.stop)
        self.base[self.base.index('simulated')] = 'http'
        self.base += ['--service-url', 'http://127.0.0.1:' + str(server.server_port), '--token-env', 'TERMINAL_TEST_TOKEN']

    def prepare(self):
        doc = {'id': 'fixture:relation', 'rev': '1-fixture', 'dtype': 'relation',
               'dataset': 'synthetic', 'schemaVersion': '0.10.1',
               'source': {'schema': 'organization', 'id': 'a'},
               'destination': {'schema': 'organization', 'id': 'b'}, 'predicate': 'subsidiary-of'}
        file = self.dir / 'relation.json'; atomic_write(file, doc)
        self.run_command('load', str(file))
        self.run_command('investigate', doc['id'], '--type', 'document-id')
        return self.run_command('plan', '--capability', 'fixture-rule/1', '--source', doc['id'])

    def test_http_discovery_submit_poll_advance_and_cli_restart(self):
        self.start_fixture(); planned = self.prepare()
        self.assertEqual('fixture-rule/1', planned['engineGoal']['capability'])
        accepted = self.run_command('submit'); key = accepted['requestId']
        self.assertEqual('accepted', accepted['state']); self.assertFalse(accepted['simulated'])
        self.assertEqual('accepted', self.run_command('status', key)['state'])
        self.assertEqual(1, len(self.runs))
        self.assertEqual(key, self.run_command('submit')['requestId'])
        done = self.run_command('advance', key)
        self.assertEqual('completed', done['state'])
        self.assertEqual('not established', done['actorCompletion'])
        self.assertEqual([], done['documents'])
        self.assertEqual('not fetched by terminal', done['evidence'][0]['readback'])
        output = self.cli('results', key)
        self.assertEqual(0, output.returncode, output.stderr)
        self.assertEqual('completed', json.loads(output.stdout)['state'])
        self.assertEqual(len(self.requests), len({r['id'] for r in self.requests}))
        self.base += ['--results-url', self.base[self.base.index('--service-url') + 1],
                      '--results-token-env', 'TERMINAL_TEST_TOKEN']
        readback = self.run_command('results', key)
        self.assertEqual('1-fixture', readback['documents'][0]['rev'])
        self.assertEqual('validated canonical 0.10.1', readback['evidence'][0]['readback'])
        # A fresh status request cannot replay a stale service response.
        self.runs.clear()
        with self.assertRaisesRegex(ValueError, 'store_not_found'):
            self.run_command('status', key)

    def test_malformed_failed_reply_does_not_claim_acknowledgement(self):
        from star_kb_terminal.service_adapter import ServiceEngine
        engine = object.__new__(ServiceEngine); engine.identity = 'fixture'
        reply = {'goal': 'x', 'state': 'failed', 'attempts': 1, 'results': [],
                 'unresolvedEffects': [], 'plan': {}, 'error': None}
        with self.assertRaisesRegex(ValueError, 'explicit error'):
            engine.convert(reply, 'x')

    def test_http_policy_and_credentials_fail_closed(self):
        self.start_fixture()
        with patch.dict(os.environ, {'TERMINAL_TEST_TOKEN': 'z' * 32}):
            with self.assertRaises(Exception):
                self.run_command('capabilities')
        self.prepare()
        with self.assertRaisesRegex(ValueError, 'not advertised'):
            self.run_command('plan', '--capability', 'invented-rule', '--source', 'fixture:relation')
        self.run_command('select', 'fixture-domain')
        with self.assertRaisesRegex(ValueError, 'Target compatibility'):
            self.run_command('plan', '--capability', 'fixture-rule/1', '--source', 'fixture:relation')
