"""Supervise pinned StarLang adapter processes, one immutable scope per worker."""
import os
import ctypes
import sys
from pathlib import Path
import selectors
import signal
import subprocess
import time
from .contracts import Fault, encode, decode, MAX_BYTES
from .projection import STARLANG_COMMIT
from .contracts import ENGINE_EOF_SECONDS, ENGINE_WAIT_SECONDS, ENGINE_REAP_SECONDS


class Engine:
    def __init__(self, starlang, sbcl, swipl, snapshot):
        # Linux subreaper: adopt/reap the owned SWI grandchild if SBCL crashes.
        # waitpid below is restricted to our dedicated worker session.
        if sys.platform != "linux" or ctypes.CDLL(None, use_errno=True).prctl(36, 1, 0, 0, 0) != 0:
            raise Fault("unsupported_supervision_platform")
        self.starlang = Path(starlang).resolve()
        self.sbcl, self.swipl = str(Path(sbcl).resolve()), str(Path(swipl).resolve())
        head = subprocess.check_output(["git", "-C", str(self.starlang), "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.check_output(["git", "-C", str(self.starlang), "status", "--porcelain", "--untracked-files=no"])
        if head != STARLANG_COMMIT or dirty:
            raise Fault("adapter_pin_mismatch")
        self.snapshot = snapshot
        self.process = None
        self.metadata = {}
        self.count = 0

    def start(self, seconds=45):
        env = os.environ.copy()
        env["STARLANG_SWI_EXECUTABLE"] = self.swipl
        # Explicit source registries; operator runtime configuration may add Lisp
        # dependencies. Never accept a source path from an RPC request.
        registry = env.get("CL_SOURCE_REGISTRY", "")
        env["CL_SOURCE_REGISTRY"] = str(self.starlang) + "//:" + registry
        self.process = subprocess.Popen(
            [self.sbcl, "--noinform", "--disable-debugger", "--script",
             str(Path(__file__).resolve().parents[1] / "service/worker.lisp")],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=env, start_new_session=True, bufsize=0)
        self.count = 0
        try:
            self.metadata = self.exchange({"operation": "snapshot", "rows": self.snapshot.rows}, seconds)
            self.metadata.pop("state", None)
        except BaseException:
            self.close()
            raise

    def exchange(self, request, seconds):
        if self.process is None or self.process.poll() is not None:
            self.close()
            raise Fault("engine_failure")
        deadline = time.monotonic() + seconds
        payload = encode(request) + b"\n"
        result = bytearray()
        try:
            # Both directions are nonblocking: a stopped worker cannot strand
            # shutdown/cancellation behind a full pipe.
            with selectors.DefaultSelector() as selector:
                os.set_blocking(self.process.stdin.fileno(), False)
                os.set_blocking(self.process.stdout.fileno(), False)
                selector.register(self.process.stdin, selectors.EVENT_WRITE)
                selector.register(self.process.stdout, selectors.EVENT_READ)
                offset = 0
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise Fault("deadline_exceeded")
                    for key, _ in selector.select(remaining):
                        if key.fileobj is self.process.stdin:
                            offset += os.write(key.fd, payload[offset:])
                            if offset == len(payload):
                                selector.unregister(key.fileobj)
                        else:
                            data = os.read(key.fd, 8192)
                            if not data:
                                raise Fault("engine_failure")
                            result.extend(data)
                            if len(result) > MAX_BYTES:
                                raise Fault("output_limit")
                            if b"\n" in result:
                                reply = decode(result)
                                if "error" in reply:
                                    raise Fault(reply["error"])
                                return reply
        except (OSError, ValueError):
            self.close()
            raise Fault("engine_failure") from None
        except Fault as exc:
            if exc.code != "query_limit":
                self.close()
            raise

    def query(self, parameters, limits, seconds):
        deadline = time.monotonic() + seconds
        if self.process is None or self.count >= 128:
            self.close()
            self.start(max(0, deadline - time.monotonic()))  # explicit replay of the trusted immutable snapshot
        self.count += 1
        return self.exchange({"operation": "query", "parameters": parameters, "limits": limits}, max(0, deadline - time.monotonic()))

    def close(self):
        process, self.process = self.process, None
        if process is not None:
            # EOF allows the adapter to shut down and reap SWI normally.
            process.stdin.close()
            try:
                process.wait(timeout=ENGINE_EOF_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            # SBCL's process port puts SWI in its own process group, but
            # it retains our dedicated session ID. Pin each owned descendant
            # with pidfd before signaling; do not kill unrelated process groups.
            owned = []
            for pid in self.session_pids(process.pid):
                fd = None
                try:
                    fd = os.pidfd_open(pid)
                    if os.getsid(pid) == process.pid:
                        signal.pidfd_send_signal(fd, signal.SIGKILL)
                        owned.append(pid)
                except ProcessLookupError:
                    pass
                finally:
                    if fd is not None:
                        os.close(fd)
            process.wait(timeout=ENGINE_WAIT_SECONDS)
            deadline = time.monotonic() + ENGINE_REAP_SECONDS
            for pid in owned:
                if pid == process.pid:
                    continue
                while True:
                    try:
                        reaped, _ = os.waitpid(pid, os.WNOHANG)
                    except ChildProcessError:
                        break  # already reaped by SBCL
                    if reaped:
                        break
                    if time.monotonic() >= deadline:
                        raise Fault("cleanup_failure")
                    time.sleep(.005)
            process.stdout.close()

    @staticmethod
    def session_pids(session):
        result = []
        for entry in Path("/proc").iterdir():
            if entry.name.isdecimal():
                try:
                    pid = int(entry.name)
                    if os.getsid(pid) == session:
                        result.append(pid)
                except ProcessLookupError:
                    pass
        return result
