"""Synthetic multi-domain replay. Labels are fixtures, not production findings."""
from pathlib import Path
import tempfile
import time
from .contracts import CanonicalAdapter, Limits, Scope
from .store import Store
from .discovery import Discovery, value_at
from .planning import Capability, Planner, ReplayCollector
from .selectors import Selectors


def document(identifier, feature, group, *, area='OH', created=0, dataset='demo'):
    return {'id':identifier,'dtype':'document','dataset':dataset,'schemaVersion':'0.10.1',
        'rawContent':'Synthetic fixture, not a statement about real people.',
        'language':'en','regionCode':area,'createdAt':created,
        'provenance':{'independentSource':'publisher:'+identifier,'replay':True,'method':'synthetic-fixture/1'},
        'extensions':{'starKbScope':{'project':'demo','operation':'replay'},
                      'features':{feature:group}}}


def example(starlang, swipl='swipl'):
    adapter=CanonicalAdapter(starlang);scope=Scope('demo','replay','demo');limits=Limits(steps=100000)
    with tempfile.TemporaryDirectory(prefix='learning-demo-') as temp:
        store=Store(Path(temp)/'cache.sqlite',scope,limits)
        try:
            reports=[]
            for domain,feature in [('source_ancestry','upstreamRelease'),('language_convention','editorialConvention')]:
                path='/extensions/features/'+feature
                train=[document(domain+'-train-'+str(i),feature,'origin-'+str(i//2),created=i) for i in range(4)]
                held=[document(domain+'-held-'+str(i),feature,'new-origin-'+str(i//2),created=100+i) for i in range(8)]
                later=[document(domain+'-later-'+str(i),feature,'later-origin-'+str(i//3),created=200+i) for i in range(9)]
                for d in train:store.ingest(adapter,d)
                learner=Discovery(store)
                discovered=learner.discover([d['id'] for d in train],[path])
                candidate=discovered[0]['candidate'];hypothesis=discovered[0]['hypothesis']
                capability=Capability('synthetic-comparison',(path,),('source-labelled sample',),8,'fixture only','local replay','replay')
                planner=Planner(store,[capability])
                requirements=[{**r,'inputs':{'source-labelled sample':'synthetic held-out pairs'}} for r in store.get(hypothesis)['requirements']]
                requests=planner.plan(hypothesis,requirements,budget=8)
                replay=ReplayCollector(adapter,store,capability.name,held).execute(requests[0])
                pairs=[]
                for a,b,label in [(0,1,True),(2,3,True),(4,6,False),(5,7,False)]:
                    label_id=domain+'-label-'+str(a)+'-'+str(b)
                    label_doc=document(label_id,'annotation','fixture',created=90)
                    label_doc['extensions']['comparisonLabel']={'left':held[a]['id'],'right':held[b]['id'],'equivalent':label}
                    label_doc['provenance']['method']='independently-assigned-synthetic-label/1'
                    store.ingest(adapter,label_doc)
                    pairs.append({'left':held[a]['id'],'right':held[b]['id'],'equivalent':label,'labelSource':label_id})
                evaluation=learner.evaluate(candidate,pairs)
                for d in later:store.ingest(adapter,d)
                selectors=Selectors(store,swipl)
                chosen=[];explained=[];selection_started=time.monotonic()
                # Same acquisition budget of three documents. The learned
                # concept suppresses redundant candidates in a later sample.
                for d in later:
                    if len(chosen)>=3:break
                    redundant=False
                    for anchor in chosen:
                        sid=selectors.create('compare-'+d['id']+'-'+anchor,1,
                            {'learned':[candidate,1,anchor]},'Avoid repeated operational evidence groups')
                        result=selectors.evaluate(sid)
                        if result['execution']!='complete':raise RuntimeError(result)
                        row=next(r for r in result['rows'] if r['subject']==d['id'])
                        explained.append(row)
                        if row['membership']=='supported':redundant=True;break
                    if not redundant:chosen.append(d['id'])
                baseline=[d['id'] for d in later[:3]]
                independent=lambda ids:len({value_at(store.document(i),path) for i in ids})
                metrics={'budgetDocuments':3,'selectionCompute':{'prologCalls':len(explained),'elapsedSeconds':time.monotonic()-selection_started,'baselinePrologCalls':0},'baseline':{'documents':baseline,'uniqueFixtureGroups':independent(baseline),'redundancy':1-independent(baseline)/3},
                         'learned':{'documents':chosen,'uniqueFixtureGroups':independent(chosen),'redundancy':1-independent(chosen)/3},
                         'interpretation':'Synthetic operational group diversity only; not verified real-world source independence.'}
                exp=store.record('experiment',{'procedure':'learned-diversification/1','candidate':candidate,
                    'metrics':metrics,'sample':'later-than-discovery-and-evaluation','mode':'synthetic-replay'},
                    [d['id'] for d in train+held+later],state='evaluated')
                reports.append({'domain':domain,'discovered':discovered,'collection':replay,
                    'evaluation':evaluation,'laterExperiment':exp,'comparison':metrics,'explanations':explained})
            # Demonstrate a rejected model-proposed concept, not just successes.
            bad=Discovery(store).propose({'path':'/language','meaning':'same language is same upstream source',
                'documents':[d['id'] for d in train]})
            rejected=Discovery(store).evaluate(bad,pairs)
            cadence=Discovery(store).cadence([d['id'] for d in later],timestamp='createdAt')
            selectors=Selectors(store,swipl)
            geography=selectors.evaluate(selectors.create('recorded-region',1,
                {'all':[{'field':['/regionCode','OH']},{'during':['/createdAt',200,205]}]},
                'Select recorded document region metadata and creation interval, not author residence.'))
            diagnostics=Discovery(store).diagnostics()
            return {'cadenceAssociation':store.get(cadence),'geographySelector':geography,
                    'selfExamination':diagnostics,'mode':'synthetic-replay','modelWeightsUpdated':False,'reports':reports,
                    'rejectedExperiment':rejected,'liveCollection':'not configured',
                    'knownLimits':'Operational definitions and finite fixture evaluation, not ASI or general scientific discovery.'}
        finally:store.close()


def passage_example(starlang, swipl='swipl'):
    """Two text-only domains. Hidden fixture groups are evaluation labels only."""
    from .contracts import digest
    adapter=CanonicalAdapter(starlang)
    reports=[]
    for domain in ('release_passages','technical_procedures'):
        with tempfile.TemporaryDirectory(prefix='passage-demo-') as temp:
            store=Store(Path(temp)/'kb.sqlite',Scope('demo','replay','demo'),Limits(steps=100000))
            try:
                def textdoc(identifier,text):
                    d=document(identifier,'unused','unused');d['extensions'].pop('features')
                    d['rawContent']=text;store.ingest(adapter,d);return d
                train=[textdoc('train-a','silver rivers carry tiny boats toward distant harbors'),
                       textdoc('train-b','quotation: SILVER rivers carry tiny boats toward distant harbors.'),
                       textdoc('train-c','bright deserts shelter ancient sleeping emerald creatures')]
                learner=Discovery(store);found=learner.discover_passages([d['id'] for d in train])
                candidate=found[0]['candidate'];hypothesis=found[0]['hypothesis']
                planner=Planner(store)
                requests=planner.plan(hypothesis,store.get(hypothesis)['requirements'])
                held_text=['pale moons illuminate quiet gardens beneath clear skies',
                           'Pale moons illuminate quiet gardens beneath clear skies!',
                           'bronze keys unlock the old hidden stone doorway',
                           'quote: Bronze keys unlock the old hidden stone doorway.',
                           'wild birds gather beside tall eastern marble towers',
                           'deep oceans conceal several ancient empty mountain cities',
                           'rainy windows overlook bright yellow rooftops every evening',
                           'distant sunsets warm each quiet grassy northern hillside']
                for i,text in enumerate(held_text):textdoc('held-'+str(i),text)
                pairs=[]
                for a,b,label in [(0,1,True),(2,3,True),(4,5,False),(6,7,False)]:
                    ann=textdoc('annotation-'+str(a),'Independent synthetic pair annotation.')
                    ann['extensions']['comparisonLabel']={'left':'held-'+str(a),'right':'held-'+str(b),'equivalent':label}
                    store.ingest(adapter,ann)
                    pairs.append({**ann['extensions']['comparisonLabel'],'labelSource':ann['id']})
                evaluation=learner.evaluate(candidate,pairs)
                passages={'release_passages':['purple herons cross the winter river before sunrise',
                            'golden ships enter a distant harbor after sunset',
                            'quiet forests shelter small woodland creatures throughout winter'],
                          'technical_procedures':['restart the worker after flushing its pending journal',
                            'verify each checksum before restoring the archived snapshot',
                            'cancel the request before releasing its active lease']}[domain]
                later=[];fixture_groups={}
                for group,text in enumerate(passages):
                    for revision in range(3):
                        d=textdoc(f'later-{group}-{revision}',f'edition {revision}: {text}.')
                        later.append(d);fixture_groups[d['id']]=group
                selectors=Selectors(store,swipl);selected=[];calls=0;explanations=[]
                started=time.monotonic()
                for d in later:
                    if len(selected)>=3:break
                    redundant=False
                    for anchor in selected:
                        sid=selectors.create('compare-'+d['id']+'-'+anchor,1,
                            {'learned':[candidate,1,anchor]},'Diversify exact lexical passages, not verified sources.')
                        result=selectors.evaluate(sid);calls+=1
                        if result['execution']!='complete':raise RuntimeError(result)
                        row=next(r for r in result['rows'] if r['subject']==d['id'])
                        explanations.append({'decision':row,'passages':[r for r in result['passageEvidence'] if r['subject']==d['id']]})
                        if row['membership']=='supported':redundant=True;break
                    if not redundant:selected.append(d['id'])
                baseline=[d['id'] for d in later[:3]]
                exact=[];hashes=set()
                for d in later:
                    key=digest(d['rawContent'])
                    if key not in hashes:hashes.add(key);exact.append(d['id'])
                    if len(exact)==3:break
                def metrics(ids):
                    groups=len({fixture_groups[i] for i in ids})
                    return {'documents':ids,'fixtureGroups':groups,'redundancy':1-groups/len(ids)}
                result={'mode':'synthetic-text-replay','domain':domain,'candidate':candidate,
                    'hypothesis':hypothesis,'evidenceRequests':requests,'evaluation':evaluation,
                    'budgetDocuments':3,'chronologicalBaseline':metrics(baseline),
                    'exactDocumentHashBaseline':metrics(exact),'learned':metrics(selected),
                    'compute':{'prologCalls':calls,'elapsedSeconds':time.monotonic()-started},
                    'explanations':explanations,
                    'limitations':'Fixture passage-group diversity only. Overlap is not evidence of coordination or actual source dependence. Equal acquisition budget, not equal compute.'}
                eid=store.record('experiment',{'procedure':'text-passage-diversification/1',**result},
                    [d['id'] for d in store.documents()],state='evaluated')
                reports.append({**result,'experiment':eid})
            finally:store.close()
    return {'reports':reports,'liveCollection':False,'modelWeightsUpdated':False}
