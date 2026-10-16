#!/usr/bin/env python3
"""Terminal-only extension of the owned real-server/CouchDB/SWI harness.

Run after engine PR 27 is available. The engine and Docker lifecycle remain in
the existing harness. A separate engine checkout can be selected explicitly with
STAR_KB_ENGINE_TEST_ROOT for pre-merge integration evidence.
"""
import os
from pathlib import Path
import secrets
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

TERMINAL_ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = Path(os.environ.get('STAR_KB_ENGINE_TEST_ROOT', TERMINAL_ROOT)).resolve()
sys.path[:0] = [str(ENGINE_ROOT / 'service'), str(ENGINE_ROOT), str(TERMINAL_ROOT)]
import readback_integration as harness
from star_kb_service.proactive import ProactiveEngine
from star_kb_service.persistence import ServerDocuments
from star_kb_service.engine import Engine
from star_kb_service.runtime import Service, Principal
from star_kb_service.transports import http_server
from star_kb_terminal.cli import execute, parser
from star_kb_terminal.workspace import atomic_write


class TerminalDurableReadback(harness.DurableReadback):
    def test_terminal_claim_and_store_restart(self):
        client = ServerDocuments(self.root, 'tenant-a', 'proof-a', self.url, self.tokens['a'],
                                 allowed_types=('relation', 'task', 'claim', 'target'))
        docs = []
        for identifier, source, target in [('terminal:ab', 'a', 'b'), ('terminal:bc', 'b', 'c')]:
            doc = self.document(identifier)
            doc.update(source={'schema': 'organization', 'id': source},
                       destination={'schema': 'organization', 'id': target}, predicate='subsidiary-of')
            docs.append(client.create(doc))
        factory = lambda snap: Engine(self.root, os.environ['STAR_KB_SBCL'], os.environ['STARLANG_SWI_EXECUTABLE'], snap)
        token = secrets.token_urlsafe(32)
        service = Service({token: Principal('terminal', 'tenant-a', 'proof-a')},
                          {('tenant-a', 'proof-a'): factory(client.snapshot([d['id'] for d in docs]))},
                          {('tenant-a', 'proof-a'): ProactiveEngine(client, factory)})
        server = http_server(service)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        def close():
            server.shutdown(); server.server_close(); thread.join(3); service.close()
        self.addCleanup(close)
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
                'TERMINAL_PROOF_TOKEN': token, 'TERMINAL_PROOF_STORE_TOKEN': self.tokens['a']}):
            folder = Path(directory)
            atomic_write(folder / 'sources.json', docs)
            base = ['--workspace', str(folder / 'workspace.json'), '--canonical-root', self.root,
                    '--adapter', 'http', '--service-url', 'http://127.0.0.1:' + str(server.server_port),
                    '--token-env', 'TERMINAL_PROOF_TOKEN', '--results-url', self.url,
                    '--results-token-env', 'TERMINAL_PROOF_STORE_TOKEN']
            def call(*args):
                return execute(parser().parse_args(base + list(args)))
            call('load', str(folder / 'sources.json'))
            call('investigate', 'terminal:ab', '--type', 'document-id')
            call('plan', '--capability', 'subsidiary-chain/1', '--source', 'terminal:ab', '--source', 'terminal:bc')
            accepted = call('submit'); key = accepted['requestId']
            self.assertEqual('accepted', accepted['state'])
            self.assertEqual('completed', call('advance', key)['state'])
            before = call('results', key)
            claim, = before['documents']
            self.assertEqual('claim', claim['dtype']); self.assertEqual('candidate', claim['verificationStatus'])
            self.assertEqual(['a'], claim['subjectRefs']); self.assertEqual(['c'], claim['objectRefs'])
            self.assertEqual('not established', before['actorCompletion'])
            harness.docker('stop', '-t', '15', self.server)
            harness.docker('restart', '-t', '15', self.couch)
            self.wait_couch(); harness.docker('start', self.server); self.wait_server()
            service.proactive[('tenant-a', 'proof-a')] = ProactiveEngine(client, factory)
            self.assertEqual(before, call('results', key))
            self.assertEqual('completed', call('advance', key)['state'])
            self.assertEqual(before['resultIds'], call('results', key)['resultIds'])


if __name__ == '__main__':
    # Run this terminal case only, without rerunning inherited engine cases.
    suite = unittest.TestSuite([TerminalDurableReadback('test_terminal_claim_and_store_restart')])
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
