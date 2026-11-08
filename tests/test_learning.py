import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import unittest
from star_kb_learning.contracts import CanonicalAdapter, LearningError, Limits, Scope
from star_kb_learning.store import Store
from star_kb_learning.discovery import Discovery
from star_kb_learning.examples import document, example
from star_kb_learning.planning import Capability, Planner, ReplayCollector
from star_kb_learning.runtime import Runtime
from star_kb_learning.selectors import Selectors, compile_expression

ROOT=os.environ.get('STARLANG_ROOT')


class GrammarTests(unittest.TestCase):
    def test_capability_boundary(self):
        for expr in ({'call':['shell','id']},{'field':['/x',{}]},{'reachable':['p','x',999]}, {'learned':['missing',1,'x']}):
            with self.assertRaises(LearningError):compile_expression(expr,{})
        with self.assertRaises(LearningError):Limits(steps=True)
        with self.assertRaises(LearningError):Limits(seconds=float('nan'))


@unittest.skipUnless(ROOT,'canonical schema required; scripts/verify-learning.py makes this mandatory')
class LearningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'learn.sqlite'
        self.scope=Scope('demo','replay','demo');self.limits=Limits(steps=100000)
        self.adapter=CanonicalAdapter(ROOT);self.store=Store(self.path,self.scope,self.limits)

    def tearDown(self):self.store.close();self.temp.cleanup()
    def add(self,i='a',group='g',**kw):
        d=document(i,'upstream',group,**kw);self.store.ingest(self.adapter,d);return d
    def selectors(self):
        if not shutil.which('swipl'):self.skipTest('SWI required by mandatory learning gate')
        return Selectors(self.store)
    def query(self,expression,version=1,name='test'):
        s=self.selectors();return s.evaluate(s.create(name,version,expression,'test'))

    def test_canonical_scope_and_legacy(self):
        d=document('a','f','v');d['dataset']='foreign'
        with self.assertRaisesRegex(LearningError,'scope'):self.store.ingest(self.adapter,d)
        d=document('a','f','v');d['extensions']['starKbScope']['project']='foreign'
        with self.assertRaisesRegex(LearningError,'scope'):self.store.ingest(self.adapter,d)
        with self.assertRaisesRegex(LearningError,'adapter'):self.store.ingest(self.adapter,{'_id':'a'})
        d=document('a','f','v');d['unknown']=1
        with self.assertRaisesRegex(LearningError,'canonical'):self.store.ingest(self.adapter,d)

    def test_observations_claims_revision_and_export(self):
        d=self.add();self.assertFalse(self.store.ingest(self.adapter,d))
        rid=self.store.record('hypothesis',{'explanation':'candidate only'},['a'])
        out=self.store.export(rid);self.adapter.validate(out,self.scope,self.limits)
        self.assertEqual(self.store.dependencies(rid)[0]['doc'],'a')
        d['rawContent']='corrected';self.store.ingest(self.adapter,d)
        self.assertEqual(self.store.get(rid)['state'],'stale')
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM history').fetchone()[0],2)
        self.assertTrue(self.store.db.execute("SELECT 1 FROM observations WHERE path='/rawContent'").fetchone())

    def test_unknown_exclusion_composition_and_explanations(self):
        self.add();self.add('b',area='CA')
        r=self.query({'all':[{'field':['/language','en']},{'field':['/regionCode','OH']}]})
        self.assertEqual(r['execution'],'complete')
        a,b=r['rows'];self.assertEqual(a['membership'],'supported');self.assertEqual(a['evidence'],['a'])
        self.assertEqual(b['membership'],'unresolved')
        r=self.query({'exclude':{'field':['/missing',True]}},name='unknown')
        self.assertTrue(all(x['membership']=='unresolved' for x in r['rows']))
        self.assertEqual(self.query({'during':['/createdAt',0,1]},name='time')['rows'][0]['membership'],'supported')
        self.assertEqual(self.query({'count':[{'field':['/language','en']},2]},name='count')['rows'][0]['membership'],'supported')

    def test_attribution_conflict_and_retraction(self):
        for identifier,polarity in [('positive','positive'),('negative','negative')]:
            d={'id':identifier,'dtype':'claim','dataset':'demo','schemaVersion':'0.10.1',
                'claim':'Source statement, not adjudicated reality.','claimantId':'speaker',
                'predicate':'advocates','object':'conduct','polarity':polarity,
                'extensions':{'starKbScope':{'project':'demo','operation':'replay'}}}
            self.store.ingest(self.adapter,d)
        expr={'attributed':['speaker','advocates','conduct']}
        result=self.query(expr)
        self.assertTrue(all(r['membership']=='conflicting' for r in result['rows']))
        d=self.store.document('negative');d['deleted']=True;self.store.ingest(self.adapter,d)
        self.assertEqual(self.store.get(result['record'])['state'],'stale')
        self.assertEqual(self.query(expr,name='after')['rows'][0]['membership'],'supported')

    def test_selector_version_immutable(self):
        self.add();s=self.selectors();s.create('v',1,{'field':['/language','en']},'test')
        with self.assertRaisesRegex(LearningError,'immutable'):s.create('v',1,{'field':['/language','fr']},'test')
        s.create('v',2,{'field':['/language','fr']},'test')

    def test_graph_direction_and_bounds(self):
        self.add('a');self.add('b')
        d={'id':'edge','dtype':'relation','dataset':'demo','schemaVersion':'0.10.1',
            'source':{'schema':'document','id':'a'},'destination':{'schema':'document','id':'b'},'predicate':'quotes',
            'extensions':{'starKbScope':{'project':'demo','operation':'replay'}}}
        self.store.ingest(self.adapter,d)
        r=self.query({'reachable':['quotes','b',1]})
        self.assertEqual(r['rows'][0]['evidence'],['edge'])
        self.assertEqual(r['rows'][0]['membership'],'supported')

    def test_planning_local_first_no_side_effect(self):
        self.add();h=self.store.record('hypothesis',{'question':'where?'})
        p=Planner(self.store,[Capability('boundary',('/missing',),('area',),3,'fixture','local')])
        local=p.plan(h,[{'path':'/regionCode','desired':'metadata','role':'observation'}])[0]
        self.assertEqual(self.store.get(local)['state'],'proposed')
        self.assertEqual(self.store.get(local)['existingDocuments'],['a'])
        missing=p.plan(h,[{'path':'/missing','desired':'boundary','role':'counterexample'}],budget=2)[0]
        self.assertFalse(self.store.get(missing)['collectionExecuted'])
        self.assertEqual(self.store.get(missing)['candidateMethods'],[])

    def test_scope_partition(self):
        self.add();other=Store(self.path,Scope('other','replay','demo'),self.limits)
        self.assertEqual(other.documents(),[])
        self.assertIsNone(other.claim());other.close()

    def test_claim_resume_retry_cancel_and_idempotency(self):
        self.add();other=Store(self.path,self.scope,self.limits)
        first=self.store.claim(lease_seconds=-1);self.assertIsNotNone(first)
        resumed=other.claim();self.assertEqual(first['id'],resumed['id'])
        self.assertIsNone(self.store.claim())
        other.settle(first['id'],resumed['attempts'],'temporary')
        again=self.store.claim();self.assertEqual(again['id'],first['id'])
        self.store.cancel(first['id']);self.store.settle(first['id'],again['attempts'])
        self.assertTrue(self.store.cancelled(first['id']));other.close()
        self.store.close();self.store=Store(self.path,self.scope,self.limits)
        self.assertEqual(len(self.store.documents()),1)
        self.assertIsNone(self.store.claim())

    def test_queue_and_corpus_limits_rollback(self):
        tiny=Store(Path(self.temp.name)/'tiny.sqlite',self.scope,Limits(queue=1,documents=2))
        tiny.ingest(self.adapter,document('a','x','v'))
        with self.assertRaisesRegex(LearningError,'queue_limit'):tiny.ingest(self.adapter,document('b','x','v'))
        self.assertIsNone(tiny.document('b'));tiny.close()

    def test_runtime_discovers_without_user_question(self):
        for i in range(4):self.add(str(i),str(i//2))
        result=Runtime(self.store).tick()
        self.assertEqual(result['state'],'done');self.assertTrue(self.store.records('hypothesis'))
        self.assertTrue(self.store.records('evidence_request'))

    def test_replay_budgets_and_cancellation(self):
        h=self.store.record('hypothesis',{'question':'x'})
        cap=Capability('fixture',('/x',),(),1,'local','replay')
        req=Planner(self.store,[cap]).plan(h,[{'path':'/x','desired':'x','role':'comparison'}],1)[0]
        collector=ReplayCollector(self.adapter,self.store,'fixture',[document('a','x','v')])
        with self.assertRaisesRegex(LearningError,'cancelled'):collector.execute(req,lambda:True)
        self.assertEqual(self.store.documents(),[])
        self.assertEqual(collector.execute(req)['mode'],'replay')

    def test_source_leakage_and_candidate_lifecycle(self):
        for i in range(4):self.add(str(i),str(i//2))
        learner=Discovery(self.store);candidate=learner.discover()[0]['candidate']
        label=document('label','annotation','v');label['extensions']['comparisonLabel']={'left':'0','right':'1','equivalent':True};self.store.ingest(self.adapter,label)
        pairs=[{'left':'0','right':'1','equivalent':True,'labelSource':'label'}]*4
        with self.assertRaisesRegex(LearningError,'leakage'):learner.evaluate(candidate,pairs)
        self.assertEqual(self.store.get(candidate)['state'],'proposed')
        with self.assertRaisesRegex(LearningError,'invalid_transition'):self.store.transition(candidate,'accepted')
        with self.assertRaises(LearningError):learner.propose({'path':'/x','meaning':'x','documents':['0'],'code':'shell'})

    def test_execution_result_limit_and_cancellation(self):
        self.add();s=self.selectors();sid=s.create('x',1,{'field':['/language','en']},'test')
        self.assertEqual(s.evaluate(sid,lambda:True)['execution'],'cancelled')
        self.store.limits=Limits(steps=1)
        self.assertEqual(s.evaluate(sid)['execution'],'budget_exhausted')

    def test_stale_predicate_and_new_corpus_invalidate_results(self):
        self.add();r=self.query({'field':['/language','en']})
        self.add('new')
        self.assertEqual(self.store.get(r['record'])['state'],'stale')
        learner=Discovery(self.store)
        for i in range(4):self.add('train'+str(i),str(i//2))
        candidate=learner.discover()[0]['candidate']
        revised=learner.revise(candidate,{'same':['/language','parameter']},'revision')
        self.assertEqual(self.store.get(revised)['version'],2)
        self.assertEqual(self.store.get(candidate)['version'],1)

    def test_expired_claim_cannot_settle_new_owner(self):
        self.add();old=self.store.claim(lease_seconds=-1)
        other=Store(self.path,self.scope,self.limits);new=other.claim()
        self.store.settle(old['id'],old['attempts'])
        self.assertFalse(other.cancelled(new['id'],new['attempts']))
        self.assertTrue(self.store.cancelled(old['id'],old['attempts']))
        other.settle(new['id'],new['attempts']);other.close()

    def test_rule_like_source_text_does_not_execute(self):
        d=self.add();d['rawContent']="'). :- shell('touch /tmp/should-not-exist-learning'). %"
        self.store.ingest(self.adapter,d)
        r=self.query({'field':['/rawContent',d['rawContent']]})
        self.assertEqual(r['execution'],'complete')
        self.assertEqual(r['rows'][0]['membership'],'supported')
        self.assertFalse(Path('/tmp/should-not-exist-learning').exists())

    def test_continuous_replay_checkpoint_and_partial_line(self):
        stream=Path(self.temp.name)/'stream.ndjson'
        stream.write_text(json.dumps(document('one','feature','a'))+'\n'+json.dumps(document('two','feature','b')))
        runtime=Runtime(self.store)
        self.assertEqual(runtime.follow(self.adapter,stream,polls=1)['changed'],1)
        self.assertIsNone(self.store.document('two'))
        with stream.open('a') as f:f.write('\n')
        self.assertEqual(runtime.follow(self.adapter,stream,polls=1)['changed'],1)
        self.assertEqual(runtime.follow(self.adapter,stream,polls=1)['changed'],0)
        stream.write_text(json.dumps(document('three','feature','c'))+'\n')
        self.assertEqual(runtime.follow(self.adapter,stream,polls=1)['changed'],1)

    def test_multi_domain_recursion(self):
        if not shutil.which('swipl'):self.skipTest('SWI required')
        report=example(ROOT)
        self.assertEqual(len(report['reports']),2)
        for r in report['reports']:
            self.assertEqual(r['evaluation']['state'],'accepted')
            self.assertEqual(r['comparison']['baseline']['uniqueFixtureGroups'],1)
            self.assertEqual(r['comparison']['learned']['uniqueFixtureGroups'],3)
            self.assertTrue(any(e['evidence'] for e in r['explanations']))
        self.assertEqual(report['rejectedExperiment']['state'],'rejected')
        self.assertEqual(report['cadenceAssociation']['timestampField'],'createdAt')
        self.assertEqual(report['geographySelector']['execution'],'complete')
        self.assertTrue(report['selfExamination']['rejectedCandidates'])
