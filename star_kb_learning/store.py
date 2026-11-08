"""Transactional replay cache and learning journal. JSON remains authoritative.

The document table preserves admitted canonical bytes; projections are disposable.
Learning records export as canonical Document extensions, never source facts.
"""
import json
import sqlite3
import time
from prolog_star_kb.projection import iter_scalars
from .contracts import VERSION, KINDS, LearningError, canonical, digest


class Store:
    def __init__(self, path, scope, limits):
        self.scope, self.limits = scope, limits
        self.db = sqlite3.connect(path, timeout=5, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS documents(scope TEXT,id TEXT,body TEXT,hash TEXT,active INTEGER,
          PRIMARY KEY(scope,id));
        CREATE TABLE IF NOT EXISTS history(scope TEXT,id TEXT,hash TEXT,body TEXT,
          PRIMARY KEY(scope,id,hash));
        CREATE TABLE IF NOT EXISTS observations(scope TEXT,doc TEXT,path TEXT,value TEXT,
          PRIMARY KEY(scope,doc,path));
        CREATE INDEX IF NOT EXISTS values_idx ON observations(scope,path,value);
        CREATE TABLE IF NOT EXISTS records(scope TEXT,id TEXT,kind TEXT,body TEXT,state TEXT,
          PRIMARY KEY(scope,id));
        CREATE TABLE IF NOT EXISTS dependencies(scope TEXT,record TEXT,doc TEXT,hash TEXT,
          PRIMARY KEY(scope,record,doc));
        CREATE INDEX IF NOT EXISTS dependencies_idx ON dependencies(scope,doc);
        CREATE TABLE IF NOT EXISTS checkpoints(scope TEXT,name TEXT,value TEXT,PRIMARY KEY(scope,name));
        CREATE TABLE IF NOT EXISTS messages(scope TEXT,id TEXT,kind TEXT,payload TEXT,state TEXT,
          attempts INTEGER DEFAULT 0,lease REAL DEFAULT 0,error TEXT,
          PRIMARY KEY(scope,id));
        ''')

    def close(self):
        self.db.close()

    def documents(self):
        return [json.loads(r[0]) for r in self.db.execute(
            'SELECT body FROM documents WHERE scope=? AND active=1 ORDER BY id', (self.scope.key,))]

    def document(self, identifier):
        row = self.db.execute('SELECT body FROM documents WHERE scope=? AND id=? AND active=1',
                              (self.scope.key, identifier)).fetchone()
        return None if row is None else json.loads(row[0])

    def ingest(self, adapter, document):
        document = adapter.validate(document, self.scope, self.limits)
        key, identifier, hashed = self.scope.key, document['id'], digest(document)
        with self.db:
            old = self.db.execute('SELECT hash FROM documents WHERE scope=? AND id=?', (key, identifier)).fetchone()
            if old and old[0] == hashed:
                return False
            count=self.db.execute('SELECT count(*) FROM documents WHERE scope=?',(key,)).fetchone()[0]
            if old is None and count >= self.limits.documents:
                raise LearningError('corpus_limit')
            if self.db.execute('SELECT count(*) FROM history WHERE scope=?',(key,)).fetchone()[0] >= self.limits.observations:
                raise LearningError('revision_limit')
            values = list(iter_scalars(document))
            total = self.db.execute('SELECT count(*) FROM observations WHERE scope=? AND doc!=?',
                                    (key, identifier)).fetchone()[0]
            if total + len(values) > self.limits.observations:
                raise LearningError('observation_limit')
            self.db.execute('INSERT OR IGNORE INTO history VALUES(?,?,?,?)', (key, identifier, hashed, canonical(document)))
            self.db.execute('INSERT OR REPLACE INTO documents VALUES(?,?,?,?,?)',
                            (key, identifier, canonical(document), hashed, not document.get('deleted', False)))
            self.db.execute('DELETE FROM observations WHERE scope=? AND doc=?', (key, identifier))
            # A selector's open-world result depends on the corpus membership,
            # including future counterevidence, not just previously supporting IDs.
            self.db.execute("UPDATE records SET state='stale' WHERE scope=? AND kind='conclusion'", (key,))
            if not document.get('deleted', False):
                self.db.executemany('INSERT INTO observations VALUES(?,?,?,?)',
                                    [(key, identifier, p, canonical(v)) for p, v in values])
            # Invalidate directly and transitively. Derived records cite their
            # complete source dependency closure, including negative decisions.
            ids = [r[0] for r in self.db.execute('SELECT record FROM dependencies WHERE scope=? AND doc=?', (key, identifier))]
            for rid in ids:
                self.db.execute("UPDATE records SET state='stale' WHERE scope=? AND id=?", (key, rid))
            if document['dtype']=='claim' and not document.get('deleted',False):
                attributed={'contract':VERSION,'kind':'attributed_claim',
                    'document':identifier,'documentDigest':hashed,'span':'/claim',
                    'statement':document['claim'],'claimant':document.get('claimantId'),
                    'predicate':document.get('predicate'),'object':document.get('object'),
                    'polarity':document.get('polarity'),'method':'canonical-claim/1',
                    'assumption':'Source attribution only, not adjudicated truth.'}
                aid='attributed_claim:'+digest(attributed)[:24]
                self.db.execute('INSERT OR IGNORE INTO records VALUES(?,?,?,?,?)',
                    (key,aid,'attributed_claim',canonical(attributed),'evaluated'))
                self.db.execute('INSERT OR REPLACE INTO dependencies VALUES(?,?,?,?)',(key,aid,identifier,hashed))
            self.enqueue('discover', {'document': identifier, 'digest': hashed}, commit=False)
        return True

    def record(self, kind, body, dependencies=(), state='proposed', identifier=None):
        if kind not in KINDS:
            raise LearningError('invalid_record_kind')
        payload = {'contract': VERSION, 'kind': kind, **body}
        identifier = identifier or kind + ':' + digest(payload)[:24]
        key = self.scope.key
        with self.db:
            existing = self.db.execute('SELECT body FROM records WHERE scope=? AND id=?', (key, identifier)).fetchone()
            if existing and existing[0] != canonical(payload):
                raise LearningError('immutable_record_conflict')
            if not existing and self.db.execute('SELECT count(*) FROM records WHERE scope=?',(key,)).fetchone()[0]>=self.limits.observations:
                raise LearningError('record_limit')
            if not existing and kind!='conclusion':
                self.db.execute("UPDATE records SET state='stale' WHERE scope=? AND kind='conclusion'", (key,))
            self.db.execute('INSERT OR IGNORE INTO records VALUES(?,?,?,?,?)',
                            (key, identifier, kind, canonical(payload), state))
            for doc in dependencies:
                row = self.db.execute('SELECT hash FROM documents WHERE scope=? AND id=? AND active=1', (key, doc)).fetchone()
                if row is None:
                    raise LearningError('missing_dependency')
                self.db.execute('INSERT OR REPLACE INTO dependencies VALUES(?,?,?,?)', (key, identifier, doc, row[0]))
        return identifier

    def get(self, identifier):
        row = self.db.execute('SELECT body,state FROM records WHERE scope=? AND id=?', (self.scope.key, identifier)).fetchone()
        if row is None:
            raise LearningError('unknown_record')
        return {**json.loads(row[0]), 'id': identifier, 'state': row[1]}

    def records(self, kind=None):
        sql, args = 'SELECT id FROM records WHERE scope=?', [self.scope.key]
        if kind:
            sql += ' AND kind=?'; args.append(kind)
        return [self.get(r[0]) for r in self.db.execute(sql + ' ORDER BY id', args)]

    def transition(self, identifier, target):
        old = self.get(identifier)['state']
        allowed = {'proposed': {'evaluated', 'rejected'}, 'evaluated': {'accepted', 'rejected'},
                   'accepted': {'retired', 'stale'}, 'stale': {'retired'}, 'rejected': set(), 'retired': set()}
        if target not in allowed.get(old, set()):
            raise LearningError('invalid_transition:' + old + '->' + target)
        with self.db:
            self.db.execute('UPDATE records SET state=? WHERE scope=? AND id=?', (target, self.scope.key, identifier))
            self.db.execute("UPDATE records SET state='stale' WHERE scope=? AND kind='conclusion'", (self.scope.key,))

    def dependencies(self, identifier):
        return [dict(r) for r in self.db.execute('SELECT doc,hash FROM dependencies WHERE scope=? AND record=? ORDER BY doc',
                                               (self.scope.key, identifier))]

    def _recover_exhausted(self):
        self.db.execute("UPDATE messages SET state='failed',error='lease_expired_after_attempt_budget',lease=0 "
                        "WHERE scope=? AND state='running' AND lease<? AND attempts>=?",
                        (self.scope.key,time.time(),self.limits.attempts))

    def enqueue(self, kind, payload, commit=True):
        if kind not in {'discover', 'evaluate', 'plan'}:
            raise LearningError('unsupported_message')
        identifier = digest([kind, payload])
        old = self.db.execute('SELECT 1 FROM messages WHERE scope=? AND id=?', (self.scope.key, identifier)).fetchone()
        if old:
            return identifier
        if self.db.execute('SELECT count(*) FROM messages WHERE scope=?',(self.scope.key,)).fetchone()[0]>=self.limits.observations:
            raise LearningError('journal_limit')
        self._recover_exhausted()
        count = self.db.execute("SELECT count(*) FROM messages WHERE scope=? AND state IN ('pending','running')", (self.scope.key,)).fetchone()[0]
        if count >= self.limits.queue:
            raise LearningError('queue_limit')
        self.db.execute('INSERT INTO messages(scope,id,kind,payload,state) VALUES(?,?,?,?,?)',
                        (self.scope.key, identifier, kind, canonical(payload), 'pending'))
        if commit:
            self.db.commit()
        return identifier

    def claim(self, lease_seconds=30):
        # BEGIN IMMEDIATE is an atomic claim across connections/processes.
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self._recover_exhausted()
            row = self.db.execute("SELECT * FROM messages WHERE scope=? AND (state='pending' OR (state='running' AND lease<?)) AND attempts<? ORDER BY rowid LIMIT 1",
                                  (self.scope.key, time.time(), self.limits.attempts)).fetchone()
            if row:
                self.db.execute("UPDATE messages SET state='running',attempts=attempts+1,lease=? WHERE scope=? AND id=?",
                                (time.time() + lease_seconds, self.scope.key, row['id']))
            self.db.commit()
            return None if row is None else {**dict(row), 'attempts': row['attempts']+1, 'payload': json.loads(row['payload'])}
        except BaseException:
            self.db.rollback(); raise

    def settle(self, identifier, attempt, error=None):
        with self.db:
            self.db.execute("UPDATE messages SET state=CASE WHEN ? IS NULL THEN 'done' WHEN attempts>=? THEN 'failed' ELSE 'pending' END,error=?,lease=0 WHERE scope=? AND id=? AND state='running' AND attempts=?",
                            (error, self.limits.attempts, error, self.scope.key, identifier, attempt))

    def cancel(self, identifier):
        with self.db:
            self.db.execute("UPDATE messages SET state='cancelled' WHERE scope=? AND id=? AND state IN ('pending','running')", (self.scope.key, identifier))

    def cancelled(self, identifier, attempt=None):
        row = self.db.execute('SELECT state,attempts FROM messages WHERE scope=? AND id=?', (self.scope.key, identifier)).fetchone()
        return row is None or row[0] == 'cancelled' or (attempt is not None and (row[1] != attempt or row[0] != 'running'))

    def export(self, identifier):
        record = self.get(identifier)
        return {'id': identifier, 'dataset': self.scope.dataset, 'dtype': 'document',
                'schemaVersion': '0.10.1', 'extensions': {
                    'starKbScope': {'project': self.scope.project, 'operation': self.scope.operation},
                    'starKbLearning': record},
                'provenance': {'method': VERSION, 'evidence': self.dependencies(identifier)}}
