"""Scoped document->Prolog and bounded query actor handlers.

These are local actor-model message handlers behind the existing dispatcher, not
unverified RabbitMQ deployment. Only configured capabilities can invoke them.
"""
import threading
import json
from jsonschema import Draft202012Validator
from prolog_star_kb.projection import _walk
from star_kb_learning.contracts import CanonicalAdapter, Limits, Scope, LearningError, digest
from star_kb_learning.store import Store
from star_kb_learning.selectors import Selectors
from .contracts import Fault, closed, encode, MAX_BYTES

ACTOR_OPERATIONS = frozenset({'document-project','prolog-query'})


class KnowledgeActors:
    def __init__(self, starlang, scope, path, swipl='swipl', documents=(), limits=None):
        self.scope=scope
        self.limits=limits or Limits()
        self.adapter=CanonicalAdapter(starlang)
        self.store=Store(path,scope,self.limits)
        self.selectors=Selectors(self.store,swipl)
        self.lock=threading.RLock()
        self.closed=False
        try:
            for document in documents:self.store.ingest(self.adapter,document)
        except BaseException:
            self.store.close();raise

    @property
    def generation(self):
        return 'sha256:'+digest([self.scope.key,self.store.documents()])

    def manifests(self):
        validator=Draft202012Validator({'$ref':'#/$defs/ActorManifest','$defs':self.adapter.defs})
        manifests=[]
        for operation in sorted(ACTOR_OPERATIONS):
            manifest={'id':'star-kb:'+operation,'dataset':self.scope.dataset,'dtype':'actor-manifest',
                'schemaVersion':'0.10.1','actor':'star-kb-'+operation,'actorVersion':'0.1.0',
                'runtime':'python-swi','accepts':['canonical-document/0.10.1' if operation=='document-project' else 'star.kb.selector/1'],
                'produces':['prolog-json-pointer-facts/1' if operation=='document-project' else 'star.kb.selector-result/1'],
                'capabilities':[operation],'endpoint':'/v1/kb',
                'metadata':{'registration':'local-handler-only','transport':'existing-authenticated-service',
                    'project':self.scope.project,'operation':self.scope.operation,'liveCollection':False}}
            if not validator.is_valid(manifest):raise Fault('invalid_actor_manifest')
            manifests.append(manifest)
        return manifests

    def health(self):
        with self.lock:
            return {'state':'ready' if not self.closed else 'closed','generation':self.generation,
                'scope':{'tenant':self.scope.tenant,'project':self.scope.project,'operation':self.scope.operation,'dataset':self.scope.dataset},
                'operations':sorted(ACTOR_OPERATIONS),'canonicalSchema':'0.10.1',
                'store':'local-replay-cache','liveCollection':False,'actors':self.manifests()}

    def invoke(self, operation, parameters, generation=None, cancelled=lambda:False):
        with self.lock:
            try:
                if self.closed or cancelled():raise Fault('service_closed')
                if operation=='document-project':
                    closed(parameters,('document',),('previousDigest',))
                    document=self.adapter.validate(parameters['document'],self.scope,self.limits)
                    previous=self.store.db.execute('SELECT body FROM documents WHERE scope=? AND id=?',
                        (self.scope.key,document['id'])).fetchone()
                    if previous is not None and digest(json.loads(previous[0]))!=digest(document):
                        if parameters.get('previousDigest')!=digest(json.loads(previous[0])):
                            raise Fault('projection_revision_conflict')
                    elif previous is None and 'previousDigest' in parameters:
                        raise Fault('projection_revision_conflict')
                    # Project first; an oversized response must not mutate cache.
                    facts=[f.render() for f in _walk(document['id'],document)]
                    result={'state':'complete','operation':operation,'documentId':document['id'],
                        'documentDigest':digest(document),'facts':facts,
                        'provenance':{'documentId':document['id'],'revision':document.get('rev'),
                                      'method':'canonical-json-pointer/1','source':document.get('sourceUrls',[])},
                        'durableCanonicalWrite':False,'projectionOnly':True}
                    if len(encode(result))>MAX_BYTES-2048:raise Fault('output_limit')
                    if cancelled():raise Fault('cancelled')
                    result['changed']=self.store.ingest(self.adapter,document)
                    result['generation']=self.generation
                    return result
                if operation=='prolog-query':
                    closed(parameters,('selector',))
                    if generation!=self.generation:raise Fault('stale_generation')
                    definition=closed(parameters['selector'],('name','version','expression','purpose'),('subject_type','parameters'))
                    sid=self.selectors.create(**definition)
                    result=self.selectors.evaluate(sid,lambda:self.closed or cancelled())
                    result={'state':'complete' if result['execution']=='complete' else 'failed',
                        'operation':operation,'generation':self.generation,**result}
                    if len(encode(result))>MAX_BYTES-2048:raise Fault('output_limit')
                    return result
                raise Fault('unsupported_operation')
            except LearningError as exc:
                raise Fault(str(exc).split(':')[0]) from None

    def close(self):
        self.closed=True
        with self.lock:self.store.close()
