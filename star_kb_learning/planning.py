"""Evidence planning is pure: collector capabilities do not execute themselves."""
from dataclasses import dataclass
from .contracts import LearningError, digest
from .discovery import value_at


@dataclass(frozen=True)
class Capability:
    name: str
    paths: tuple
    inputs: tuple
    cost: int
    coverage: str
    access: str
    mode: str = 'replay'

    def __post_init__(self):
        if self.mode not in ('replay','live') or type(self.cost) is not int or self.cost < 0:
            raise LearningError('invalid_capability')


class Planner:
    def __init__(self, store, capabilities=()):
        self.store,self.capabilities=store,tuple(capabilities)

    def plan(self, origin, requirements, budget=10):
        if type(budget) is not int or not 0<=budget<=1000:raise LearningError('invalid_budget')
        self.store.get(origin) # scope-bound origin mandatory
        docs=self.store.documents();requests=[]
        for requirement in requirements:
            if not isinstance(requirement,dict) or not {'path','desired','role'}<=set(requirement):raise LearningError('invalid_requirement')
            path=requirement['path']
            existing=[d['id'] for d in docs if value_at(d,path) is not None]
            methods=[{'name':c.name,'cost':c.cost,'coverage':c.coverage,'access':c.access,'mode':c.mode,
                      'requiredInputs':list(c.inputs)} for c in self.capabilities if path in c.paths and c.cost<=budget]
            # Retrieval alone does not provide independent validation labels.
            unresolved=not existing or requirement['role'] in ('comparison','counterexample','context')
            state='unresolved' if unresolved and not methods else 'requested' if unresolved else 'existing'
            body={'origin':origin,'unresolvedCondition':requirement,'desiredEvidence':requirement['desired'],
                'existingDocuments':existing,'candidateMethods':methods,'scope':self.store.scope.key,
                'budget':budget,'state':state,
                'reason':'independent/discriminating evidence required' if unresolved else 'available in local records',
                'completion':['valid provenance and scope','support and refutation coverage','independent source labels'],
                'stop':['budget exhausted','capability unavailable','discriminating evidence acquired'],
                'valueBasis':'Resolve a specified comparison or counterexample; no invented numeric confidence.',
                'collectionExecuted':False}
            requests.append(self.store.record('evidence_request',body,existing,state='proposed'))
        return requests


class ReplayCollector:
    """Explicit fixture replay, never represented as live actor dispatch."""
    def __init__(self, adapter, store, name, documents):
        self.adapter,self.store,self.name,self.documents=adapter,store,name,documents

    def execute(self, request_id, cancelled=lambda:False):
        request=self.store.get(request_id)
        if request['kind']!='evidence_request' or not any(m['name']==self.name and m['mode']=='replay' for m in request['candidateMethods']):
            raise LearningError('unsupported_collection')
        if len(self.documents)>request['budget']:raise LearningError('acquisition_budget')
        imported=[]
        for document in self.documents:
            if cancelled():raise LearningError('cancelled')
            # Schema admission and scoped provenance apply to fixtures too.
            if not document.get('provenance',{}).get('replay'):
                raise LearningError('replay_provenance_required')
            self.store.ingest(self.adapter,document);imported.append(document['id'])
        return {'mode':'replay','request':request_id,'documents':imported,'cost':len(imported)}
