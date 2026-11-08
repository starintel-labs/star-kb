"""Real text discovery with independently annotated, source-disjoint fixtures."""
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from star_kb_learning.contracts import CanonicalAdapter, LearningError, Limits, Scope
from star_kb_learning.store import Store
from star_kb_learning.discovery import Discovery
from star_kb_learning.examples import document
from star_kb_learning.passages import token_windows, overlap
from star_kb_learning.planning import Capability, Planner, ReplayCollector
from star_kb_learning.runtime import Runtime
from star_kb_learning.selectors import Selectors, compile_expression


class PassagePrimitiveTests(unittest.TestCase):
    def test_exact_offsets_and_normalization(self):
        a='prefix: STRASSE, river boats float softly home. suffix'
        b='Straße river boats float softly home!'
        match=overlap(token_windows(a),token_windows(b))
        self.assertEqual(a[slice(*match['subjectSpan'])],'STRASSE, river boats float softly home')
        self.assertEqual(b[slice(*match['anchorSpan'])],'Straße river boats float softly home')
        self.assertEqual(match['tokenCount'],6)
        self.assertIsNone(overlap(token_windows(None),token_windows(b)))
        self.assertIsNone(overlap(token_windows('a b c'),token_windows(b)))

    def test_limits_and_closed_grammar(self):
        for n in (0,True,2,17):
            with self.assertRaises(LearningError):token_windows('hello',n)
        with self.assertRaisesRegex(LearningError,'input_limit'):token_windows('a '*2049)
        for expr in ({'passage':['/rawContent',True,'a']},{'passage':['/rawContent',6,{}]},
                     {'passage':['/rawContent',6,"a'). shell(x)."]}):
            with self.assertRaises(LearningError):compile_expression(expr,{})


@unittest.skipUnless(os.environ.get('STARLANG_ROOT'),'pinned canonical schema required')
class PassageLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temp.name)/'kb.sqlite',Scope('demo','replay','demo'),Limits(steps=100000))
        self.adapter=CanonicalAdapter(os.environ['STARLANG_ROOT'])
        self.learner=Discovery(self.store)

    def tearDown(self):self.store.close();self.temp.cleanup()

    def add(self,i,text,group=None):
        d=document(i,'unused',i)
        d['rawContent']=text
        d['extensions'].pop('features') # no prelabelled feature inputs
        d['provenance']['independentSource']=group or 'publisher:'+i
        self.store.ingest(self.adapter,d)
        return d

    def train(self):
        self.add('train-a','the silver river carries quiet boats tonight')
        self.add('train-b','quote: THE SILVER RIVER carries quiet boats tonight!')
        self.add('train-c','oranges sparkle among distant silent mountain forests')
        return self.learner.discover_passages(['train-a','train-b','train-c'])[0]

    def evaluate(self,candidate):
        texts=['a pale moon lights the northern garden', 'A PALE moon lights the northern garden!',
               'bronze keys open the old wooden cupboard','Bronze keys open the old wooden cupboard.',
               'small birds gather beside the eastern tower','deep oceans hide several ancient empty cities',
               'our rainy windows overlook bright yellow rooftops','the distant sunset warms each quiet hillside']
        for i,text in enumerate(texts):self.add('held-'+str(i),text)
        pairs=[]
        for a,b,label in [(0,1,True),(2,3,True),(4,5,False),(6,7,False)]:
            i='label-'+str(a);d=self.add(i,'Independent fixture annotation, not an observed world fact.')
            d['extensions']['comparisonLabel']={'left':'held-'+str(a),'right':'held-'+str(b),'equivalent':label}
            self.store.ingest(self.adapter,d)
            pairs.append({**d['extensions']['comparisonLabel'],'labelSource':i})
        return self.learner.evaluate(candidate,pairs)

    def test_text_to_accepted_predicate_to_prolog_reuse(self):
        if not shutil.which('swipl'):self.skipTest('actual SWI required')
        found=self.train();candidate=found['candidate']
        definition=self.store.get(candidate)
        self.assertEqual(definition['expression'],{'passage':['/rawContent',6,'parameter']})
        for span in definition['sourceSpans']:
            self.assertIn('silver',self.store.document(span['document'])['rawContent'][slice(*span['span'])].lower())
        self.assertEqual(self.evaluate(candidate)['state'],'accepted')
        self.add('later-a','crimson foxes leap across icy frozen streams')
        self.add('later-b','reported: Crimson foxes leap across icy frozen streams.')
        self.add('later-c','many clear stars twinkle above distant green valleys')
        selectors=Selectors(self.store)
        sid=selectors.create('reuse-passages',1,{'learned':[candidate,1,'later-a']},'Find lexical overlap only')
        result=selectors.evaluate(sid)
        self.assertEqual(result['execution'],'complete')
        rows={r['subject']:r for r in result['rows']}
        self.assertEqual(rows['later-b']['membership'],'supported')
        self.assertEqual(rows['later-c']['membership'],'unresolved')
        self.assertEqual(rows['later-b']['evidence'],['later-a','later-b'])
        witness=next(x for x in result['passageEvidence'] if x['subject']=='later-b')
        self.assertEqual(witness['method'],'unicode-word-casefold-passage/1')
        # Retraction invalidates learned predicates and prior selector conclusions.
        d=self.store.document('train-a');d['deleted']=True;self.store.ingest(self.adapter,d)
        self.assertEqual(self.store.get(candidate)['state'],'stale')
        self.assertEqual(self.store.get(result['record'])['state'],'stale')
        with self.assertRaisesRegex(LearningError,'predicate_not_accepted'):selectors.evaluate(sid)

    def test_failed_annotation_alignment_rejects_not_silently_promotes(self):
        candidate=self.train()['candidate']
        # Change the proposed operational definition explicitly: the unrelated
        # metadata concept cannot masquerade as evaluated passage overlap.
        revision=self.learner.revise(candidate,{'same':['/language','parameter']},'Same recorded language only')
        self.assertEqual(self.evaluate(revision)['state'],'rejected')
        self.assertEqual(self.store.get(candidate)['state'],'proposed')

    def test_replay_collector_refuses_missing_inputs(self):
        found=self.train();planner=Planner(self.store,[Capability('fixture',('/rawContent',),('sample',),1,'fixture','local')])
        req=planner.plan(found['hypothesis'],[{'path':'/rawContent','desired':'held-out','role':'comparison'}])[0]
        with self.assertRaisesRegex(LearningError,'unsupported_collection'):
            ReplayCollector(self.adapter,self.store,'fixture',[]).execute(req)

    def test_continuous_discovery_and_discriminating_plan(self):
        self.train()
        result=Runtime(self.store).tick()
        self.assertEqual(result['state'],'done')
        hypothesis=next(h for h in self.store.records('hypothesis') if 'common boilerplate' in h.get('alternatives',[]))
        planner=Planner(self.store,[Capability('context',('/rawContent',),('url',),1,'public only','public','live')])
        requirement={'path':'/rawContent','desired':'original context','role':'context','excludeSources':['publisher:train-a']}
        req=self.store.get(planner.plan(hypothesis['id'],[requirement])[0])
        self.assertEqual(req['planningState'],'unresolved')
        self.assertEqual(req['candidateMethods'][0]['missingInputs'],['url'])
        self.assertNotIn('train-a',req['existingDocuments'])
        self.assertIn('common boilerplate',req['competingExplanations'])
        requirement['inputs']={'url':'https://example.invalid/public'}
        ready=self.store.get(planner.plan(hypothesis['id'],[requirement])[0])
        self.assertEqual(ready['planningState'],'requested')
        self.assertFalse(ready['collectionExecuted'])

    def test_no_pattern_and_bounded_failure(self):
        self.add('one','a single tiny text')
        self.assertEqual(self.learner.discover_passages(),[])
        self.add('two','a '*2049)
        with self.assertRaisesRegex(LearningError,'input_limit'):self.learner.discover_passages()
        with self.assertRaisesRegex(LearningError,'cancelled'):self.learner.discover_passages(cancelled=lambda:True)
        if shutil.which('swipl'):
            selectors=Selectors(self.store)
            sid=selectors.create('bounded',1,{'passage':['/rawContent',6,'one']},'Bound text work')
            self.assertEqual(selectors.evaluate(sid)['execution'],'input_limit')

    def test_oversized_text_does_not_discard_metadata_discovery(self):
        for i in range(4):
            d=document('metadata-'+str(i),'recordedFeature',str(i//2))
            if i==0:d['rawContent']='word '*2049
            self.store.ingest(self.adapter,d)
        runtime=Runtime(self.store);result=runtime.tick()
        self.assertEqual(result['state'],'done')
        self.assertTrue(result['result'])
        self.assertEqual(result['discoveryStages'][1]['execution'],'input_limit')
        self.assertTrue(self.store.records('evidence_request'))
        failures=[r for r in self.store.records('experiment') if r.get('procedure')=='discovery-stage/1']
        self.assertEqual(len(failures),1)
        # Remaining ingestion events for this generation are checkpoint no-ops.
        rest=runtime.drain()
        self.assertTrue(all(r['state']=='done' and r['result']==[] for r in rest))
        self.assertEqual(len([r for r in self.store.records('experiment') if r.get('procedure')=='discovery-stage/1']),1)
        self.assertIsNone(self.store.claim())
        self.assertEqual(len(self.store.document('metadata-0')['rawContent'].split()),2049)

    def test_foreign_scope_and_missing_text(self):
        self.train()
        other=Store(Path(self.temp.name)/'kb.sqlite',Scope('other','replay','demo'),Limits())
        self.assertEqual(Discovery(other).discover_passages(),[]);other.close()
        d=self.add('absent','unused');d.pop('rawContent');self.store.ingest(self.adapter,d)
        if shutil.which('swipl'):
            selectors=Selectors(self.store)
            sid=selectors.create('absent',1,{'passage':['/rawContent',6,'train-a']},'Unknown remains unknown')
            result=selectors.evaluate(sid)
            self.assertEqual(next(r for r in result['rows'] if r['subject']=='absent')['membership'],'unresolved')
