"""Actual document projection -> SWI query -> existing HTTP dispatcher proof."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.request

ROOT=os.environ.get('STARLANG_ROOT')


@unittest.skipUnless(ROOT and shutil.which('swipl'),'actual schema and SWI required by learning acceptance gate')
class KnowledgeActorTests(unittest.TestCase):
    def setUp(self):
        from star_kb_learning.contracts import Scope
        from star_kb_service.actors import KnowledgeActors
        from star_kb_service.runtime import Principal,Service
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.actor=KnowledgeActors(ROOT,Scope('demo','replay','demo'),Path(self.temp.name)/'actor.sqlite')
        self.token='synthetic-operator-token-'+'x'*32
        self.service=Service({self.token:Principal('actor-client','tenant','demo',('document-project','prolog-query'))},{},
                             {('tenant','demo'):self.actor})
        self.addCleanup(self.service.close)

    def request(self,op,parameters=None,identifier='one',generation=None,token=None):
        from star_kb_service.contracts import encode
        value={'protocol':'star.kb.service/1','id':identifier,'operation':op}
        if parameters is not None:value['parameters']=parameters
        if generation is not None:value['generation']=generation
        return self.service.handle(token or self.token,encode(value))

    def document(self):
        from star_kb_learning.examples import document
        return document('doc','sharedSource','release-one')

    def selector(self):
        return {'name':'recorded-language','version':1,'purpose':'recorded language metadata',
                'expression':{'field':['/language','en']}}

    def test_projection_query_replay_generation_and_provenance(self):
        projection=self.request('document-project',{'document':self.document()})
        self.assertEqual(projection['state'],'complete')
        self.assertTrue(any("'/language'" in f for f in projection['facts']))
        self.assertFalse(projection['durableCanonicalWrite'])
        self.assertEqual(projection['discoveryEventsProcessed'],1)
        replay=self.request('document-project',{'document':self.document()})
        self.assertEqual(projection,replay)
        result=self.request('prolog-query',{'selector':self.selector()},'query',projection['generation'])
        self.assertEqual(result['execution'],'complete')
        self.assertEqual(result['rows'][0]['membership'],'supported')
        self.assertEqual(result['evidenceDetails']['doc']['documentId'],'doc')
        stale=self.request('prolog-query',{'selector':self.selector()},'stale','wrong')
        self.assertEqual(stale['error'],'stale_generation')

    def test_capabilities_schema_scope_and_goal_injection(self):
        from star_kb_service.runtime import Principal
        self.service.credentials['read-only-'+'y'*32]=Principal('reader','tenant','demo')
        r=self.request('document-project',{'document':self.document()},token='read-only-'+'y'*32)
        self.assertEqual(r['error'],'capability_denied')
        d=self.document();d['dataset']='foreign'
        self.assertEqual(self.request('document-project',{'document':d},'foreign')['error'],'scope_mismatch')
        s=self.selector();s['expression']={'call':['shell','id']}
        r=self.request('prolog-query',{'selector':s},'raw',self.actor.generation)
        self.assertEqual(r['error'],'prohibited_capability')
        self.assertEqual(self.request('health',identifier='health',token='bad')['error'],'unauthorized')

    def test_http_wire_uses_same_dispatcher_and_actual_swi(self):
        from star_kb_service.transports import http_server
        server=http_server(self.service);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def post(req):
                r=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+'/v1/kb',
                    data=json.dumps(req).encode(),headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
                with opener.open(r,timeout=5) as response:return json.load(response)
            projected=post({'protocol':'star.kb.service/1','id':'http-project','operation':'document-project',
                            'parameters':{'document':self.document()}})
            result=post({'protocol':'star.kb.service/1','id':'http-query','operation':'prolog-query',
                         'generation':projected['generation'],'parameters':{'selector':self.selector()}})
            self.assertEqual(result['rows'][0]['membership'],'supported')
            self.assertEqual(result['execution'],'complete')
        finally:server.shutdown();server.server_close();thread.join(1)

    def test_restart_rebuilds_scoped_projection(self):
        from star_kb_service.actors import KnowledgeActors
        self.request('document-project',{'document':self.document()})
        generation=self.actor.generation
        # A second handler reads the canonical replay cache; no source re-write.
        other=KnowledgeActors(ROOT,self.actor.scope,Path(self.temp.name)/'actor.sqlite')
        try:
            self.assertEqual(other.generation,generation)
            r=other.invoke('prolog-query',{'selector':self.selector()},generation)
            self.assertEqual(r['execution'],'complete')
        finally:other.close()

    def test_projection_revision_compare_and_swap(self):
        from star_kb_learning.contracts import digest
        original=self.document();self.request('document-project',{'document':original})
        changed={**original,'language':'fr'}
        r=self.request('document-project',{'document':changed},'unleased')
        self.assertEqual(r['error'],'projection_revision_conflict')
        r=self.request('document-project',{'document':changed,'previousDigest':digest(original)},'leased')
        self.assertEqual(r['state'],'complete')
        r=self.request('document-project',{'document':original},'stale-redelivery')
        self.assertEqual(r['error'],'projection_revision_conflict')
        self.assertEqual(len(self.actor.manifests()),2)

    def test_tenant_namespace_even_when_project_names_and_path_match(self):
        from star_kb_service.actors import KnowledgeActors
        from star_kb_learning.contracts import Scope
        path=Path(self.temp.name)/'shared-cache.sqlite'
        first=KnowledgeActors(ROOT,Scope('demo','replay','demo',tenant='one'),path)
        second=KnowledgeActors(ROOT,Scope('demo','replay','demo',tenant='two'),path)
        try:
            first.invoke('document-project',{'document':self.document()})
            self.assertEqual(second.store.documents(),[])
            self.assertNotEqual(first.generation,second.generation)
        finally:first.close();second.close()

    def test_actual_http_only_cli_without_sbcl(self):
        import selectors
        config={'scopes':[{'tenant':'tenant','dataset':'demo','learning':{
            'project':'demo','operation':'replay','path':str(Path(self.temp.name)/'cli.sqlite'),
            'documents':[self.document()]}}],
            'credentials':[{'identity':'test','tenant':'tenant','dataset':'demo','tokenEnv':'KB_SYNTHETIC_ACTOR_TOKEN',
                            'capabilities':['document-project','prolog-query']}]}
        path=Path(self.temp.name)/'config.json';path.write_text(json.dumps(config))
        env={**os.environ,'STARLANG_ROOT':ROOT,'KB_SYNTHETIC_ACTOR_TOKEN':self.token}
        env.pop('STAR_KB_SBCL',None)
        process=subprocess.Popen([sys.executable,'-m','star_kb_service',str(path),'--http-port','0','--http-only'],
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,text=True)
        try:
            with selectors.DefaultSelector() as select:
                select.register(process.stdout,selectors.EVENT_READ)
                self.assertTrue(select.select(5),'service startup deadline')
                addresses=json.loads(process.stdout.readline())
            self.assertIsNone(addresses['zmq'])
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            request=urllib.request.Request(addresses['http'],data=json.dumps({'protocol':'star.kb.service/1','id':'cli-health','operation':'health'}).encode(),
                headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
            with opener.open(request,timeout=3) as r:health=json.load(r)
            self.assertEqual(health['canonicalSchema'],'0.10.1')
        finally:
            process.terminate();process.communicate(timeout=5)
        self.assertEqual(process.returncode,0)
