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
                requests=planner.plan(hypothesis,store.get(hypothesis)['requirements'],budget=8)
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
