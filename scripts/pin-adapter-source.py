#!/usr/bin/env python3
"""Maintain the reviewed adapter byte inventory from its exact clean Git pin."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from star_kb_service.projection import STARLANG_COMMIT

parser = argparse.ArgumentParser()
parser.add_argument("source", type=Path)
parser.add_argument("--check", action="store_true")
args = parser.parse_args()
def git(*words):
    return subprocess.check_output(["git", "-C", str(args.source), *words], text=True).strip()
if git("rev-parse", "HEAD") != STARLANG_COMMIT or git("status", "--porcelain"):
    raise SystemExit("adapter checkout must be clean and match STARLANG_COMMIT")
files = git("ls-files").splitlines()
manifest = {"commit": STARLANG_COMMIT,
            "files": {p: hashlib.sha256((args.source / p).read_bytes()).hexdigest() for p in files}}
payload = json.dumps(manifest, sort_keys=True, indent=2) + "\n"
output = ROOT / "star_kb_service/adapter-source.json"
if args.check:
    if output.read_text() != payload:
        raise SystemExit("adapter byte inventory drift")
    print("adapter byte inventory: exact pin verified")
else:
    output.write_text(payload)
