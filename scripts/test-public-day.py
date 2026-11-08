#!/usr/bin/env python3
"""Bounded local test of a user-provided public Reddit canonical day export.

No network access, credentials, external collection or publication. Original
canonical bytes are preserved in the input; scoped copies are an explicit replay
adapter and never posted back to StarIntel.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from star_kb_learning.contracts import CanonicalAdapter, Limits, Scope, canonical, digest
from star_kb_learning.discovery import Discovery
from star_kb_learning.store import Store
from star_kb_learning.selectors import Selectors


def run(input_path,starlang,day,dataset='reddit',source='https://ingest.starintel.actor',swipl='swipl'):
    start=int(dt.datetime.combine(dt.date.fromisoformat(day),dt.time(),dt.timezone.utc).timestamp());end=start+86400
    limits=Limits(steps=100000);adapter=CanonicalAdapter(starlang);scope=Scope('public-day-test','public-reddit-replay',dataset)
    accepted=[];excluded=0;originals=[]
    # Bound scanned input as well as admitted corpus; export an actual day at
    # source rather than asking this tool to scan an arbitrary entire dump.
    with Path(input_path).open('rb') as stream:
        for n in range(2049):
            line=stream.readline(limits.document_bytes+1)
            if not line:break
            if n==2048 or len(line)>limits.document_bytes:raise ValueError('export_scan_limit')
            if not line.strip():continue
            document=json.loads(line)
            validator=adapter.validators.get(document.get('dtype'))
            if validator is None or not validator.is_valid(document) or document.get('schemaVersion')!='0.10.1':
                raise ValueError('canonical_0.10.1_export_required')
            if document.get('dataset')!=dataset:raise ValueError('dataset_mismatch')
            timestamp=document.get('createdAt')
            if type(timestamp) is not int or not start<=timestamp<end:
                excluded+=1;continue
            if len(accepted)>=limits.documents:raise ValueError('day_sample_limit_256')
            originals.append({'document':document['id'],'originalDigest':digest(document),
                'publicationField':'/createdAt','publicationTime':timestamp})
            replay=json.loads(canonical(document))
            replay.setdefault('extensions',{})['starKbScope']={'project':scope.project,'operation':scope.operation}
            replay.setdefault('transformHistory',[]).append({'method':'public-day-scope-replay/1',
                'originalDigest':digest(document),'exportSource':source})
            accepted.append(replay)
    if not accepted:raise ValueError('no_records_in_requested_day')
    with tempfile.TemporaryDirectory(prefix='star-kb-public-day-') as temp:
        store=Store(Path(temp)/'day.sqlite',scope,limits)
        try:
            for d in accepted:store.ingest(adapter,d)
            learner=Discovery(store);candidates=learner.discover()
            s=Selectors(store,swipl)
            sid=s.create('publication-day',1,{'during':['/createdAt',start,end-1]},
                'Recorded creation timestamps in the requested UTC day. Not collection or keystroke times.')
            result=s.evaluate(sid)
            if result['execution']!='complete':raise RuntimeError(result)
            return {'mode':'user-provided-public-export-replay','liveApiRead':False,'source':source,
                'dataset':dataset,'utcDay':day,'accepted':len(accepted),'excludedOutsideWindowOrMissingTime':excluded,
                'originalRecords':originals,'selectors':result,'candidatePatterns':candidates,
                'acceptance':'No predicates accepted without separate independently labelled evaluation evidence.',
                'scopeAdapter':'Explicit local namespaced scope binding; original export is not modified.'}
        finally:store.close()


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('input',type=Path);p.add_argument('--starlang',required=True)
    p.add_argument('--day',required=True);p.add_argument('--dataset',default='reddit');p.add_argument('--swipl',default='swipl');p.add_argument('--output',required=True,type=Path)
    a=p.parse_args();a.output.write_text(json.dumps(run(a.input,a.starlang,a.day,a.dataset,swipl=a.swipl),indent=2)+'\n')
