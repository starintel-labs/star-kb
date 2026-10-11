"""Installed trees retain byte-level identity; absence of Git grants no trust."""
import os
from pathlib import Path
import shutil
import tempfile
import unittest

from star_kb_service.contracts import Fault
from star_kb_service.identity import verify_adapter_source

ROOT = os.environ.get("STARLANG_IDENTITY_ROOT") or os.environ.get("STARLANG_ROOT")


@unittest.skipUnless(ROOT, "pinned adapter source required")
class AdapterIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "installed"
        shutil.copytree(ROOT, self.root, ignore=shutil.ignore_patterns(".git"))

    def test_installed_without_git_has_exact_content_identity(self):
        self.assertFalse((self.root / ".git").exists())
        verify_adapter_source(self.root)

    def test_mutated_adapter_without_git_fails(self):
        path = self.root / "star-logic-adapter-swi/src/worker.lisp"
        path.write_bytes(path.read_bytes() + b"\n; mutation\n")
        with self.assertRaisesRegex(Fault, "adapter_pin_mismatch"):
            verify_adapter_source(self.root)

    def test_added_or_missing_source_fails(self):
        path = self.root / "injected.lisp"
        path.write_text("; extra executable source")
        with self.assertRaisesRegex(Fault, "adapter_pin_mismatch"):
            verify_adapter_source(self.root)
        path.unlink()
        (self.root / "star-logic-adapter-swi/src/worker.lisp").unlink()
        with self.assertRaisesRegex(Fault, "adapter_pin_mismatch"):
            verify_adapter_source(self.root)

    def test_symlink_substitution_fails(self):
        path = self.root / "star-logic-adapter-swi/src/worker.lisp"
        original = Path(ROOT).resolve() / "star-logic-adapter-swi/src/worker.lisp"
        path.unlink()
        path.symlink_to(original)
        with self.assertRaisesRegex(Fault, "adapter_pin_mismatch"):
            verify_adapter_source(self.root)
