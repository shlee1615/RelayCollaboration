"""Durable symmetric consumers for dual-agent/v1. No shell/model implementation.

Executor callbacks are trusted code and MUST enforce cancellation and their OS
permissions. Scope validation here is not an operating-system sandbox. A crash
after the durable execution-start marker is never resolved by rerunning a model.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import threading
import time
from typing import Any, Callable
import uuid

MAX_RESULT_BYTES = 96 * 1024
TERMINAL_LOCAL = frozenset({"completed", "rejected", "manual_review"})


class WorkerError(RuntimeError):
    """Messages are fixed diagnostics, never a prompt, credential or subprocess log."""


def _json(value: Any, limit=MAX_RESULT_BYTES) -> str:
    budget = [20000]
    def visit(item, depth=0):
        budget[0] -= 1
        if depth > 24 or budget[0] < 0:
            raise WorkerError("JSON nesting limit exceeded")
        if item is None or type(item) in (str, bool, int):
            if type(item) is int and item.bit_length() > 64:
                raise WorkerError("JSON integers must fit 64 bits")
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                visit(child, depth + 1)
            return
        if type(item) is dict and all(type(k) is str for k in item):
            for child in item.values():
                visit(child, depth + 1)
            return
        raise WorkerError("Only finite JSON values are accepted")
    visit(value)
    data = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(data.encode("utf-8")) > limit:
        raise WorkerError("JSON size limit exceeded")
    return data


def _uuid(value):
    try:
        return type(value) is str and str(uuid.UUID(value)) == value
    except ValueError:
        return False


def checked_path(value, *, must_exist=False) -> Path:
    """Check every ancestor, including Windows reparse attributes on Python 3.10."""
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or "\x00" in str(path):
        raise WorkerError("An absolute normalized path is required")
    if os.name == "nt" and str(path).startswith("\\\\"):
        raise WorkerError("Worker state must use a local filesystem")
    if any(any(character in part for character in "*?[]") for part in path.parts):
        raise WorkerError("Wildcard paths are not accepted")
    cursor = Path(path.anchor)
    for part in path.parts[1:]:
        if os.name == "nt" and (":" in part or part.endswith((".", " "))):
            raise WorkerError("Ambiguous Windows path is not accepted")
        cursor /= part
        try:
            info = os.lstat(cursor)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise WorkerError("Symlinks and reparse points are not accepted")
    if must_exist and not path.exists():
        raise WorkerError("Required path does not exist")
    return path.resolve(strict=must_exist)


def _within(root: Path, path: Path) -> bool:
    try:
        return os.path.commonpath([os.path.normcase(str(root)), os.path.normcase(str(path))]) == os.path.normcase(str(root))
    except ValueError:
        return False


def _child(root: Path, relative: str) -> Path:
    candidate = checked_path(root / relative)
    if not _within(root, candidate):
        raise WorkerError("Path escapes its configured root")
    return candidate


def _atomic_json(root: Path, relative: str, value: dict):
    target = _child(root, relative)
    temporary = _child(root, relative + "." + uuid.uuid4().hex + ".tmp")
    data = (_json(value) + "\n").encode("utf-8")
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        checked_path(target)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            checked_path(temporary).unlink()


class WorkerLock:
    """One active consumer per actor and broker, independent of worker state root."""
    def __init__(self, broker_root: Path, actor: str):
        directory = _child(broker_root, "runtime")
        directory.mkdir(exist_ok=True)
        self.path = _child(directory, f"worker-{actor}.lock")
        descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        self.stream = os.fdopen(descriptor, "r+b")
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            self.stream.close()
            raise WorkerError("Worker lock must be a regular file")
        if os.fstat(descriptor).st_size == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise WorkerError("Another worker owns this actor and broker") from None

    def close(self):
        if self.stream.closed:
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


@dataclass(frozen=True)
class WorkerSettings:
    actor: str
    project: str
    broker_root: Path | str
    state_root: Path | str
    project_root: Path | str
    allowed_paths: tuple[str, ...] = ()
    allowed_modes: tuple[str, ...] = ("read_only", "proposal")
    claim_ttl_seconds: int = 900
    task_timeout_seconds: float = 600
    safety_margin_seconds: float = 30
    transport: str = "http"
    url: str = "http://127.0.0.1:9041"
    poll_seconds: float = 2
    request_timeout_seconds: float = 10
    shutdown_grace_seconds: float = 5
    auth_retry_seconds: float = 30
    bridge_client_sha256: str | None = None

    def __post_init__(self):
        if self.actor not in ("codex", "claude") or not self.project or len(self.project) > 160:
            raise WorkerError("Invalid fixed actor or project")
        if not set(self.allowed_modes).issubset({"read_only", "proposal"}):
            raise WorkerError("Implementation is disabled in worker v1")
        if type(self.claim_ttl_seconds) is not int or not 30 <= self.claim_ttl_seconds <= 900:
            raise WorkerError("Claim TTL must be an integer between 30 and 900 seconds")
        numbers = (self.task_timeout_seconds, self.safety_margin_seconds, self.poll_seconds,
                   self.request_timeout_seconds, self.shutdown_grace_seconds, self.auth_retry_seconds)
        if not all(type(value) in (int, float) and math.isfinite(value) and value > 0 for value in numbers):
            raise WorkerError("Worker timing values must be positive finite numbers")
        if (self.safety_margin_seconds < 5 or self.task_timeout_seconds + self.safety_margin_seconds > self.claim_ttl_seconds
                or self.request_timeout_seconds > self.safety_margin_seconds / 2
                or self.shutdown_grace_seconds > self.safety_margin_seconds / 2):
            raise WorkerError("Timeouts must fit within claim TTL and its safety margin")
        if self.transport not in ("http", "file"):
            raise WorkerError("Invalid transport")
        for name in ("broker_root", "project_root", "state_root"):
            object.__setattr__(self, name, checked_path(getattr(self, name), must_exist=name != "state_root"))
        scopes = []
        for item in self.allowed_paths:
            candidate = Path(item)
            candidate = checked_path(candidate if candidate.is_absolute() else self.project_root / candidate)
            if not _within(self.project_root, candidate):
                raise WorkerError("Configured path scope escapes the project")
            scopes.append(str(candidate))
        object.__setattr__(self, "allowed_paths", tuple(scopes))


@dataclass(frozen=True)
class ExecutionContext:
    task_id: str
    attempt_id: str
    timeout_seconds: float
    deadline_epoch: float
    stop_event: threading.Event
    allowed_paths: tuple[str, ...]
    project_root: Path
    evidence_dir: Path


def _redact(value, secrets_to_hide=()):
    if isinstance(value, dict):
        return {key: ("[redacted]" if re.search(r"token|password|secret|authorization|api.?key|credential", key, re.I)
                      else _redact(item, secrets_to_hide)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, secrets_to_hide) for item in value]
    if isinstance(value, str):
        for secret in secrets_to_hide:
            if secret:
                value = value.replace(secret, "[redacted]")
        value = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [redacted]", value)
        value = re.sub(r"\bsk-[A-Za-z0-9_-]{10,}\b", "[redacted]", value)
        value = re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[redacted]", value)
    return value


class Worker:
    def __init__(self, settings: WorkerSettings, executor: Callable, preflight: Callable,
                 request_fn: Callable | None = None):
        self.settings, self.executor, self.preflight = settings, executor, preflight
        self.request_fn = request_fn
        self.lock = None
        self.connection = None
        self.active_thread = None
        self.executor_stop = None
        self.server_epoch = None
        self.server_monotonic = None
        self.auth_next_check = 0.0
        self.auth_checked = False
        self.preflight_this_cycle = None
        self.pagination_supported = None
        self.status = {"schema": "collab-worker-status/v1", "actor": settings.actor,
                       "project": settings.project, "pid": os.getpid(), "state": "created",
                       "last_task_id": None, "reason": None, "model_execution_count": 0,
                       "auth_ready": None}

    def _status(self, state, reason=None, task_id=None):
        self.status.update(state=state, reason=reason, updated_at=time.time())
        if task_id is not None:
            self.status["last_task_id"] = task_id
        if self.connection is not None:
            self.status["journal_counts"] = dict(self.connection.execute("SELECT state,COUNT(*) FROM jobs GROUP BY state"))
            _atomic_json(self.settings.state_root, "status.json", self.status)
        return dict(self.status)

    def snapshot(self):
        return dict(self.status)

    def start(self):
        if self.connection is not None:
            return
        settings = self.settings
        self.lock = WorkerLock(settings.broker_root, settings.actor)
        try:
            settings.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            checked_path(settings.state_root)
            _child(settings.state_root, "evidence").mkdir(exist_ok=True, mode=0o700)
            database = _child(settings.state_root, "journal.sqlite")
            for suffix in ("-wal", "-shm", "-journal"):
                checked_path(str(database) + suffix)
            self.connection = sqlite3.connect(database, isolation_level=None)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=FULL")
            self.connection.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS jobs (
                    task_id TEXT PRIMARY KEY, task_json TEXT NOT NULL, task_digest TEXT NOT NULL,
                    state TEXT NOT NULL, reason TEXT, claim_request_id TEXT, claim_token TEXT,
                    claim_expires_at REAL, claim_attempt INTEGER, expected_claim_attempt INTEGER, attempt_id TEXT,
                    execution_started_at REAL, result_json TEXT, completion_status TEXT,
                    complete_request_id TEXT, updated_at REAL NOT NULL
                );
            """)
            if "expected_claim_attempt" not in {row[1] for row in self.connection.execute("PRAGMA table_info(jobs)")}:
                self.connection.execute("ALTER TABLE jobs ADD COLUMN expected_claim_attempt INTEGER")
            identity = _json({"actor": settings.actor, "project": settings.project,
                              "broker_root": os.path.normcase(str(settings.broker_root)),
                              "project_root": os.path.normcase(str(settings.project_root))})
            old = self.connection.execute("SELECT value FROM metadata WHERE key='identity'").fetchone()
            if old and old[0] != identity:
                raise WorkerError("Journal belongs to different worker identity")
            self.connection.execute("INSERT OR IGNORE INTO metadata VALUES ('identity',?)", (identity,))
            if self.request_fn is None:
                from . import bridge_client
                if settings.bridge_client_sha256 and hashlib.sha256(Path(bridge_client.__file__).read_bytes()).hexdigest().lower() != settings.bridge_client_sha256.lower():
                    raise WorkerError("Packaged bridge client does not match optional pin")
                self.request_fn = bridge_client.request
            self._status("started")
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.executor_stop is not None:
            self.executor_stop.set()
        if self.active_thread is not None and self.active_thread.is_alive():
            # Releasing the actor lock while a trusted callback still runs permits
            # overlapping side effects. A supervisor may terminate this process.
            raise WorkerError("Executor still running; actor lock retained")
        if self.connection is not None:
            self._status("stopped")
            self.connection.close()
            self.connection = None
        if self.lock is not None:
            self.lock.close()
            self.lock = None

    def _rpc(self, op, args, request_id=None):
        settings = self.settings
        return self.request_fn(settings.broker_root, settings.actor, op, args,
                               transport=settings.transport, url=settings.url,
                               timeout=settings.request_timeout_seconds, request_id=request_id)

    def _now(self):
        if self.server_epoch is None:
            raise WorkerError("Broker clock has not been synchronized")
        return self.server_epoch + (time.monotonic() - self.server_monotonic)

    def _ready(self):
        try:
            sent = time.monotonic()
            health = self._rpc("health", {})
            if not health.get("ok"):
                self._status("transport_error", "broker_auth_or_health_failed")
                return False
            self.server_epoch = float(health["result"]["server_time"])
            self.server_monotonic = sent  # Conservative: includes roundtrip latency.
            return True
        except Exception:
            self._status("transport_error", "broker_health_unavailable")
            return False

    def _executor_ready(self):
        if self.preflight_this_cycle is not None:
            return self.preflight_this_cycle
        if time.monotonic() < self.auth_next_check:
            self._status("auth_blocked", "executor_preflight_not_ready")
            self.preflight_this_cycle = False
            return False
        try:
            result = self.preflight()
            if not isinstance(result, dict) or result.get("ok") is not True or result.get("auth_ready") is not True:
                self.auth_next_check = time.monotonic() + self.settings.auth_retry_seconds
                self.status["auth_ready"] = False
                self.preflight_this_cycle = False
                self._status("auth_blocked", "executor_preflight_not_ready")
                return False
            self.status["auth_ready"] = True
            self.preflight_this_cycle = True
            return True
        except Exception:
            self.auth_next_check = time.monotonic() + self.settings.auth_retry_seconds
            self.status["auth_ready"] = False
            self.preflight_this_cycle = False
            self._status("auth_blocked", "preflight_unavailable")
            return False

    def _row(self, task_id):
        return self.connection.execute("SELECT * FROM jobs WHERE task_id=?", (task_id,)).fetchone()

    def _update(self, task_id, **fields):
        allowed = {"state", "reason", "claim_request_id", "claim_token", "claim_expires_at", "claim_attempt", "expected_claim_attempt",
                   "attempt_id", "execution_started_at", "result_json", "completion_status", "complete_request_id"}
        if set(fields) - allowed:
            raise WorkerError("Unsupported journal field")
        fields["updated_at"] = time.time()
        self.connection.execute("UPDATE jobs SET " + ",".join(key + "=?" for key in fields) + " WHERE task_id=?",
                                (*fields.values(), task_id))

    def _register(self, task, state, reason=None):
        public = {key: task[key] for key in ("task_id", "project", "sender", "recipient", "title", "prompt", "mode", "allowed_paths")}
        if task.get('images'):
            from .vision import validate_images
            public['images'] = validate_images(task['images'])
        encoded = _json(public, 2 * 1024 * 1024)
        self.connection.execute("INSERT INTO jobs (task_id,task_json,task_digest,state,reason,updated_at) VALUES (?,?,?,?,?,?)",
                                (task["task_id"], encoded, hashlib.sha256(encoded.encode()).hexdigest(), state, reason, time.time()))

    def _task_scope(self, task):
        if not _uuid(task.get("task_id")):
            return "invalid_task_id"
        if task.get("project") != self.settings.project or task.get("recipient") != self.settings.actor:
            return "different_project_or_recipient"
        if task.get("mode") not in self.settings.allowed_modes:
            return "mode_not_enabled"
        paths = task.get("allowed_paths", [])
        if not isinstance(paths, list):
            return "invalid_path_scope"
        try:
            for item in paths:
                candidate = Path(item)
                candidate = checked_path(candidate if candidate.is_absolute() else self.settings.project_root / candidate)
                if not _within(self.settings.project_root, candidate):
                    return "path_outside_project"
                if not any(_within(Path(scope), candidate) for scope in self.settings.allowed_paths):
                    return "path_not_authorized"
        except (WorkerError, TypeError, ValueError, OSError):
            return "invalid_path_scope"
        return None

    def _manual(self, task_id, reason):
        self._update(task_id, state="manual_review", reason=reason, claim_token=None)
        self._status("manual_review", reason, task_id)
        _atomic_json(self.settings.state_root, f"evidence/{task_id}.json",
                     {"schema": "collab-worker-evidence/v1", "task_id": task_id, "actor": self.settings.actor,
                      "state": "manual_review", "reason": reason, "updated_at": time.time()})

    def _claim(self, task_id, *, new=False):
        if not self._executor_ready():
            return False
        row = self._row(task_id)
        if new or not row["claim_request_id"]:
            request_id = str(uuid.uuid4())
            expected_attempt = row["claim_attempt"] + 1 if new else 1
        else:
            request_id = row["claim_request_id"]
            expected_attempt = row["expected_claim_attempt"]
            if expected_attempt is None:
                self._manual(task_id, "legacy_claim_expectation_missing")
                return False
        # Persist expectation together with UUID BEFORE the request. Replaying a
        # lost reclaim response must not bypass the generation check.
        self._update(task_id, state="claiming", claim_request_id=request_id, expected_claim_attempt=expected_attempt,
                     complete_request_id=None if new else row["complete_request_id"])
        response = self._rpc("task.claim", {"task_id": task_id, "ttl_seconds": self.settings.claim_ttl_seconds}, request_id)
        if not response.get("ok"):
            code = response.get("error", {}).get("code", "unknown")
            if code in ("TASK_UNAVAILABLE", "STALE_CLAIM"):
                self._manual(task_id, "claim_owned_elsewhere_or_terminal")
            else:
                self._status("transport_error", "claim_not_confirmed", task_id)
            return False
        claimed = response["result"]
        if claimed.get("claim_attempt") != expected_attempt:
            # Another claimant can execute between a task.get and this claim.
            # An atomic new claim is not proof there was no intervening generation.
            self._manual(task_id, "intervening_claim_generation")
            return False
        self._update(task_id, state="result_ready" if row["result_json"] else "claimed",
                     claim_token=claimed["claim_token"], claim_expires_at=claimed["claim_expires_at"],
                     claim_attempt=claimed["claim_attempt"], complete_request_id=None if new else row["complete_request_id"])
        return True

    def _finish(self, task_id):
        row = self._row(task_id)
        request_id = row["complete_request_id"] or str(uuid.uuid4())
        self._update(task_id, complete_request_id=request_id)
        response = self._rpc("task.complete", {"task_id": task_id, "claim_token": row["claim_token"],
                            "status": row["completion_status"], "result": json.loads(row["result_json"])}, request_id)
        if response.get("ok"):
            self._update(task_id, state="completed", reason=None, claim_token=None)
            self._status("idle", "completion_confirmed", task_id)
            return True
        if response.get("error", {}).get("code") == "STALE_CLAIM":
            current = self._rpc("task.get", {"task_id": task_id})
            if not current.get("ok"):
                return False
            task = current["result"]
            if task["status"] in ("completed", "failed"):
                if _json(task.get("result")) == row["result_json"] and task["status"] == row["completion_status"]:
                    self._update(task_id, state="completed", claim_token=None)
                    return True
                self._manual(task_id, "terminal_result_differs")
            elif task.get("claim_attempt") != row["claim_attempt"]:
                self._manual(task_id, "claim_generation_changed")
            elif task.get("claim_expires_at", 0) <= self._now():
                if self._claim(task_id, new=True):
                    return self._finish(task_id)
        elif response.get("error", {}).get("code") in {"STORAGE_ERROR", "INTERNAL_ERROR", "internal_error", "unauthorized"}:
            self._status("transport_error", "completion_not_confirmed", task_id)
        else:
            self._manual(task_id, "completion_permanently_rejected")
        return False

    def _execute(self, task_id, stop_event):
        if not self._executor_ready():
            return False
        row = self._row(task_id)
        task = json.loads(row["task_json"])
        reason = self._task_scope(task)
        if reason:
            self._manual(task_id, reason)
            return False
        remaining = row["claim_expires_at"] - self._now() - self.settings.safety_margin_seconds
        if remaining <= 0:
            if row["claim_expires_at"] <= self._now() and self._claim(task_id, new=True):
                return self._execute(task_id, stop_event)
            self._status("idle", "waiting_for_safe_reclaim", task_id)
            return False
        if stop_event.is_set():
            return False
        timeout = min(self.settings.task_timeout_seconds, remaining)
        attempt_id = str(uuid.uuid4())
        self.executor_stop = threading.Event()
        context = ExecutionContext(task_id, attempt_id, timeout, time.time() + timeout, self.executor_stop,
                                   tuple(task["allowed_paths"]), self.settings.project_root,
                                   _child(self.settings.state_root, "evidence"))
        # This FULL-synchronous commit MUST precede every executor invocation.
        self._update(task_id, state="executing", attempt_id=attempt_id, execution_started_at=time.time())
        self.status["model_execution_count"] += 1
        self._status("executing", None, task_id)
        holder = {}

        def invoke():
            try:
                holder["result"] = self.executor(dict(task), context)
            except BaseException:
                holder["exception"] = True

        self.active_thread = threading.Thread(target=invoke, daemon=True, name=f"worker-executor-{self.settings.actor}")
        self.active_thread.start()
        deadline = time.monotonic() + timeout
        cancelled = False
        while self.active_thread.is_alive():
            if stop_event.is_set() or time.monotonic() >= deadline:
                cancelled = True
                self.executor_stop.set()
                self.active_thread.join(self.settings.shutdown_grace_seconds)
                break
            self.active_thread.join(min(0.1, max(0.001, deadline - time.monotonic())))
        if self.active_thread.is_alive():
            self._manual(task_id, "executor_did_not_stop")
            raise WorkerError("Executor did not stop; supervisor must terminate worker")
        if cancelled or holder.get("exception"):
            # An interruption may follow an external side effect. Never retry it.
            self._manual(task_id, "execution_interrupted_or_exception")
            return False
        try:
            raw = holder["result"]
            if not isinstance(raw, dict) or type(raw.get("ok")) is not bool:
                raise WorkerError("Executor must return a structured result with boolean ok")
            sanitized = _redact(raw, (row["claim_token"],))
            encoded = _json(sanitized)
        except (WorkerError, ValueError, TypeError, RecursionError):
            self._manual(task_id, "invalid_executor_result")
            return False
        completion_status = "completed" if sanitized["ok"] and sanitized.get("status") not in ("failed", "timeout", "cancelled") else "failed"
        self._update(task_id, state="result_ready", result_json=encoded, completion_status=completion_status)
        _atomic_json(self.settings.state_root, f"evidence/{task_id}.json",
                     {"schema": "collab-worker-evidence/v1", "task_id": task_id, "actor": self.settings.actor,
                      "attempt_id": attempt_id, "state": "result_ready", "completion_status": completion_status,
                      "result_sha256": hashlib.sha256(encoded.encode()).hexdigest(),
                      "execution_kind": sanitized.get("execution_kind", "unspecified"), "updated_at": time.time()})
        return self._finish(task_id)

    def _list_page(self, status, cursor=None):
        args = {"to": self.settings.actor, "status": status, "limit": 100}
        if self.pagination_supported is not False:
            args["project"] = self.settings.project
            if cursor is not None:
                args["cursor"] = cursor
        response = self._rpc("task.list", args)
        if (self.pagination_supported is None and not response.get("ok")
                and response.get("error", {}).get("code") == "INVALID_ARGS"):
            self.pagination_supported = False
            return self._rpc("task.list", {"to": self.settings.actor, "status": status, "limit": 100})
        if response.get("ok") and "next_cursor" in response["result"]:
            self.pagination_supported = True
        return response

    def run_once(self, stop_event=None):
        stop_event = stop_event or threading.Event()
        self.start()
        if stop_event.is_set():
            return self._status("stopping")
        if self.active_thread is not None and self.active_thread.is_alive():
            return self._status("manual_review", "executor_still_running")
        self.preflight_this_cycle = None
        if not self._ready():
            return self.snapshot()
        if not self.auth_checked:
            self.auth_checked = True
            self._executor_ready()
        try:
            # Recover known work before accepting new work.
            for row in self.connection.execute("SELECT * FROM jobs WHERE state NOT IN ('completed','rejected','manual_review') ORDER BY updated_at").fetchall():
                task_id, state = row["task_id"], row["state"]
                if state == "executing":
                    self._manual(task_id, "crash_after_execution_started")
                elif state == "result_ready":
                    self._finish(task_id)
                elif state == "claiming":
                    if self._claim(task_id):
                        recovered = self._row(task_id)
                        self._finish(task_id) if recovered["result_json"] else self._execute(task_id, stop_event)
                elif state == "claimed":
                    self._execute(task_id, stop_event)
                return self.snapshot()
            saturated = False
            for status in ("running", "pending"):
                cursor, seen_cursors = None, set()
                for _ in range(200):
                    if stop_event.is_set():
                        return self._status("stopping")
                    listing = self._list_page(status, cursor)
                    if not listing.get("ok"):
                        return self._status("transport_error", "task_listing_failed")
                    tasks = listing["result"]["tasks"]
                    saturated = saturated or (self.pagination_supported is not True and len(tasks) >= 100)
                    for task in reversed(tasks):
                        if task.get("project") != self.settings.project or self._row(task["task_id"]):
                            continue
                        reason = self._task_scope(task)
                        if reason:
                            self._register(task, "rejected", reason)
                            self._status("idle", reason, task["task_id"])
                            continue
                        if status == "running":
                            if task.get("claim_expires_at", 0) <= self._now():
                                self._register(task, "manual_review", "untracked_previous_claim")
                                self._status("manual_review", "untracked_previous_claim", task["task_id"])
                            continue
                        if task.get('image_count'):
                            full = self._rpc('task.get', {'task_id':task['task_id']})
                            if not full.get('ok') or full['result'].get('task_id') != task['task_id']:
                                return self._status('transport_error', 'image_task_fetch_failed')
                            task = full['result']
                            from .vision import validate_images
                            if len(validate_images(task.get('images', []))) != task.get('image_count'):
                                return self._status('transport_error', 'image_task_incomplete')
                        self._register(task, "claiming")
                        if stop_event.is_set():
                            return self._status("stopping")
                        if self._claim(task["task_id"]):
                            self._execute(task["task_id"], stop_event)
                        return self.snapshot()
                    cursor = listing["result"].get("next_cursor")
                    if self.pagination_supported is not True or cursor is None:
                        break
                    encoded_cursor = _json(cursor)
                    if encoded_cursor in seen_cursors:
                        return self._status("attention_required", "pagination_cursor_repeated")
                    seen_cursors.add(encoded_cursor)
                else:
                    return self._status("attention_required", "pagination_scan_limit_reached")
            if saturated:
                # A pre-patch v1 broker may hide older valid tasks.
                return self._status("attention_required", "queue_window_full_no_cursor")
            if self.status["auth_ready"] is False:
                return self._status("auth_blocked", "executor_preflight_not_ready")
            return self._status("idle")
        except WorkerError:
            raise
        except Exception:
            return self._status("transport_error", "operation_interrupted_retry_preserved")

    def run(self, stop_event=None, max_tasks=None):
        stop_event = stop_event or threading.Event()
        if max_tasks is not None and (type(max_tasks) is not int or max_tasks <= 0):
            raise WorkerError("max_tasks must be a positive integer")
        try:
            self.start()
            while not stop_event.is_set():
                self.run_once(stop_event)
                if max_tasks is not None and self.status["model_execution_count"] >= max_tasks:
                    break
                stop_event.wait(self.settings.poll_seconds)
            return self.snapshot()
        finally:
            self.close()
