"""Small, persistent coordination broker; no agent or shell execution.

Transport code must authenticate ``actor`` before calling ``handle``. Capabilities
returned in receipts are secrets: do not log requests, receipts, or the database.
Modes and allowed_paths describe a task; workers must enforce them independently.
All timestamps are UTC Unix seconds. A request UUID identifies one immutable
attempt; retrying it returns its original receipt even after a lease has expired.
Use a new UUID for a new acquire/claim attempt or a fresh read.
"""

from __future__ import annotations

import hashlib
import base64
import json
import math
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Any
import uuid


PROTOCOL = "dual-agent/v1"
ACTORS = frozenset({"codex", "claude"})
MODES = frozenset({"read_only", "proposal", "implementation"})
STATUSES = frozenset({"pending", "running", "completed", "failed"})
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESULT_BYTES = 128 * 1024
MAX_PROMPT_BYTES = 64 * 1024
MAX_TITLE_BYTES = 512
MAX_PATHS = 128


class _Fault(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _error(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


def _json(value: Any, limit: int = MAX_REQUEST_BYTES) -> str:
    """Reject non-JSON Python values, excessive nesting, NaN, and huge inputs."""
    budget = [20000]

    def visit(item: Any, depth: int) -> None:
        budget[0] -= 1
        if depth > 32 or budget[0] < 0:
            raise _Fault("INVALID_REQUEST", "JSON structure is too complex.")
        kind = type(item)
        if item is None or kind in (str, bool):
            return
        if kind is int:
            if item.bit_length() > 64:
                raise _Fault("INVALID_REQUEST", "JSON integers must fit 64 bits.")
            return
        if kind is float and math.isfinite(item):
            return
        if kind is list:
            for child in item:
                visit(child, depth + 1)
            return
        if kind is dict and all(type(key) is str for key in item):
            for child in item.values():
                visit(child, depth + 1)
            return
        raise _Fault("INVALID_REQUEST", "Request must contain only JSON values.")

    visit(value, 0)
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False)
        size = len(encoded.encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise _Fault("INVALID_REQUEST", "Request contains invalid JSON text.") from None
    if size > limit:
        raise _Fault("TOO_LARGE", "JSON payload exceeds its size limit.")
    return encoded


def _fields(args: dict, required: set[str], optional: set[str] | None = None) -> None:
    if not required.issubset(args) or set(args) - required - (optional or set()):
        raise _Fault("INVALID_ARGS", "Missing required or unsupported argument fields.")


def _string(value: Any, name: str, maximum: int, *, empty: bool = False) -> str:
    if type(value) is not str or (not empty and not value.strip()) or "\x00" in value:
        raise _Fault("INVALID_ARGS", f"{name} must be a valid string.")
    if len(value.encode("utf-8")) > maximum:
        raise _Fault("TOO_LARGE", f"{name} exceeds its size limit.")
    return value


def _choice(value: Any, choices: frozenset[str], name: str) -> str:
    if type(value) is not str or value not in choices:
        raise _Fault("INVALID_ARGS", f"{name} has an unsupported value.")
    return value


def _uuid(value: Any, name: str) -> str:
    if type(value) is not str:
        raise _Fault("INVALID_ARGS", f"{name} must be a canonical UUID string.")
    try:
        if str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise _Fault("INVALID_ARGS", f"{name} must be a canonical UUID string.") from None
    return value


def _ttl(args: dict) -> int:
    ttl = args.get("ttl_seconds", 300)
    if type(ttl) is not int or not 30 <= ttl <= 900:
        raise _Fault("INVALID_ARGS", "ttl_seconds must be an integer from 30 to 900.")
    return ttl


def _project(args: dict) -> str:
    return _string(args.get("project", "relay"), "project", 160)


class Broker:
    """Thread-safe across instances sharing one local SQLite database file.

    ``db_path`` must refer to a file on a local filesystem, not a network share or
    ``:memory:``. The broker creates no directories and accesses no project files.
    A separate connection and write transaction serialize each request together
    with its idempotency receipt. Callers need not hold an in-process mutex.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        if self.db_path in ("", ":memory:"):
            raise ValueError("Broker requires a persistent SQLite database path.")
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS leaders (
                    project TEXT PRIMARY KEY,
                    actor TEXT,
                    token TEXT,
                    generation INTEGER NOT NULL,
                    expires_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    project TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    recipient TEXT NOT NULL,
                    title TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    allowed_paths_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    leader_generation INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    claimed_at REAL,
                    claim_expires_at REAL,
                    claim_token TEXT,
                    claim_attempt INTEGER NOT NULL DEFAULT 0,
                    completed_at REAL,
                    result_json TEXT
                );
                CREATE INDEX IF NOT EXISTS tasks_recipient_status
                    ON tasks(recipient, status, created_at);
                CREATE TABLE IF NOT EXISTS receipts (
                    request_id TEXT PRIMARY KEY,
                    request_hash TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
            """)
            # Additive migration; old tasks and receipts retain their identity.
            columns = {row[1] for row in connection.execute('PRAGMA table_info(tasks)')}
            if 'images_json' not in columns:
                connection.execute("ALTER TABLE tasks ADD COLUMN images_json TEXT NOT NULL DEFAULT '[]'")
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=15000")
        return connection

    def observe(self, actor: str, kind: str, args: dict) -> dict:
        """Authenticated by transport. SELECT-only snapshots, never receipts.

        This interface intentionally has no request UUID or mutation dispatch.
        Repeated observations are fresh, with one SQLite read transaction each.
        """
        connection = None
        try:
            _choice(actor, ACTORS, 'actor')
            if type(args) is not dict:
                raise _Fault('INVALID_ARGS', 'Observation args must be an object.')
            if kind not in {'summary', 'tasks', 'task'}:
                raise _Fault('INVALID_ARGS', 'Unknown observation kind.')
            allowed = {'project'} | ({'status', 'direction', 'q', 'cursor', 'limit'} if kind == 'tasks' else {'task_id'} if kind == 'task' else set())
            _fields(args, {'task_id'} if kind == 'task' else set(), allowed)
            project = _project(args)
            now = time.time()
            connection = self._connect()
            connection.execute('PRAGMA query_only=ON')
            connection.execute('BEGIN')
            if kind == 'summary':
                counts = dict.fromkeys(('pending', 'running', 'completed', 'failed'), 0)
                for row in connection.execute('SELECT status,COUNT(*) AS n FROM tasks WHERE project=? GROUP BY status', (project,)):
                    counts[row['status']] = row['n']
                expired = connection.execute("SELECT COUNT(*) FROM tasks WHERE project=? AND status='running' AND claim_expires_at<=?", (project, now)).fetchone()[0]
                counts['total'] = sum(counts.values())
                counts['attention'] = counts['failed'] + expired
                counts['running'] -= expired
                row = connection.execute('SELECT actor,generation,expires_at FROM leaders WHERE project=?', (project,)).fetchone()
                leader = dict(row) if row else {'actor': None, 'generation': 0, 'expires_at': 0}
                leader['active'] = bool(leader['actor'] and leader['expires_at'] > now)
                if not leader['active']:
                    leader['actor'] = None
                value = {'server_time': now, 'project': project, 'counts': counts, 'leader': leader}
            elif kind == 'task':
                task_id = _uuid(args['task_id'], 'task_id')
                row = connection.execute('SELECT * FROM tasks WHERE project=? AND task_id=?', (project, task_id)).fetchone()
                if row is None:
                    raise _Fault('TASK_NOT_FOUND', 'Task does not exist in this project.')
                task = self._public_task(row)
                task.update(self._observation_item(row, now))
                value = {'server_time': now, 'task': task}
            else:
                status = args.get('status', 'all')
                direction = args.get('direction', 'all')
                if status not in STATUSES | {'all', 'attention'} or direction not in {'all', 'codex-claude', 'claude-codex'}:
                    raise _Fault('INVALID_ARGS', 'Invalid observation filter.')
                query = _string(args.get('q', ''), 'q', 512, empty=True)
                limit = args.get('limit', 40)
                if type(limit) is not int or not 1 <= limit <= 100:
                    raise _Fault('INVALID_ARGS', 'limit must be 1 to 100.')
                clauses, values = ['project=?'], [project]
                if status == 'attention':
                    clauses.append("(status='failed' OR (status='running' AND claim_expires_at<=?))")
                    values.append(now)
                elif status == 'running':
                    clauses.append("status='running' AND claim_expires_at>?")
                    values.append(now)
                elif status != 'all':
                    clauses.append('status=?'); values.append(status)
                if direction != 'all':
                    sender, recipient = direction.split('-')
                    clauses.extend(('sender=?', 'recipient=?')); values.extend((sender, recipient))
                if query:
                    clauses.append("(title LIKE ? ESCAPE '\\' OR task_id LIKE ? ESCAPE '\\')")
                    pattern = '%' + query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
                    values.extend((pattern, pattern))
                total = connection.execute('SELECT COUNT(*) FROM tasks WHERE ' + ' AND '.join(clauses), values).fetchone()[0]
                cursor = args.get('cursor')
                if cursor:
                    try:
                        if not isinstance(cursor, str) or len(cursor) > 512:
                            raise ValueError()
                        decoded = json.loads(base64.b64decode(cursor.encode('ascii'), altchars=b'-_', validate=True))
                        if type(decoded) is not list or len(decoded) != 2 or type(decoded[0]) not in (float, int) or not math.isfinite(decoded[0]):
                            raise ValueError()
                        stamp, task_id = float(decoded[0]), _uuid(decoded[1], 'cursor')
                    except (ValueError, UnicodeError, TypeError, OverflowError):
                        raise _Fault('INVALID_ARGS', 'Invalid observation cursor.') from None
                    clauses.append('(created_at < ? OR (created_at = ? AND task_id > ?))')
                    values.extend((stamp, stamp, task_id))
                rows = connection.execute('SELECT * FROM tasks WHERE ' + ' AND '.join(clauses) + ' ORDER BY created_at DESC,task_id ASC LIMIT ?', (*values, limit + 1)).fetchall()
                page = rows[:limit]
                next_cursor = base64.urlsafe_b64encode(json.dumps([page[-1]['created_at'], page[-1]['task_id']]).encode()).decode() if len(rows) > limit else None
                value = {'server_time': now, 'items': [self._observation_item(row, now) for row in page], 'next_cursor': next_cursor, 'total': total}
            return {'ok': True, 'result': value}
        except _Fault as exc:
            return _error(exc.code, exc.message)
        except (sqlite3.Error, ValueError, TypeError, OverflowError):
            return _error('OBSERVATION_UNAVAILABLE', 'Observation is unavailable.')
        finally:
            if connection is not None:
                if connection.in_transaction:
                    connection.rollback()
                connection.close()

    @staticmethod
    def _observation_item(row, now):
        names = ('task_id', 'project', 'sender', 'recipient', 'title', 'mode', 'status',
                 'created_at', 'claimed_at', 'completed_at', 'updated_at', 'claim_expires_at', 'claim_attempt')
        item = {name: row[name] for name in names}
        encoded = row['result_json']
        result = json.loads(encoded) if encoded else {}
        item.update(claim_expired=bool(row['status'] == 'running' and (row['claim_expires_at'] or 0) <= now),
                    prompt_bytes=len(row['prompt'].encode('utf-8')), result_bytes=len(encoded.encode('utf-8')) if encoded else 0,
                    result_ok=result.get('ok'), reported_model=result.get('reported_model'))
        return item

    def handle(self, request: Any) -> dict:
        """Return a JSON-safe receipt for one authenticated actor's request."""
        try:
            canonical = _json(request)
            if type(request) is not dict or set(request) != {
                "protocol", "request_id", "actor", "op", "args"
            }:
                raise _Fault("INVALID_REQUEST", "A complete protocol envelope is required.")
            if request["protocol"] != PROTOCOL:
                raise _Fault("INVALID_REQUEST", "Unsupported protocol version.")
            request_id = _uuid(request["request_id"], "request_id")
            actor = _choice(request["actor"], ACTORS, "actor")
            op = _string(request["op"], "op", 64)
            if type(request["args"]) is not dict:
                raise _Fault("INVALID_ARGS", "args must be an object.")
            request_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
            return self._transaction(request_id, request_hash, actor, op, request["args"])
        except _Fault as exc:
            return _error(exc.code, exc.message)
        except sqlite3.Error:
            return _error("STORAGE_ERROR", "Coordination storage is unavailable; retry the same request.")
        except Exception:
            # Never include input, local paths, SQL values or capabilities in errors.
            return _error("INTERNAL_ERROR", "The broker could not process this request.")

    def _transaction(self, request_id: str, request_hash: str,
                     actor: str, op: str, args: dict) -> dict:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute(
                "SELECT request_hash, response_json FROM receipts WHERE request_id = ?",
                (request_id,),
            ).fetchone()
            if previous:
                if previous["request_hash"] != request_hash:
                    return _error("REQUEST_ID_CONFLICT", "request_id already identifies a different request.")
                return json.loads(previous["response_json"])
            now = time.time()
            try:
                result = self._dispatch(connection, actor, op, args, now)
                response = {"ok": True, "result": result}
            except _Fault as exc:
                response = _error(exc.code, exc.message)
            response_json = json.dumps(response, separators=(",", ":"),
                                       ensure_ascii=False, allow_nan=False)
            connection.execute(
                "INSERT INTO receipts VALUES (?, ?, ?, ?)",
                (request_id, request_hash, response_json, now),
            )
            connection.commit()
            return response
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    def _dispatch(self, connection: sqlite3.Connection, actor: str,
                  op: str, args: dict, now: float) -> dict:
        handlers = {
            "health": self._health,
            "lead.acquire": self._lead_acquire,
            "lead.release": self._lead_release,
            "task.submit": self._task_submit,
            "task.claim": self._task_claim,
            "task.complete": self._task_complete,
            "task.get": self._task_get,
            "task.list": self._task_list,
        }
        if op not in handlers:
            raise _Fault("UNKNOWN_OP", "Unsupported broker operation.")
        return handlers[op](connection, actor, args, now)

    @staticmethod
    def _health(connection: sqlite3.Connection, actor: str, args: dict, now: float) -> dict:
        _fields(args, set())
        return {"protocol": PROTOCOL, "storage": "sqlite", "server_time": now}

    @staticmethod
    def _require_leader(connection: sqlite3.Connection, actor: str,
                        project: str, token: str, now: float) -> sqlite3.Row:
        current = connection.execute(
            "SELECT * FROM leaders WHERE project = ?", (project,),
        ).fetchone()
        if (not current or current["actor"] != actor or not current["token"]
                or current["expires_at"] <= now
                or not secrets.compare_digest(current["token"].encode("utf-8"), token.encode("utf-8"))):
            raise _Fault("STALE_LEADER", "A current leadership capability for this actor and project is required.")
        return current

    def _lead_acquire(self, connection: sqlite3.Connection, actor: str,
                      args: dict, now: float) -> dict:
        _fields(args, set(), {"project", "ttl_seconds"})
        project, ttl = _project(args), _ttl(args)
        current = connection.execute(
            "SELECT * FROM leaders WHERE project = ?", (project,),
        ).fetchone()
        if current and current["token"] and current["expires_at"] > now:
            raise _Fault("LEASE_HELD", "Leadership is already leased; release it or wait for expiry.")
        generation = current["generation"] + 1 if current else 1
        token, expiry = secrets.token_urlsafe(32), now + ttl
        connection.execute("""
            INSERT INTO leaders (project, actor, token, generation, expires_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(project) DO UPDATE SET actor=excluded.actor, token=excluded.token,
                generation=excluded.generation, expires_at=excluded.expires_at,
                updated_at=excluded.updated_at
        """, (project, actor, token, generation, expiry, now))
        return {"project": project, "leader": actor, "leader_token": token,
                "generation": generation, "expires_at": expiry}

    def _lead_release(self, connection: sqlite3.Connection, actor: str,
                      args: dict, now: float) -> dict:
        _fields(args, {"leader_token"}, {"project"})
        project = _project(args)
        token = _string(args["leader_token"], "leader_token", 128)
        current = self._require_leader(connection, actor, project, token, now)
        connection.execute(
            "UPDATE leaders SET actor=NULL, token=NULL, expires_at=0, updated_at=? WHERE project=?",
            (now, project),
        )
        return {"project": project, "released": True, "generation": current["generation"]}

    @staticmethod
    def _task(connection: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            raise _Fault("TASK_NOT_FOUND", "Task does not exist.")
        return row

    @staticmethod
    def _public_task(row: sqlite3.Row, include_images=True) -> dict:
        task = dict(row)
        task.pop("claim_token")
        images = json.loads(task.pop('images_json'))
        if images:
            task['image_count'] = len(images)
            if include_images:
                task['images'] = images
        task["allowed_paths"] = json.loads(task.pop("allowed_paths_json"))
        encoded_result = task.pop("result_json")
        task["result"] = json.loads(encoded_result) if encoded_result is not None else None
        return task

    def _task_submit(self, connection: sqlite3.Connection, actor: str,
                     args: dict, now: float) -> dict:
        _fields(args, {"leader_token", "to", "title", "prompt"},
                {"project", "mode", "allowed_paths", "images"})
        project = _project(args)
        token = _string(args["leader_token"], "leader_token", 128)
        recipient = _choice(args["to"], ACTORS, "to")
        if recipient == actor:
            raise _Fault("INVALID_ARGS", "Tasks must be addressed to the other peer.")
        title = _string(args["title"], "title", MAX_TITLE_BYTES)
        prompt = _string(args["prompt"], "prompt", MAX_PROMPT_BYTES)
        from .vision import validate_images
        try:
            images = validate_images(args.get('images', []))
        except ValueError as exc:
            raise _Fault('INVALID_ARGS', str(exc)) from None
        mode = _choice(args.get("mode", "read_only"), MODES, "mode")
        paths = args.get("allowed_paths", [])
        if type(paths) is not list or len(paths) > MAX_PATHS:
            raise _Fault("INVALID_ARGS", "allowed_paths must be a list with at most 128 entries.")
        for path in paths:
            _string(path, "allowed_paths entry", 2048)
        if mode == "implementation" and not paths:
            raise _Fault("INVALID_ARGS", "Implementation tasks require explicit nonempty allowed_paths.")
        current = self._require_leader(connection, actor, project, token, now)
        task_id = str(uuid.uuid4())
        connection.execute("""
            INSERT INTO tasks (task_id, project, sender, recipient, title, prompt, mode,
                allowed_paths_json, images_json, status, leader_generation, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)
        """, (task_id, project, actor, recipient, title, prompt, mode,
              json.dumps(paths, ensure_ascii=False), json.dumps(images), current["generation"], now, now))
        return self._public_task(self._task(connection, task_id))

    def _task_claim(self, connection: sqlite3.Connection, actor: str,
                    args: dict, now: float) -> dict:
        _fields(args, {"task_id"}, {"ttl_seconds"})
        task_id, ttl = _uuid(args["task_id"], "task_id"), _ttl(args)
        row = self._task(connection, task_id)
        if row["recipient"] != actor:
            raise _Fault("NOT_RECIPIENT", "Only the task recipient may claim this task.")
        if (row["status"] not in {"pending", "running"}
                or (row["status"] == "running" and row["claim_expires_at"] > now)):
            raise _Fault("TASK_UNAVAILABLE", "Task is completed or already has an active claim.")
        token, expiry = secrets.token_urlsafe(32), now + ttl
        connection.execute("""
            UPDATE tasks SET status='running', claimed_at=?, claim_expires_at=?,
                claim_token=?, claim_attempt=claim_attempt+1, updated_at=? WHERE task_id=?
        """, (now, expiry, token, now, task_id))
        result = self._public_task(self._task(connection, task_id))
        result["claim_token"] = token
        return result

    def _task_complete(self, connection: sqlite3.Connection, actor: str,
                       args: dict, now: float) -> dict:
        _fields(args, {"task_id", "claim_token", "status", "result"})
        task_id = _uuid(args["task_id"], "task_id")
        token = _string(args["claim_token"], "claim_token", 128)
        status = _choice(args["status"], frozenset({"completed", "failed"}), "status")
        if type(args["result"]) is not dict:
            raise _Fault("INVALID_ARGS", "result must be an object.")
        encoded_result = _json(args["result"], MAX_RESULT_BYTES)
        row = self._task(connection, task_id)
        if row["recipient"] != actor:
            raise _Fault("NOT_RECIPIENT", "Only the task recipient may complete this task.")
        if (row["status"] != "running" or row["claim_expires_at"] <= now
                or not row["claim_token"]
                or not secrets.compare_digest(row["claim_token"].encode("utf-8"), token.encode("utf-8"))):
            raise _Fault("STALE_CLAIM", "A current claim capability is required to complete the task.")
        connection.execute("""
            UPDATE tasks SET status=?, result_json=?, completed_at=?, updated_at=?,
                claim_token=NULL WHERE task_id=?
        """, (status, encoded_result, now, now, task_id))
        return self._public_task(self._task(connection, task_id))

    def _task_get(self, connection: sqlite3.Connection, actor: str,
                  args: dict, now: float) -> dict:
        _fields(args, {"task_id"})
        return self._public_task(self._task(connection, _uuid(args["task_id"], "task_id")))

    def _task_list(self, connection: sqlite3.Connection, actor: str,
                   args: dict, now: float) -> dict:
        """Page immutable creation order; keep filters unchanged between pages.

        A structured cursor is the last returned (created_at, task_id) pair.
        Newly submitted tasks can appear before that cursor and are picked up by
        the next fresh traversal. Removing completed tasks does not shift pages.
        """
        _fields(args, set(), {"to", "status", "limit", "project", "cursor"})
        limit = args.get("limit", 50)
        if type(limit) is not int or not 1 <= limit <= 100:
            raise _Fault("INVALID_ARGS", "limit must be an integer from 1 to 100.")
        clauses, parameters = [], []
        if "to" in args:
            clauses.append("recipient=?")
            parameters.append(_choice(args["to"], ACTORS, "to"))
        if "status" in args:
            clauses.append("status=?")
            parameters.append(_choice(args["status"], STATUSES, "status"))
        if "project" in args:
            clauses.append("project=?")
            parameters.append(_project(args))
        cursor = args.get("cursor")
        if cursor is not None:
            if type(cursor) is not dict or set(cursor) != {"created_at", "task_id"}:
                raise _Fault("INVALID_ARGS", "cursor must contain created_at and task_id.")
            created_at = cursor["created_at"]
            if type(created_at) not in (int, float) or not math.isfinite(created_at):
                raise _Fault("INVALID_ARGS", "cursor created_at must be a finite number.")
            # created_at is a REAL column. Binding as float also avoids SQLite's
            # narrower signed-integer limit for otherwise valid JSON numbers.
            created_at = float(created_at)
            task_id = _uuid(cursor["task_id"], "cursor task_id")
            clauses.append("(created_at < ? OR (created_at = ? AND task_id > ?))")
            parameters.extend((created_at, created_at, task_id))
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        rows = connection.execute(
            "SELECT * FROM tasks" + where + " ORDER BY created_at DESC, task_id ASC LIMIT ?",
            (*parameters, limit + 1),
        ).fetchall()
        page = rows[:limit]
        next_cursor = None
        if len(rows) > limit:
            next_cursor = {"created_at": page[-1]["created_at"], "task_id": page[-1]["task_id"]}
        return {"tasks": [self._public_task(row, include_images=False) for row in page], "next_cursor": next_cursor}
