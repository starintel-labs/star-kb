#!/usr/bin/env python3
"""Text-derived discovery, held-out evaluation and actual SWI predicate reuse."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from star_kb_learning.examples import passage_example
p=argparse.ArgumentParser();p.add_argument('--starlang',required=True);p.add_argument('--output',type=Path,required=True)
a=p.parse_args();report=passage_example(a.starlang)
a.output.write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({'reports':[{k:r[k] for k in ('domain','chronologicalBaseline','exactDocumentHashBaseline','learned','compute')} for r in report['reports']],'output':str(a.output)},indent=2))
