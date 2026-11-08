"""One bounded actor-style event pump. Run repeatedly or follow a replay stream.

Responsibilities share one transactional process in this first implementation;
there is no claim that a live StarIntel collector is connected.
"""
from .contracts import LearningError, canonical, digest
from .discovery import Discovery
from .planning import Planner


class Runtime:
    def __init__(self, store, planner=None):
        self.store=store;self.discovery=Discovery(store);self.planner=planner or Planner(store)

    def tick(self):
        message=self.store.claim()
        if message is None:return None
        identifier=message['id'];stages=[]
        cancel=lambda:self.store.cancelled(identifier,message['attempts'])
        try:
            if cancel():raise LearningError('cancelled')
            if message['kind']=='discover':
                # Repeated queued ingestion events for an unchanged bounded
                # corpus do not rerun synthesis or re-create hypotheses.
                generation=digest([dict(r) for r in self.store.db.execute(
                    'SELECT id,hash FROM documents WHERE scope=? ORDER BY id',(self.store.scope.key,))])
                checkpoint=self.store.db.execute('SELECT value FROM checkpoints WHERE scope=? AND name=?',
                                                (self.store.scope.key,'discovery')).fetchone()
                result=[] if checkpoint and checkpoint[0]==generation else self.discovery.discover(cancelled=cancel)
                if not checkpoint or checkpoint[0]!=generation:
                    stages.append({'strategy':'field-equivalence','execution':'complete','candidates':len(result)})
                    if len(result)<self.store.limits.candidates:
                        try:
                            passages=self.discovery.discover_passages(cancelled=cancel)
                            result+=passages
                            stages.append({'strategy':'passage','execution':'complete','candidates':len(passages)})
                        except LearningError as exc:
                            if str(exc) not in ('passage_input_limit','discovery_budget'):raise
                            # One strategy's input/budget failure must not discard
                            # successful metadata candidates or retry an unchanged
                            # corpus indefinitely. Record the incomplete stage.
                            stage={'strategy':'passage','execution':'input_limit' if str(exc)=='passage_input_limit' else 'budget_exhausted',
                                   'reason':str(exc),'inputGeneration':generation,'candidates':0}
                            self.store.record('experiment',{'procedure':'discovery-stage/1',**stage},
                                              [d['id'] for d in self.store.documents()],state='evaluated')
                            stages.append(stage)
                    else:stages.append({'strategy':'passage','execution':'candidate_limit','candidates':0})
                for item in result:
                    hypothesis=self.store.get(item['hypothesis'])
                    self.planner.plan(item['hypothesis'],hypothesis['requirements'])
                with self.store.db:
                    self.store.db.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?,?)',(self.store.scope.key,'discovery',generation))
            elif message['kind']=='plan':
                result=self.planner.plan(message['payload']['origin'],message['payload']['requirements'])
            elif message['kind']=='evaluate':
                result=self.discovery.evaluate(message['payload']['candidate'],message['payload']['pairs'],cancelled=cancel)
            else:raise LearningError('unsupported_message')
            if cancel():raise LearningError('cancelled')
            self.store.settle(identifier,message['attempts'])
            return {'message':identifier,'state':'done','result':result,'discoveryStages':stages}
        except LearningError as exc:
            self.store.settle(identifier,message['attempts'],str(exc))
            return {'message':identifier,'state':'cancelled' if cancel() else 'retry_or_failed','error':str(exc)}

    def drain(self, maximum=32):
        if type(maximum) is not int or not 1<=maximum<=128:raise LearningError('invalid_step_limit')
        results=[]
        for _ in range(maximum):
            result=self.tick()
            if result is None:break
            results.append(result)
        return results

    def follow(self, adapter, path, *, polls=None, interval=.2, cancelled=lambda:False):
        """Tail an operator-selected canonical NDJSON replay stream.

        Durable byte offsets and document-hash admission make restart idempotent.
        A truncated/replaced stream is explicitly replayed from byte zero.
        No URL, shell command or source-text instruction is dispatched.
        """
        import json
        import time
        from pathlib import Path
        path=Path(path).resolve()
        if polls is not None and (type(polls) is not int or not 1<=polls<=100000):
            raise LearningError('invalid_poll_budget')
        if type(interval) not in (int,float) or not .05<=interval<=5:
            raise LearningError('invalid_poll_interval')
        name='stream:'+digest(str(path));rounds=0;changed=0
        while not cancelled() and (polls is None or rounds<polls):
            stat=path.stat();identity=[stat.st_dev,stat.st_ino]
            old=self.store.db.execute('SELECT value FROM checkpoints WHERE scope=? AND name=?',
                                     (self.store.scope.key,name)).fetchone()
            saved=json.loads(old[0]) if old else {'identity':identity,'offset':0}
            offset=saved['offset'] if saved['identity']==identity and stat.st_size>=saved['offset'] else 0
            with path.open('rb') as stream:
                stream.seek(offset)
                for _ in range(min(16,self.store.limits.queue)):
                    if cancelled():break
                    line=stream.readline(self.store.limits.document_bytes+1)
                    if not line:break
                    if len(line)>self.store.limits.document_bytes:raise LearningError('document_limit')
                    if not line.endswith(b'\n'):
                        break # producer has not committed a complete record yet
                    if line.strip():changed+=self.store.ingest(adapter,json.loads(line))
                    offset=stream.tell()
                    with self.store.db:
                        self.store.db.execute('INSERT OR REPLACE INTO checkpoints VALUES(?,?,?)',
                            (self.store.scope.key,name,canonical({'identity':identity,'offset':offset})))
                    self.drain(maximum=1)
            self.drain(maximum=16)
            rounds+=1
            if polls is None or rounds<polls:time.sleep(interval)
        return {'mode':'canonical-ndjson-replay','changed':changed,'polls':rounds,
                'state':'cancelled' if cancelled() else 'checkpointed'}
