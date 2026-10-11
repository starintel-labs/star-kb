"""Actual installed CLI admission/query and owned-process shutdown proof."""
import json
import os
from pathlib import Path
import secrets
import selectors
import subprocess
import tempfile
import unittest
import urllib.request
import urllib.error

COMMAND = os.environ.get("STAR_KB_INSTALLED_COMMAND")


@unittest.skipUnless(COMMAND, "installed package command required (mandatory Nix check)")
class InstalledServiceTests(unittest.TestCase):
    def test_installed_cli_http_zmq_parity_and_shutdown(self):
        import zmq
        import star_kb_service
        from star_kb_service.contracts import PROTOCOL, encode
        from star_kb_service.engine import Engine
        from star_kb_service.projection import Snapshot, STARLANG_COMMIT
        from test_shared_service import document
        root = Path(os.environ["STAR_KB_INSTALLED_ROOT"]).resolve()
        self.assertTrue(Path(star_kb_service.__file__).resolve().is_relative_to(root))
        token = secrets.token_urlsafe(32)
        config = {"scopes": [{"tenant": "a", "dataset": "d", "documents": [document()]}],
                  "credentials": [{"identity": "alice", "tenant": "a", "dataset": "d", "tokenEnv": "KB_INSTALLED_TOKEN"}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(config))
            env = os.environ.copy()
            env["KB_INSTALLED_TOKEN"] = token
            # Installed wrapper, not the caller, must supply all runtime paths.
            for key in ("PYTHONPATH", "STARLANG_ROOT", "STAR_KB_SBCL", "STARLANG_SWI_EXECUTABLE", "CL_SOURCE_REGISTRY"):
                env.pop(key, None)
            with tempfile.TemporaryFile() as errors:
                process = subprocess.Popen([COMMAND, str(path), "--http-port", "0"], cwd=directory,
                    env=env, stdout=subprocess.PIPE, stderr=errors, text=True)
                workers = []
                try:
                    with selectors.DefaultSelector() as ready:
                        ready.register(process.stdout, selectors.EVENT_READ)
                        self.assertTrue(ready.select(60), "installed CLI readiness deadline")
                    line = process.stdout.readline()
                    self.assertTrue(line, "installed CLI exited before readiness")
                    endpoints = json.loads(line)
                    workers = [int(pid) for pid in Path(f"/proc/{process.pid}/task/{process.pid}/children").read_text().split()]
                    self.assertTrue(workers)
                    def http(request, secret=token):
                        req = urllib.request.Request(endpoints["http"], data=encode(request),
                            headers={"Authorization": "Bearer " + secret})
                        try:
                            reply = urllib.request.urlopen(req, timeout=15)
                        except urllib.error.HTTPError as exc:
                            reply = exc
                        with reply:
                            return json.load(reply)
                    snapshot = Snapshot(os.environ["STARLANG_ROOT"], "a", "d", [document()])
                    query = {"protocol": PROTOCOL, "id": "installed-query", "operation": "relation-neighbors",
                        "generation": snapshot.generation,
                        "parameters": {"node": "a", "predicate": "owns", "direction": "out"}}
                    self.assertEqual("unauthorized", http(query, "invalid")["error"])
                    pending = http(query)
                    self.assertEqual("pending", pending["state"])
                    with zmq.Context() as context, context.socket(zmq.REQ) as sock:
                        sock.plain_username = b"alice"
                        sock.plain_password = token.encode()
                        sock.setsockopt(zmq.LINGER, 0)
                        sock.setsockopt(zmq.RCVTIMEO, 15000)
                        sock.connect(endpoints["zmq"])
                        sock.send(encode({"authorization": token, "request": query}))
                        self.assertEqual(pending, json.loads(sock.recv()))
                        request = {"protocol": PROTOCOL, "id": "installed-next", "operation": "next", "handle": pending["handle"]}
                        sock.send(encode({"authorization": token, "request": request}))
                        result = json.loads(sock.recv())
                    self.assertEqual(result, http(request))
                    self.assertEqual("completed", result["state"])
                    self.assertEqual("b", result["answers"][0]["destination"])
                    self.assertEqual(document()["evidence"], result["answers"][0]["provenance"]["evidence"])
                    self.assertIn(STARLANG_COMMIT, json.dumps(result))
                finally:
                    process.terminate()
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                        raise
                    process.stdout.close()
                self.assertEqual(0, process.returncode)
                for pid in workers:
                    self.assertFalse(Path(f"/proc/{pid}").exists(), "owned Lisp worker survived shutdown")
                    self.assertEqual([], Engine.session_pids(pid), "owned SWI worker survived shutdown")
