#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from star_kb_learning.examples import example
p=argparse.ArgumentParser();p.add_argument('--starlang',required=True);p.add_argument('--swipl',default='swipl');p.add_argument('--output',type=Path)
a=p.parse_args();result=example(a.starlang,a.swipl);text=json.dumps(result,indent=2)+'\n'
if a.output:a.output.write_text(text)
else:print(text,end='')
