import argparse
import json
import sys
from pathlib import Path
from .contracts import CanonicalAdapter, Limits, Scope
from .store import Store
from .runtime import Runtime
from .selectors import Selectors


def main():
    p=argparse.ArgumentParser(description='Bounded continuous symbolic learning; replay, not live collection.')
    p.add_argument('--starlang',required=True)
    p.add_argument('--store',required=True)
    p.add_argument('--project',required=True);p.add_argument('--operation',required=True);p.add_argument('--dataset',required=True)
    sub=p.add_subparsers(dest='command',required=True)
    replay=sub.add_parser('replay');replay.add_argument('input',type=Path)
    sub.add_parser('tick')
    follow=sub.add_parser('follow');follow.add_argument('input',type=Path);follow.add_argument('--polls',type=int)

    query=sub.add_parser('select');query.add_argument('definition',type=Path);query.add_argument('--swipl',default='swipl')
    export=sub.add_parser('export');export.add_argument('id')
    args=p.parse_args()
    adapter=CanonicalAdapter(args.starlang)
    store=Store(args.store,Scope(args.project,args.operation,args.dataset),Limits())
    try:
        if args.command=='replay':
            changed=0
            # Bounded each line and transaction, so interruption is replay-safe.
            with args.input.open() as stream:
                for line in stream:
                    if len(line.encode())>store.limits.document_bytes:raise ValueError('document_limit')
                    if line.strip():changed+=store.ingest(adapter,json.loads(line))
            result={'mode':'replay','changed':changed,'events':Runtime(store).drain()}
        elif args.command=='tick':result=Runtime(store).drain()
        elif args.command=='follow':result=Runtime(store).follow(adapter,args.input,polls=args.polls)
        elif args.command=='select':
            definition=json.loads(args.definition.read_text())
            selectors=Selectors(store,args.swipl)
            result=selectors.evaluate(selectors.create(**definition))
        else:
            result=store.export(args.id);adapter.validate(result,store.scope,store.limits)
        print(json.dumps(result,indent=2))
    finally:store.close()


if __name__=='__main__':main()
