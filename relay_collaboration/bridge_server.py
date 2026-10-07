"""Local authenticated coordination broker. This service never executes tasks."""

from __future__ import annotations

import argparse
import csv
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import re
import secrets
import stat
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import uuid
from urllib.parse import urlsplit, parse_qs

from relay_collaboration.bridge_core import Broker

PROTOCOL = "dual-agent/v1"
MAX_BODY_BYTES = 4 * 1024 * 1024
ACTORS = ("codex", "claude")
_WINDOWS_SID = None


def error(code, message):
    return {"ok": False, "error": {"code": code, "message": message}}


def is_uuid(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def request_digest(envelope):
    clean = dict(envelope)
    clean.pop("auth_token", None)
    return hashlib.sha256(json.dumps(clean, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def _is_link(path):
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def safe_path(root, path):
    """Reject symlinks/junctions and resolved paths outside the broker root."""
    root, path = Path(root).resolve(), Path(path).absolute()
    try:
        parts = path.relative_to(root).parts
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise ValueError("path is outside broker root") from exc
    cursor = root
    for part in parts:
        cursor = cursor / part
        if _is_link(cursor):
            raise ValueError("symlinks and junctions are not permitted")
    return path


def private_permissions(path, directory=False):
    """Use POSIX modes or Windows ACLs; chmod alone is insufficient on Windows."""
    global _WINDOWS_SID
    path = Path(path)
    if os.name != "nt":
        path.chmod(0o700 if directory else 0o600)
        return
    system32 = Path(os.environ["SystemRoot"]) / "System32"
    flags = {"creationflags": subprocess.CREATE_NO_WINDOW}
    if _WINDOWS_SID is None:
        result = subprocess.run(
            [str(system32 / "whoami.exe"), "/user", "/fo", "csv", "/nh"],
            capture_output=True, check=True, text=True, **flags,
        )
        rows = list(csv.reader(io.StringIO(result.stdout.strip())))
        sid = rows[0][1] if rows and len(rows[0]) == 2 else ""
        if not re.fullmatch(r"S-1-\d+(?:-\d+)+", sid):
            raise RuntimeError("could not determine current Windows user SID")
        _WINDOWS_SID = sid
    rights = "(OI)(CI)F" if directory else "F"
    subprocess.run(
        [str(system32 / "icacls.exe"), str(path), "/inheritance:r", "/grant:r",
         f"*{_WINDOWS_SID}:{rights}"],
        capture_output=True, check=True, **flags,
    )


def atomic_json(root, destination, value):
    destination = safe_path(root, destination)
    temporary = destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp")
    safe_path(root, temporary)
    payload = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        safe_path(root, destination)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def bounded_read(root, path, maximum=MAX_BODY_BYTES):
    path = safe_path(root, path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("only regular files are permitted")
        if metadata.st_size > maximum:
            raise ValueError("file exceeds size limit")
        payload = source.read(maximum + 1)
        if len(payload) > maximum:
            raise ValueError("file exceeds size limit")
        return payload


class ProcessLock:
    """Advisory OS lock released by the OS after a crash, without PID guessing."""

    def __init__(self, root):
        self.path = safe_path(root, Path(root) / "state" / "server.lock")
        self.stream = self.path.open("a+b")
        if self.path.stat().st_size == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self.stream.close()
            self.stream = None
            raise RuntimeError("another broker already owns this root") from exc

    def close(self):
        if self.stream is None:
            return
        try:
            self.stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()
            self.stream = None


class BridgeService:
    def __init__(self, root, poll_seconds=0.3):
        if not 0.02 <= poll_seconds <= 60:
            raise ValueError("poll_seconds must be between 0.02 and 60")
        original_root = Path(root).absolute()
        if _is_link(original_root):
            raise ValueError("broker root cannot be a symlink or junction")
        original_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = original_root.resolve()
        for name in ("state", "credentials", "requests", "processing", "responses", "processed"):
            directory = safe_path(self.root, self.root / name)
            directory.mkdir(exist_ok=True, mode=0o700)
        self.lock = ProcessLock(self.root)
        self.httpd = None
        self.http_thread = None
        self.file_thread = None
        self.stop_event = threading.Event()
        self.poll_seconds = poll_seconds
        self.file_errors = 0
        self.call_lock = threading.RLock()
        self.file_lock = threading.Lock()
        try:
            private_permissions(self.root / "credentials", directory=True)
            self.tokens = {}
            for actor in ACTORS:
                path = safe_path(self.root, self.root / "credentials" / f"{actor}.token")
                if not path.exists():
                    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(descriptor, "w", encoding="ascii") as stream:
                        stream.write(secrets.token_urlsafe(48) + "\n")
                private_permissions(path)
                token = bounded_read(self.root, path, 256).decode("ascii").strip()
                if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", token):
                    raise ValueError(f"invalid credential format for {actor}")
                self.tokens[actor] = token
            database = safe_path(self.root, self.root / "state" / "bridge.sqlite")
            self.broker = Broker(database)
        except Exception:
            self.lock.close()
            raise

    def dispatch(self, envelope, token):
        if not isinstance(envelope, dict):
            return error("bad_request", "request must be a JSON object")
        actor = envelope.get("actor")
        expected = self.tokens.get(actor) if isinstance(actor, str) else None
        if (expected is None or not isinstance(token, str)
                or not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", token)
                or not hmac.compare_digest(token, expected)):
            return error("unauthorized", "actor credential is missing or invalid")
        clean = dict(envelope)
        clean.pop("auth_token", None)
        try:
            with self.call_lock:
                return self.broker.handle(clean)
        except Exception:
            return error("internal_error", "broker could not process request")

    def observe(self, actor, token, kind, args):
        expected = self.tokens.get(actor) if isinstance(actor, str) else None
        if (expected is None or not isinstance(token, str)
                or not re.fullmatch(r'[A-Za-z0-9_-]{43,128}', token)
                or not hmac.compare_digest(token, expected)):
            return error('unauthorized', 'actor credential is missing or invalid')
        return self.broker.observe(actor, kind, args)

    def process_files_once(self):
        """One bad request cannot stop later requests from being processed."""
        with self.file_lock:
            self._process_files_locked()

    def _process_files_locked(self):
        directory = safe_path(self.root, self.root / "requests")
        processing = safe_path(self.root, self.root / "processing")
        # Captured files survive a crash and are replayed before newly queued work.
        candidates = sorted(processing.glob("*.json")) + sorted(directory.glob("*.json"))
        for candidate in candidates:
            path = candidate
            if not is_uuid(path.stem):
                continue
            envelope = None
            try:
                safe_path(self.root, path)
                if path.parent == directory:
                    captured = safe_path(self.root, processing / path.name)
                    if captured.exists():
                        continue
                    os.replace(path, captured)
                    path = captured
                envelope = json.loads(bounded_read(self.root, path).decode("utf-8"))
                if not isinstance(envelope, dict) or envelope.get("request_id") != path.stem:
                    response = error("bad_request", "request_id must match the UUID filename")
                else:
                    response = self.dispatch(envelope, envelope.get("auth_token"))
            except FileNotFoundError:
                continue
            except (ValueError, UnicodeError, OSError, RecursionError):
                response = error("bad_request", "request file is invalid or exceeds size limit")
            try:
                response = dict(response)
                response["_transport"] = {"request_id": path.stem,
                                          "request_digest": request_digest(envelope) if isinstance(envelope, dict) else None}
                atomic_json(self.root, self.root / "responses" / path.name, response)
                # Do not duplicate bearer credentials in the processed archive.
                archived = dict(envelope) if isinstance(envelope, dict) else {"request_id": path.stem}
                archived.pop("auth_token", None)
                atomic_json(self.root, self.root / "processed" / path.name, archived)
                safe_path(self.root, path).unlink(missing_ok=True)
            except (OSError, ValueError, RecursionError):
                # A restart retries safely through the core's request-id journal.
                self.file_errors += 1

    def _file_loop(self):
        while not self.stop_event.is_set():
            try:
                self.process_files_once()
            except (OSError, ValueError):
                self.file_errors += 1
            self.stop_event.wait(self.poll_seconds)

    def start(self, host="127.0.0.1", port=9041):
        if host != "127.0.0.1":
            raise ValueError("only host 127.0.0.1 is supported")
        if self.httpd is not None:
            raise RuntimeError("service is already started")
        self.httpd = ThreadingHTTPServer((host, port), make_handler(self))
        self.httpd.daemon_threads = True
        self.http_thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.file_thread = threading.Thread(target=self._file_loop, daemon=True)
        self.http_thread.start()
        self.file_thread.start()
        return self.httpd.server_address[1]

    def close(self):
        self.stop_event.set()
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
        for thread in (self.http_thread, self.file_thread):
            if thread is not None:
                thread.join(timeout=5)
        self.lock.close()


def make_handler(service):
    class Handler(BaseHTTPRequestHandler):
        server_version = "DualAgentBridge/1"

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

        def log_message(self, format, *args):
            # Requests contain prompts and capabilities; never log bodies/headers.
            return

        def send_json(self, status, value):
            data = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

        def local_request(self):
            port = self.server.server_address[1]
            allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
            if port == 80:
                allowed.update({"127.0.0.1", "localhost"})
            hosts = self.headers.get_all("Host", [])
            if self.client_address[0] != "127.0.0.1" or len(hosts) != 1 or hosts[0].lower() not in allowed:
                self.send_json(403, error("forbidden_host", "loopback Host is required"))
                return False
            origins = self.headers.get_all("Origin", [])
            if len(origins) > 1 or (origins and origins[0].lower() != "http://" + hosts[0].lower()):
                self.send_json(403, error("forbidden_origin", "cross-origin requests are rejected"))
                return False
            return True

        def do_GET(self):
            if not self.local_request():
                return
            if self.path.startswith('/observe'):
                try:
                    parsed = urlsplit(self.path)
                    kind = {'/observe': 'summary', '/observe/tasks': 'tasks', '/observe/task': 'task'}.get(parsed.path)
                    if kind is None or len(self.path) > 4096:
                        raise ValueError()
                    fields = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=10)
                    if any(len(v) != 1 for v in fields.values()):
                        raise ValueError()
                    args = {k: v[0] for k, v in fields.items()}
                    actor = args.pop('actor', 'codex')
                    if 'limit' in args:
                        args['limit'] = int(args['limit'])
                    credentials = self.headers.get_all('Authorization', [])
                    token = credentials[0][7:] if len(credentials) == 1 and credentials[0].startswith('Bearer ') else None
                    response = service.observe(actor, token, kind, args)
                    code = response.get('error', {}).get('code')
                    self.send_json(401 if code == 'unauthorized' else 200 if response.get('ok') else 400, response)
                except (ValueError, UnicodeError):
                    self.send_json(400, error('bad_request', 'Invalid observation query'))
                return
            if self.path != "/health":
                self.send_json(404, error("not_found", "unknown endpoint"))
                return
            self.send_json(200, {"ok": True, "protocol": PROTOCOL, "service": "dual-agent-bridge"})

        def do_OPTIONS(self):
            self.send_json(405, error("method_not_allowed", "browser CORS is not enabled"))

        def do_POST(self):
            if not self.local_request():
                return
            if self.path != "/rpc":
                self.send_json(404, error("not_found", "unknown endpoint"))
                return
            lengths = self.headers.get_all("Content-Length", [])
            if self.headers.get("Transfer-Encoding") or len(lengths) != 1 or not re.fullmatch(r"\d+", lengths[0]):
                self.send_json(400, error("bad_request", "one Content-Length and no Transfer-Encoding are required"))
                return
            size = int(lengths[0])
            if size > MAX_BODY_BYTES:
                self.send_json(413, error("body_too_large", "request body exceeds size limit"))
                return
            types = self.headers.get_all("Content-Type", [])
            if len(types) != 1 or types[0].split(";", 1)[0].strip().lower() != "application/json":
                self.send_json(415, error("unsupported_media_type", "Content-Type application/json is required"))
                return
            try:
                data = self.rfile.read(size)
                if len(data) != size:
                    raise ValueError("incomplete request body")
                envelope = json.loads(data.decode("utf-8"))
            except (ValueError, UnicodeError, OSError, RecursionError):
                self.send_json(400, error("bad_request", "request body must be complete UTF-8 JSON"))
                return
            credentials = self.headers.get_all("Authorization", [])
            token = None
            if len(credentials) == 1 and credentials[0].startswith("Bearer "):
                token = credentials[0][7:]
            response = service.dispatch(envelope, token)
            status = 401 if response.get("error", {}).get("code") == "unauthorized" else 200
            self.send_json(status, response)

    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--host", default="127.0.0.1", choices=["127.0.0.1"])
    parser.add_argument("--port", type=int, default=9041)
    parser.add_argument("--poll-seconds", type=float, default=0.3)
    args = parser.parse_args(argv)
    service = None
    try:
        service = BridgeService(args.root, args.poll_seconds)
        port = service.start(args.host, args.port)
        print(json.dumps({"ready": True, "url": f"http://127.0.0.1:{port}", "protocol": PROTOCOL}), flush=True)
        while not service.stop_event.wait(0.5):
            pass
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        print(f"bridge startup/runtime failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if service is not None:
            service.close()


if __name__ == "__main__":
    raise SystemExit(main())
