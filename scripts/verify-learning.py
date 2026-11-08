#!/usr/bin/env python3
"""Mandatory acceptance gate: dependencies absent is failure, never a green skip."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
p=argparse.ArgumentParser();p.add_argument('--starlang',required=True);a=p.parse_args()
if not shutil.which('swipl'):raise SystemExit('FAIL: SWI-Prolog required')
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from star_kb_learning.contracts import CanonicalAdapter
CanonicalAdapter(a.starlang)
env={**os.environ,'STARLANG_ROOT':a.starlang}
subprocess.run([sys.executable,'-m','unittest','discover','-s','tests','-p','test_learning.py','-v'],cwd=root,env=env,check=True)
subprocess.run([sys.executable,'scripts/learning-demo.py','--starlang',a.starlang,'--output','/tmp/star-kb-learning-evaluation.json'],cwd=root,env=env,check=True)
print('PASS: schema, actual SWI selectors, replay learning, held-out evaluation and recursive reuse')
