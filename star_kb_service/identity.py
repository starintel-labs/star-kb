"""Verify the same pinned source in a Git checkout or an installed source tree."""
import hashlib
import json
from pathlib import Path
import subprocess
from .contracts import Fault
from .projection import STARLANG_COMMIT


def verify_adapter_source(root):
    root = Path(root).resolve()
    manifest = json.loads(Path(__file__).with_name("adapter-source.json").read_bytes())
    if manifest["commit"] != STARLANG_COMMIT:
        raise Fault("adapter_pin_mismatch")
    if (root / ".git").exists():
        try:
            head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            dirty = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=no"])
        except (OSError, subprocess.CalledProcessError):
            raise Fault("adapter_pin_mismatch") from None
        if head != STARLANG_COMMIT or dirty:
            raise Fault("adapter_pin_mismatch")
    # A missing .git is not a bypass: compare every pinned byte and reject extra
    # files. The trusted manifest ships with KB, never with RPC/operator input.
    expected = manifest["files"]
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*")
              if p.is_file() and ".git" not in p.relative_to(root).parts}
    if actual != set(expected):
        raise Fault("adapter_pin_mismatch")
    try:
        for name, digest in expected.items():
            path = root / name
            if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise Fault("adapter_pin_mismatch")
    except OSError:
        raise Fault("adapter_pin_mismatch") from None
