"""Bounded symbolic program synthesis and empirical evaluation.

Initial learner invents same-value operational concepts from repeated fields.
The field vocabulary comes from evidence, not from example-specific words.
It does not interpret a shared field as nationality, intent, or coordination.
"""
from collections import defaultdict
import time
from .contracts import LearningError, canonical, digest


def value_at(document, path):
    value = document
    try:
        for part in path.lstrip('/').split('/'):
            part = part.replace('~1','/').replace('~0','~')
            value = value[int(part)] if isinstance(value,list) else value[part]
    except (KeyError,IndexError,TypeError,ValueError):
        return None
    return value


def source_group(document):
    # No source independence is inferred from distinct document/account IDs.
    return document.get('provenance',{}).get('independentSource')


class Discovery:
    def __init__(self, store):
        self.store=store

    def discover(self, identifiers=None, paths=None, cancelled=lambda:False):
        start=time.monotonic()
        docs=self.store.documents()
        if identifiers is not None:
            wanted=set(identifiers);docs=[d for d in docs if d['id'] in wanted]
            if len(docs)!=len(wanted): raise LearningError('missing_discovery_document')
        docids={d['id'] for d in docs}
        # Shape-level grouping is indexed and bounded. Different semantic
        # geography/timestamp fields remain separate, never pooled.
        groups=defaultdict(lambda:defaultdict(list))
        for row in self.store.db.execute('SELECT doc,path,value FROM observations WHERE scope=? ORDER BY path,value,doc',(self.store.scope.key,)):
            if cancelled(): raise LearningError('cancelled')
            if time.monotonic()-start>self.store.limits.seconds: raise LearningError('discovery_budget')
            doc,path,value=row
            if doc not in docids or (paths is not None and path not in paths): continue
            if paths is None and not (path.startswith('/extensions/features/') or path in ('/language','/regionCode')): continue
            groups[path][value].append(doc)
        result=[]
        for path,values in sorted(groups.items()):
            repeated={v:ids for v,ids in values.items() if len(ids)>1 and v!='null'}
            if not repeated or len(values)<2: continue
            dependencies=sorted({i for ids in values.values() for i in ids})
            expression={'same':[path,'parameter']}
            name='same_observed_'+digest(path)[:12]
            body={'name':name,'version':1,'expression':expression,'method':'bounded-field-equivalence/1',
                  'meaning':f'Two documents record the same non-null scalar at {path}.',
                  'parameters':['anchorDocument'],'subjectType':'document',
                  'discoveryDocuments':dependencies,'discoverySources':sorted({source_group(d) for d in docs if source_group(d)}),
                  'supportingExamples':[ids[:2] for ids in repeated.values()],
                  'counterexampleValues':list(values)[:4],
                  'applicability':'Equality of a recorded field only; no inferred identity, origin, intent or location.',
                  'requirements':[{'path':path,'role':'comparison','desired':'independently labelled held-out positive and negative pairs'}]}
            rid=self.store.record('definition',body,dependencies)
            hypothesis=self.store.record('hypothesis',{
                'candidate':rid,'initiatingDocuments':dependencies,
                'proposedExplanation':body['meaning'],
                'alternatives':['shared upstream source','editorial policy','genre or cohort convention','chance or duplicated material'],
                'expectedConsequences':['same-field pairs match independent comparison labels on new sources'],
                'refutation':['held-out same-field pairs have different independent labels'],
                'evaluationMethod':'source-disjoint labelled pair comparison; baseline predicts no equivalence',
                'requirements':body['requirements']}, dependencies)
            result.append({'candidate':rid,'hypothesis':hypothesis})
            if len(result)>=self.store.limits.candidates:break
        return result

    def propose(self, payload, method='frozen-model-proposal/1'):
        """Optional frozen LLM/plugin proposals remain untrusted candidates.

        Only the implemented synthesis grammar is accepted; arbitrary source
        code, goals, capabilities and instructions are never executable.
        """
        if not isinstance(payload,dict) or set(payload)!={'path','meaning','documents'}:
            raise LearningError('invalid_proposal')
        path=payload['path']
        if not isinstance(path,str) or not path.startswith('/') or len(path)>256:
            raise LearningError('invalid_proposal')
        docs=[self.store.document(i) for i in payload['documents']]
        if not docs or any(d is None for d in docs):raise LearningError('missing_dependency')
        return self.store.record('definition',{'name':'proposed_'+digest(payload)[:12],
            'version':1,'expression':{'same':[path,'parameter']},'meaning':str(payload['meaning'])[:1000],
            'method':method,'discoveryDocuments':payload['documents'],
            'discoverySources':sorted({source_group(d) for d in docs if source_group(d)}),
            'applicability':'Untrusted model-proposed operational equality; not an empirical finding.'},payload['documents'])

    def evaluate(self, candidate, pairs, minimum=4, cancelled=lambda:False):
        record=self.store.get(candidate)
        if record['kind']!='definition':raise LearningError('not_definition')
        if record['state'] in ('accepted','rejected'):
            for old in self.store.records('experiment'):
                if old.get('candidate')==candidate and [{k:r[k] for k in ('left','right','equivalent','labelSource')} for r in old.get('sample',[])]==pairs:
                    return {'candidate':candidate,'state':record['state'],'experiment':old['id'],
                            **{k:v for k,v in old.items() if k not in ('state','id')}}
            raise LearningError('evaluation_replay_conflict')
        if record['state']!='proposed':raise LearningError('candidate_not_proposed')
        if not isinstance(pairs,list) or not minimum<=len(pairs)<=self.store.limits.documents:
            raise LearningError('evaluation_sample_limit')
        for pair in pairs:
            if (not isinstance(pair,dict) or set(pair)!={'left','right','equivalent','labelSource'}
                    or type(pair['equivalent']) is not bool
                    or any(not isinstance(pair[k],str) or not pair[k] for k in ('left','right','labelSource'))):
                raise LearningError('independent_labels_required')
        evaluation_ids={pair[k] for pair in pairs for k in ('left','right')}
        evaluation_documents=[self.store.document(i) for i in evaluation_ids]
        if any(d is None for d in evaluation_documents):raise LearningError('missing_evaluation_document')
        evaluation_sources={source_group(d) for d in evaluation_documents}
        path=record['expression']['same'][0]
        discovery=set(record['discoveryDocuments']); sources=set(record['discoverySources'])
        tp=fp=tn=fn=0;deps=[];seen=set();groups=set();rows=[];used_documents=set();used_sources=set()
        for pair in pairs:
            if cancelled():raise LearningError('cancelled')
            if not isinstance(pair,dict) or set(pair)!={'left','right','equivalent','labelSource'} or type(pair['equivalent']) is not bool or not pair['labelSource']:
                raise LearningError('independent_labels_required')
            label_record=self.store.document(pair['labelSource'])
            if label_record is None or label_record.get('extensions',{}).get('comparisonLabel') != {
                    'left':pair['left'],'right':pair['right'],'equivalent':pair['equivalent']}:
                raise LearningError('label_provenance_required')
            if (pair['labelSource'] in evaluation_ids or not source_group(label_record)
                    or source_group(label_record) in sources | evaluation_sources):
                raise LearningError('label_source_separation_required')
            deps.append(pair['labelSource'])
            left,right=(self.store.document(pair[k]) for k in ('left','right'))
            if left is None or right is None:raise LearningError('missing_evaluation_document')
            key=tuple(sorted((left['id'],right['id'])))
            if left['id']==right['id'] or key in seen:raise LearningError('duplicate_evaluation_pair')
            seen.add(key)
            if used_documents.intersection(key):raise LearningError('dependent_evaluation_pairs')
            used_documents.update(key)
            if discovery.intersection(key):raise LearningError('evaluation_leakage')
            for d in (left,right):
                group=source_group(d)
                if not group or group in sources:raise LearningError('source_separation_required')
                if group in used_sources:raise LearningError('dependent_evaluation_sources')
                groups.add(group)
            used_sources.update(source_group(d) for d in (left,right))
            a,b=value_at(left,path),value_at(right,path)
            if a is None or b is None:raise LearningError('missing_evaluation_observation')
            predicted=canonical(a)==canonical(b);label=pair['equivalent']
            if predicted and label:tp+=1
            elif predicted:fp+=1
            elif label:fn+=1
            else:tn+=1
            deps.extend(key);rows.append({**pair,'predicted':predicted})
        n=len(pairs);accuracy=(tp+tn)/n;baseline=(tn+fp)/n
        evaluation={'candidate':candidate,'sample':rows,'sourceGroups':sorted(groups),
            'confusion':{'tp':tp,'fp':fp,'tn':tn,'fn':fn},'accuracy':accuracy,
            'baselineAccuracy':baseline,'baseline':'always non-equivalent','cost':{'pairs':n},
            'uncertainty':'Finite labelled sample; no probability of truth or population guarantee.',
            'criterion':'both classes; >=2 independent held-out source labels; no errors; beats constant baseline'}
        if cancelled():raise LearningError('cancelled')
        success=tp>0 and tn>0 and fp==0 and fn==0 and accuracy>baseline and len(groups)>=2
        dependencies=[d['doc'] for d in self.store.dependencies(candidate)]+deps
        eid=self.store.record('experiment',evaluation,dependencies,state='evaluated')
        self.store.transition(candidate,'evaluated')
        self.store.transition(candidate,'accepted' if success else 'rejected')
        # Acceptance must depend on evaluation evidence as well as discovery.
        self.store.record('definition',{k:v for k,v in record.items() if k not in ('id','state','contract','kind')},dependencies,identifier=candidate)
        return {'candidate':candidate,'state':self.store.get(candidate)['state'],'experiment':eid,**evaluation}

    def cadence(self, identifiers, timestamp='createdAt'):
        if timestamp not in ('createdAt','collectedAt','observedAt','sentAt'):
            raise LearningError('unsupported_timestamp_semantics')
        docs=[self.store.document(i) for i in identifiers]
        if any(d is None for d in docs) or len(docs)>self.store.limits.documents:raise LearningError('invalid_sample')
        values=sorted((d[timestamp],d['id']) for d in docs if type(d.get(timestamp)) is int)
        intervals=[b[0]-a[0] for a,b in zip(values,values[1:])]
        return self.store.record('association',{'method':'observed-intervals/1','timestampField':timestamp,
            'sampleDocuments':[x[1] for x in values],'intervals':intervals,
            'limits':'Sample-only temporal association, not evidence of automation or keystroke timing.'},[d['id'] for d in docs],state='evaluated')

    def revise(self, candidate, expression, meaning):
        old=self.store.get(candidate)
        if old['kind']!='definition':raise LearningError('not_definition')
        from .selectors import compile_expression
        compile_expression(expression,{},bound=self.store.limits.depth)
        if set(expression)!= {'same'}:raise LearningError('unsupported_synthesis_grammar')
        body={k:v for k,v in old.items() if k not in ('contract','kind','id','state')}
        body.update(version=old['version']+1,expression=expression,meaning=meaning,revises=candidate)
        return self.store.record('definition',body,[r['doc'] for r in self.store.dependencies(candidate)])

    def diagnostics(self):
        return {'hypothesesWithoutDiscriminatingEvidence':[r['id'] for r in self.store.records('hypothesis')
                    if not any(q.get('origin')==r['id'] and q.get('candidateMethods') for q in self.store.records('evidence_request'))],
                'staleRules':[r['id'] for r in self.store.records('definition') if r['state']=='stale'],
                'rejectedCandidates':[r['id'] for r in self.store.records('definition') if r['state']=='rejected'],
                'failedProcedures':[dict(r) for r in self.store.db.execute(
                    "SELECT id,kind,error,attempts FROM messages WHERE scope=? AND state='failed'",(self.store.scope.key,))]}
