"""Closed selector AST compiled into the repository's bounded SWI-Prolog API."""
import json
import re
import subprocess
import tempfile
import time
from pathlib import Path
from prolog_star_kb.projection import Fact, prolog_term, iter_scalars
from .contracts import LearningError, canonical, digest
from .passages import token_windows, overlap
from .discovery import value_at

MODULE = Path(__file__).resolve().parents[1] / 'kb/core/star_learning.pl'
IDENTIFIER = re.compile(r'^[A-Za-z0-9_.:/-]{1,256}$')


def compile_expression(expr, definitions, depth=0, bound=12):
    if depth > bound or not isinstance(expr, dict) or len(expr) != 1:
        raise LearningError('invalid_selector')
    op, args = next(iter(expr.items()))
    if op in ('all', 'any'):
        if not isinstance(args, list) or not 1 <= len(args) <= 16:
            raise LearningError('invalid_selector')
        return op + '([' + ','.join(compile_expression(x, definitions, depth+1, bound) for x in args) + '])'
    if op == 'exclude':
        return 'exclude(' + compile_expression(args, definitions, depth+1, bound) + ')'
    if op == 'count':
        if not isinstance(args, list) or len(args) != 2 or type(args[1]) is not int or not 1 <= args[1] <= 256:
            raise LearningError('invalid_aggregation')
        return 'count(' + compile_expression(args[0], definitions, depth+1, bound) + ',' + str(args[1]) + ')'
    arities = {'field': 2, 'same': 2, 'attributed': 3, 'reachable': 3, 'during': 3, 'learned': 3, 'passage': 3}
    if op not in arities or not isinstance(args, list) or len(args) != arities[op]:
        raise LearningError('prohibited_capability')
    if op in ('field', 'same', 'during', 'passage') and (not isinstance(args[0], str) or not args[0].startswith('/') or len(args[0])>256):
        raise LearningError('invalid_path')
    if op == 'passage':
        if type(args[1]) is not int or not 3<=args[1]<=16 or not isinstance(args[2],str) or not IDENTIFIER.fullmatch(args[2]):
            raise LearningError('invalid_passage_selector')
    if op == 'field':
        if isinstance(args[1], (dict, list)):
            raise LearningError('scalar_required')
        return f'field({prolog_term(args[0])},{prolog_term(canonical(args[1]))})'
    if op == 'attributed':
        if not all(isinstance(x,str) for x in args[:2]) or isinstance(args[2],(dict,list)):
            raise LearningError('invalid_attribution')
        return 'attributed('+prolog_term(args[0])+','+prolog_term(args[1])+','+prolog_term(canonical(args[2]))+')'
    if op == 'reachable' and (type(args[2]) is not int or not 1 <= args[2] <= 8):
        raise LearningError('graph_depth_limit')
    if op == 'during':
        if any(type(x) not in (int,float) for x in args[1:]) or args[1] > args[2]:
            raise LearningError('invalid_temporal_range')
    if op == 'learned':
        key = (args[0], args[1])
        if key not in definitions:
            raise LearningError('predicate_not_accepted')
    if any(not isinstance(x, (str,int,float)) or isinstance(x,bool) for x in args):
        raise LearningError('invalid_selector_argument')
    return op + '(' + ','.join(prolog_term(x) for x in args) + ')'


class Selectors:
    def __init__(self, store, swipl='swipl'):
        self.store, self.swipl = store, swipl

    def create(self, name, version, expression, purpose, subject_type='any', parameters=()):
        if not IDENTIFIER.fullmatch(name) or type(version) is not int or version < 1 or not purpose:
            raise LearningError('invalid_selector_identity')
        definitions = self.definitions()
        compile_expression(expression, definitions, bound=self.store.limits.depth)
        # Immutable identity + explicit revision. Selector meaning never changes
        # when a different candidate version is accepted.
        return self.store.record('selector', {'name': name, 'version': version,
            'subjectType': subject_type, 'parameters': list(parameters), 'purpose': purpose,
            'expression': expression, 'scope': self.store.scope.key,
            'completeness': 'open_world', 'bounds': vars(self.store.limits)},
            identifier=f'selector:{name}:{version}')

    def definitions(self):
        return {(r['id'],r['version']):r for r in self.store.records('definition') if r['state']=='accepted'}

    def evaluate(self, identifier, cancelled=lambda: False):
        selector = self.store.get(identifier)
        if selector['kind'] != 'selector':
            raise LearningError('not_selector')
        definitions = self.definitions()
        expression = compile_expression(selector['expression'], definitions, bound=self.store.limits.depth)
        documents = self.store.documents()
        facts = []
        passage_evidence=[]
        # Only requested anchors are compared: no quadratic all-pairs corpus scan.
        def requests(expr):
            op,args=next(iter(expr.items()))
            if op in ('all','any'):
                for child in args:yield from requests(child)
            elif op in ('exclude','count'):
                yield from requests(args if op=='exclude' else args[0])
            elif op=='passage':yield tuple(args)
            elif op=='learned':
                body=definitions[(args[0],args[1])]['expression']
                if 'passage' in body:
                    path,minimum,_=body['passage'];yield (path,minimum,args[2])
        started=time.monotonic();windows={};window_count=0
        try:
            for path,minimum,anchor in sorted(set(requests(selector['expression']))):
                anchor_doc=self.store.document(anchor)
                if anchor_doc is None:continue
                for d in documents:
                    if cancelled():return {'execution':'cancelled','rows':[]}
                    if time.monotonic()-started>self.store.limits.seconds:
                        return {'execution':'timeout','rows':[]}
                    for doc in (d,anchor_doc):
                        key=(doc['id'],path,minimum)
                        if key not in windows:
                            windows[key]=token_windows(value_at(doc,path),minimum)
                            window_count+=len(windows[key] or {})
                            if window_count>self.store.limits.observations:raise LearningError('passage_input_limit')
                    witness=overlap(windows[(d['id'],path,minimum)],windows[(anchor,path,minimum)])
                    if witness is not None:
                        facts.append(Fact('star_learning:passage_match',(d['id'],anchor,path,minimum)).render())
                        passage_evidence.append({'subject':d['id'],'anchor':anchor,'path':path,**witness})
        except LearningError as exc:
            return {'execution':'input_limit','reason':str(exc),'rows':[]}
        # Learning artifacts are an explicit subject family, not silently mixed
        # into the canonical-document universe of an existing selector.
        record_kinds={'hypothesis','definition','association','evidence_request','experiment'}
        subjects=self.store.records(selector['subjectType']) if selector['subjectType'] in record_kinds else []
        if len(subjects)>self.store.limits.documents:
            return {'execution':'sample_limit','rows':[]}
        for record in subjects:
            facts.append(Fact('star_learning:subject',(record['id'],record['kind'])).render())
            for path,value in iter_scalars(record):
                facts.append(Fact('star_learning:value',(record['id'],path,canonical(value))).render())
        for d in documents:
            facts.append(Fact('star_learning:subject',(d['id'],d['dtype'])).render())
        for row in self.store.db.execute('SELECT doc,path,value FROM observations WHERE scope=? ORDER BY doc,path', (self.store.scope.key,)):
            facts.append(Fact('star_learning:value',tuple(row)).render())
        for d in documents:
            if d['dtype']=='claim' and d.get('claimantId') and d.get('predicate') and 'object' in d:
                polarity = d.get('polarity')
                if polarity in ('positive','negative'):
                    facts.append(Fact('star_learning:claim',(d['claimantId'],d['predicate'],canonical(d['object']),polarity,d['id'])).render())
            if d['dtype']=='relation' and d.get('direction','directed')=='directed':
                facts.append(Fact('star_learning:edge',(d['source']['id'],d['predicate'],d['destination']['id'],d['id'])).render())
        for (rid,version), definition in definitions.items():
            body = compile_expression(definition['expression'], {}, bound=self.store.limits.depth)
            facts.append(f'star_learning:definition({prolog_term(rid)},{version},{body}).')
        limits = self.store.limits
        goal = f'star_learning:run({expression},{prolog_term(selector["subjectType"])},{limits.results},{limits.steps},{limits.seconds})'
        if cancelled():
            return {'execution':'cancelled','rows':[]}
        with tempfile.TemporaryDirectory(prefix='star-kb-selector-') as temp:
            fixture = Path(temp)/'facts.pl'; fixture.write_text('\n'.join(facts))
            # The program, module and process options are operator-owned.
            try:
                p = subprocess.Popen([self.swipl,'-q','--stack-limit=64m','-s',str(MODULE),'-s',str(fixture),'-g',goal,'-t','halt'],
                                     stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            except OSError as exc:
                return {'execution':'engine_unavailable','reason':str(exc),'rows':[]}
            deadline = time.monotonic()+limits.seconds+1
            while True:
                if cancelled():
                    p.kill();p.communicate();return {'execution':'cancelled','rows':[]}
                try:
                    output,error = p.communicate(timeout=.05); break
                except subprocess.TimeoutExpired:
                    if time.monotonic() > deadline:
                        p.kill();p.communicate();return {'execution':'timeout','rows':[]}
            if p.returncode or len(output)>1048576:
                return {'execution':'engine_failure','reason':error.decode(errors='replace')[:1000],'rows':[]}
            try:
                result=json.loads(output)
            except ValueError:
                return {'execution':'engine_failure','rows':[]}
        result['selector']={'id':identifier,'version':selector['version']}
        result['scope']=self.store.scope.key
        evidence_ids={e for row in result.get('rows',[]) for e in row['evidence']}
        result['evidenceDetails']={d['id']:{'documentId':d['id'],'digest':digest(d),
            'revision':d.get('rev'),'sourceUrls':d.get('sourceUrls',[]),
            'provenance':d.get('provenance',{}),
            'observedAt':d.get('observedAt'),'publicationTime':d.get('createdAt'),
            'extractionMethod':'canonical-json-pointer/1',
            'scope':self.store.scope.key} for d in documents if d['id'] in evidence_ids}
        for record in subjects:
            if record['id'] in evidence_ids:
                result['evidenceDetails'][record['id']]={'recordId':record['id'],'kind':record['kind'],
                    'version':record.get('version'),'state':record['state'],'digest':digest(record),
                    'sourceDependencies':self.store.dependencies(record['id']),
                    'extractionMethod':'typed-learning-record/1','scope':self.store.scope.key}
        result['passageEvidence']=passage_evidence
        result['definition']=selector['expression']
        result['assumptions']=['Open-world absence is unresolved.', 'Attributed statements are not adjudicated facts.']
        # Cache explanations with the complete bounded input set: adding or
        # changing evidence creates a new evaluation and changes its identity.
        deps=[d['id'] for d in documents]
        body={'selector':identifier,'result':result,'inputDigest':digest(documents),
              'predicateVersions':[[k,v] for k,v in sorted(definitions)]}
        result['record']=self.store.record('conclusion',body,deps,state='evaluated')
        return result
