"""Existing engine integration; HTTP storage is an explicit protocol fixture.

The double test is not inference proof; the real worker test requires SBCL/SWI.
Neither test claims actual StarIntel/CouchDB durability or actor completion.
"""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = os.environ.get('STARLANG_ROOT')
ENGINE = bool(ROOT and importlib.util.find_spec('star_kb_service.proactive')
              and importlib.util.find_spec('test_proactive'))
REAL = ENGINE and all(os.environ.get(k) for k in ('STAR_KB_SBCL', 'STARLANG_SWI_EXECUTABLE'))


@unittest.skipUnless(ENGINE, 'requires engine PR 27 and its isolated canonical HTTP fixture')
class TerminalEngineTests(unittest.TestCase):
    def exercise(self, real):
        from test_proactive import CanonicalHttpFixture, QueryDouble, relation
        from star_kb_service.proactive import ProactiveEngine
        from star_kb_service.persistence import ServerDocuments
        from star_kb_service.projection import Snapshot
        from star_kb_service.runtime import Service, Principal
        from star_kb_service.transports import http_server
        from star_kb_terminal.cli import execute, parser
        from star_kb_terminal.workspace import atomic_write
        docs = [relation('ab', 'a', 'b'), relation('bc', 'b', 'c')]
        fixture = CanonicalHttpFixture(docs)
        self.addCleanup(fixture.close)
        store_url = 'http://127.0.0.1:' + str(fixture.server.server_port)
        store = ServerDocuments(ROOT, 'tenant-a', 'd', store_url, 'x' * 32,
                                allowed_types=('relation', 'task', 'claim', 'target'))
        snapshot = Snapshot(ROOT, 'tenant-a', 'd', docs)
        if real:
            from star_kb_service.engine import Engine
            factory = lambda snap: Engine(ROOT, os.environ['STAR_KB_SBCL'], os.environ['STARLANG_SWI_EXECUTABLE'], snap)
            health_engine = factory(snapshot)
        else:
            class HealthDouble:
                metadata = {}
                process = type('Process', (), {'poll': lambda self: None})()
                def start(self): pass
                def close(self): pass
            health_engine = HealthDouble()
            health_engine.snapshot = snapshot
            factory = QueryDouble
        service = Service({'x' * 32: Principal('operator', 'tenant-a', 'd')},
                          {('tenant-a', 'd'): health_engine},
                          {('tenant-a', 'd'): ProactiveEngine(store, factory)})
        server = http_server(service)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def close():
            server.shutdown(); server.server_close(); thread.join(3); service.close()
        self.addCleanup(close)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'TERMINAL_INTEGRATION_TOKEN': 'x' * 32}):
            path = Path(directory)
            atomic_write(path / 'sources.json', docs)
            base = ['--workspace', str(path / 'operator.json'), '--canonical-root', ROOT,
                    '--adapter', 'http', '--service-url', 'http://127.0.0.1:' + str(server.server_port),
                    '--token-env', 'TERMINAL_INTEGRATION_TOKEN', '--results-url', store_url,
                    '--results-token-env', 'TERMINAL_INTEGRATION_TOKEN']
            def call(*arguments):
                return execute(parser().parse_args(base + list(arguments)))
            self.assertIn('subsidiary-chain/1', call('capabilities')['rules'])
            call('load', str(path / 'sources.json'))
            call('investigate', 'ab', '--type', 'document-id')
            call('plan', '--capability', 'subsidiary-chain/1', '--source', 'ab', '--source', 'bc')
            accepted = call('submit'); key = accepted['requestId']
            self.assertEqual('accepted', accepted['state'])
            self.assertEqual('accepted', call('status', key)['state'])
            self.assertEqual('completed', call('advance', key)['state'])
            result = call('results', key)
            claim, = result['documents']
            self.assertEqual('claim', claim['dtype'])
            self.assertEqual('candidate', claim['verificationStatus'])
            self.assertEqual('unknown', claim['truthStatus'])
            self.assertEqual(['a'], claim['subjectRefs']); self.assertEqual(['c'], claim['objectRefs'])
            self.assertEqual('validated canonical 0.10.1', result['evidence'][0]['readback'])
            self.assertEqual('not established', result['actorCompletion'])
            service.proactive[('tenant-a', 'd')] = ProactiveEngine(store, factory)
            self.assertEqual(result, call('results', key))
            self.assertEqual('completed', call('advance', key)['state'])
            self.assertEqual(1, len([d for d in fixture.documents.values() if d['dtype'] == 'claim']))
            self.assertEqual(1, len(json.loads((path / 'operator.json').read_text())['submissions']))

    def test_service_and_proactive_engine_with_inference_double(self):
        self.exercise(False)

    @unittest.skipUnless(REAL, 'requires real SBCL/SWI; double test is not real inference evidence')
    def test_real_swi_terminal_goal_and_evidence_readback(self):
        self.exercise(True)
