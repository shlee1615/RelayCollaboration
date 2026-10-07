"""Dispatch explicit UTF-8 context with durable requests and resumable receipts.

No model output is executed. Destinations and permissions come from versioned configuration. A name is
an immutable dispatch identity: rerun the SAME command to resume after timeout.
Use a new name only for an intentionally new review. Preserve the private
journal and broker database; deleting either defeats recovery guarantees.

Exit codes: 0 completed with result.ok, 2 terminal failure/rejection, 3 resumable
timeout/transport outage, 4 invalid local configuration/journal or active peer.
Public files: <name>-receipt.json and <name>-reply.md. Private state inherits the
already-protected runtime directory ACL; this program never reads auth tokens.
Path/reparse checks are fail-closed checks, not an OS sandbox against a hostile
same-user process replacing directories between checks and filesystem calls.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import stat
import sys
import time
import uuid


MAX_PROMPT_BYTES = 64 * 1024
MAX_JOURNAL_BYTES = 8 * 1024 * 1024
POLL_SECONDS = 2.0
WAIT_SECONDS = 300.0
MAX_ACQUISITION_ROTATIONS = 10000
_SLUG = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_SECRET_KEY = re.compile(
    r"(?:.*(?:password|credential|authorization|secret).*|(?:leader|claim|auth|access|refresh|bearer)[_-]?token|api[_-]?key|calls|controller_records)",
    re.IGNORECASE,
)


class DispatchError(Exception):
    """Safe fixed message: never attach raw transport errors/capabilities."""


class ResumeLater(DispatchError):
    pass


class BrokerRejected(DispatchError):
    pass


def _canonical_uuid(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError):
        raise DispatchError("Invalid saved or returned request/task identity.") from None
    return value


def checked_path(value, *, directory=False, must_exist=False):
    """Reject path aliases and all existing symlink/reparse ancestors."""
    raw = os.fspath(value)
    if not raw or "\x00" in raw:
        raise DispatchError("Invalid local path.")
    path = Path(raw)
    if ".." in path.parts:
        raise DispatchError("Parent traversal is not allowed.")
    path = Path(os.path.abspath(path))
    if os.name == "nt":
        if path.drive.startswith("\\"):
            raise DispatchError("Network/device paths are not allowed.")
        for part in path.parts[1:]:
            base = part.split(".", 1)[0].upper()
            if (any(c in part for c in ':*?"<>|') or part.rstrip(" .") != part
                    or base in {"CON", "PRN", "AUX", "NUL"}
                    or re.fullmatch(r"(?:COM|LPT)[1-9]", base)):
                raise DispatchError("Aliased Windows paths are not allowed.")
    for current in reversed((path, *path.parents)):
        try:
            info = current.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise DispatchError("Symlinks and reparse points are not allowed.")
        if current != path and not stat.S_ISDIR(info.st_mode):
            raise DispatchError("A path ancestor is not a directory.")
    if path.exists():
        info = path.lstat()
        if directory and not stat.S_ISDIR(info.st_mode):
            raise DispatchError("Expected a directory.")
        if not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
            raise DispatchError("Expected a regular, unlinked file.")
    elif must_exist:
        raise DispatchError("Required local path does not exist.")
    return path


def approved_output(value, roots):
    path = checked_path(value, directory=True)
    for root in roots:
        root = checked_path(root, directory=True)
        if path != root and path.is_relative_to(root):
            return path
    raise DispatchError("Output must be a child of the staging or deployed worker directory.")


def read_prompt(value):
    path = checked_path(value, must_exist=True)
    with path.open("rb") as stream:
        raw = stream.read(MAX_PROMPT_BYTES + 1)
    if len(raw) > MAX_PROMPT_BYTES:
        raise DispatchError("Prompt exceeds the 64 KiB UTF-8 limit.")
    try:
        prompt = raw.decode("utf-8")
    except UnicodeError:
        raise DispatchError("Prompt must be valid UTF-8.") from None
    if not prompt.strip() or "\x00" in prompt:
        raise DispatchError("Prompt must be nonempty and contain no NUL.")
    return path, prompt, hashlib.sha256(raw).hexdigest()


def _json(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise DispatchError("Invalid JSON data.") from None
    if len(raw) > MAX_JOURNAL_BYTES:
        raise DispatchError("Saved state exceeds its fixed size limit.")
    return raw + b"\n"


def _strict_load(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    def no_constant(_):
        raise ValueError
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=no_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise DispatchError("Private journal is invalid; preserve it for review.") from None


def atomic_write(path, raw):
    path = checked_path(path)
    checked_path(path.parent, directory=True, must_exist=True)
    tmp = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        checked_path(path)
        checked_path(path.parent, directory=True, must_exist=True)
        os.replace(tmp, path)
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if tmp.exists():
            tmp.unlink()


class JournalLock:
    """One OS lifetime lock per journal, released by the OS after a crash.

    Windows uses a per-journal named mutex. POSIX uses an empty private
    lockfile with a stable inode: never unlink a lockfile while peers may
    hold or open it. It contains no credential, PID or task payload.
    """
    def __init__(self, journal):
        self.journal = checked_path(journal)
        self.handle = None
        self._posix_handle = False

    def _acquire_posix(self, locker=None):
        # locker injection exercises path handling on Windows; native flock
        # behavior must still be validated on a POSIX host.
        if locker is None:
            import fcntl as locker
        lock_path = checked_path(self.journal.with_name(self.journal.name + '.lock'))
        checked_path(lock_path.parent, directory=True, must_exist=True)
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_CLOEXEC', 0)
        fd = None
        try:
            fd = os.open(lock_path, flags, 0o600)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise DispatchError("Controller lock must be a regular, unlinked file.")
            if hasattr(os, 'fchmod'):
                os.fchmod(fd, 0o600)
            checked_path(lock_path)
            current = lock_path.lstat()
            if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
                raise DispatchError("Controller lock identity changed while opening.")
            locker.flock(fd, locker.LOCK_EX | locker.LOCK_NB)
            checked_path(lock_path)
            current = lock_path.lstat()
            if (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino):
                raise DispatchError("Controller lock identity changed while acquiring.")
        except BaseException as exc:
            if fd is not None:
                os.close(fd)
            if isinstance(exc, OSError):
                raise DispatchError("Controller lock unavailable or another controller owns this dispatch name.") from None
            raise
        self.handle, self._posix_handle = fd, True
        return self

    def __enter__(self):
        if os.name == "nt":
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
            kernel.CreateMutexW.restype = wintypes.HANDLE
            kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            kernel.WaitForSingleObject.restype = wintypes.DWORD
            kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
            kernel.ReleaseMutex.restype = wintypes.BOOL
            kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            kernel.CloseHandle.restype = wintypes.BOOL
            identity = os.path.normcase(str(self.journal)).encode("utf-8")
            name = "Global\\CodexContextDispatch-" + hashlib.sha256(identity).hexdigest()
            handle = kernel.CreateMutexW(None, False, name)
            if not handle:
                raise DispatchError("Cannot acquire controller OS lock.")
            result = kernel.WaitForSingleObject(handle, 0)
            if result not in (0, 0x80):  # acquired, or previous owner died
                kernel.CloseHandle(handle)
                raise DispatchError("Another controller owns this dispatch name.")
            self.kernel, self.handle = kernel, handle
        else:
            self._acquire_posix()
        return self

    def __exit__(self, *_):
        if self.handle is not None:
            if self._posix_handle:
                os.close(self.handle)
            else:
                self.kernel.ReleaseMutex(self.handle)
                self.kernel.CloseHandle(self.handle)
            self.handle = None
            self._posix_handle = False


def sanitize_result(value, capabilities=()):
    """Whitelist origin to task.result, strip secret keys and known capabilities.

    Pattern redaction supplements that boundary; it cannot identify every
    possible secret a user might explicitly put into their prompt.
    """
    def visit(item, depth=0):
        if depth > 32:
            raise DispatchError("Returned result is too deeply nested.")
        if isinstance(item, dict):
            return {key: visit(child, depth + 1) for key, child in item.items()
                    if isinstance(key, str) and not _SECRET_KEY.fullmatch(key)
                    and not any(secret and secret in key for secret in capabilities)}
        if isinstance(item, list):
            return [visit(child, depth + 1) for child in item]
        if isinstance(item, str):
            for secret in capabilities:
                if secret:
                    item = item.replace(secret, "[REDACTED]")
            item = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [REDACTED]", item)
            item = re.sub(r"\bsk-[A-Za-z0-9_-]{12,}", "[REDACTED]", item)
            item = re.sub(r"(?i)((?:leader_token|claim_token|auth_token|api_key|password)\s*[\"']?\s*[:=]\s*[\"']?)[^\s\"',;}{]+",
                          r"\1[REDACTED]", item)
            return item
        if item is None or type(item) in (bool, int):
            return item
        if type(item) is float and math.isfinite(item):
            return item
        raise DispatchError("Returned result contains invalid data.")
    return visit(value)


class Controller:
    """Versioned config supplies project, transport and runtime paths.

    request_fn has bridge_client.request's
    signature. run() returns a sanitized terminal receipt, or raises ResumeLater.
    """
    def __init__(self, *, prompt_file, name, title, output_dir, request_fn,
                 state_root, broker_root, project="relay", transport="http", url="http://127.0.0.1:9041", stop_event=None,
                 output_roots=None, clock=time.monotonic, sleep=time.sleep, recipient='claude', images=None):
        if recipient not in ('codex', 'claude'):
            raise DispatchError('Recipient must be codex or claude.')
        from .vision import validate_images, image_manifest
        try:
            self.images = validate_images(images if images is not None else [])
        except ValueError as exc:
            raise DispatchError(str(exc)) from None
        self.recipient = recipient
        self.actor = 'claude' if recipient == 'codex' else 'codex'
        if not isinstance(name, str) or len(name) > 64 or not _SLUG.fullmatch(name):
            raise DispatchError("Name must be a lowercase alphanumeric slug with single hyphens, max 64 characters.")
        if not isinstance(title, str) or not title.strip() or "\x00" in title or len(title.encode("utf-8")) > 512:
            raise DispatchError("Title must be nonempty UTF-8, at most 512 bytes, without NUL.")
        self.prompt_path, self.prompt, prompt_sha = read_prompt(prompt_file)
        self.output_roots = tuple(output_roots or (Path(output_dir),))
        self.output_dir = approved_output(output_dir, self.output_roots)
        if self.prompt_path in {self.output_dir / (name + "-receipt.json"), self.output_dir / (name + "-reply.md")}:
            raise DispatchError("Prompt cannot be one of this dispatch's output files.")
        self.state_root = checked_path(state_root, directory=True, must_exist=True)
        if self.state_root == self.output_dir or self.state_root.is_relative_to(self.output_dir) or self.output_dir.is_relative_to(self.state_root):
            raise DispatchError("Public output and private state must not overlap.")
        self.journal = checked_path(self.state_root / ("controller-" + name + ".json"))
        self.broker_root = checked_path(broker_root, directory=True, must_exist=True)
        self.request = request_fn
        self.project, self.transport, self.url, self.stop_event = project, transport, url, stop_event
        self.clock, self.sleep = clock, sleep
        self.config = {
            "name": name, "title": title, "prompt_file": str(self.prompt_path),
            "output_dir": str(self.output_dir), "prompt_sha256": prompt_sha,
            "broker_root": str(self.broker_root), "actor": self.actor, "transport": self.transport, "url": self.url,
            "project": self.project, "to": self.recipient, "mode": "proposal", "allowed_paths": [],
        }
        if self.images:
            self.config['images'] = image_manifest(self.images)
        self.data = None
        self._rotations_this_run = 0

    def _save(self):
        atomic_write(self.journal, _json(self.data))

    def _load(self):
        checked_path(self.journal)
        if self.journal.exists():
            with self.journal.open("rb") as stream:
                raw = stream.read(MAX_JOURNAL_BYTES + 1)
            if len(raw) > MAX_JOURNAL_BYTES:
                raise DispatchError("Private journal is oversized; preserve it for review.")
            self.data = _strict_load(raw)
            if (not isinstance(self.data, dict) or self.data.get("version") != 1
                    or self.data.get("config") != self.config
                    or not isinstance(self.data.get("calls"), dict)
                    or type(self.data.get("round")) is not int or self.data["round"] < 1):
                raise DispatchError("Saved prompt content or parameters differ; resume with the original inputs.")
        else:
            self.data = {"version": 1, "config": self.config, "round": 1, "calls": {}}
            self._save()

    def _rpc(self, op, args, request_id, deadline):
        if self.stop_event is not None and self.stop_event.is_set():
            raise ResumeLater("Service stopping; preserve the original UUID and inputs.")
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise ResumeLater("Wait timed out; rerun the same command to resume the saved task.")
        try:
            response = self.request(self.broker_root, self.actor, op, args,
                                    transport=self.transport, url=self.url, timeout=min(5, remaining), request_id=request_id)
        except Exception:
            raise ResumeLater("Transport unavailable; rerun the same command to resume saved request IDs.") from None
        if not isinstance(response, dict) or type(response.get("ok")) is not bool:
            raise ResumeLater("Uncertain broker response; preserve state and rerun the same command.")
        if response["ok"]:
            if not isinstance(response.get("result"), dict):
                raise ResumeLater("Uncertain broker response; preserve state and rerun the same command.")
        else:
            error = response.get("error")
            if not isinstance(error, dict) or not re.fullmatch(r"[A-Z_]{1,64}", str(error.get("code", ""))):
                raise ResumeLater("Uncertain broker response; preserve state and rerun the same command.")
            if error["code"] in {"STORAGE_ERROR", "INTERNAL_ERROR"}:
                raise ResumeLater("Broker storage unavailable; rerun with saved request IDs.")
        return response

    def _call(self, key, op, args, deadline):
        calls = self.data["calls"]
        if key not in calls:
            calls[key] = {"op": op, "args": args, "request_id": str(uuid.uuid4())}
            self._save()  # durable intent precedes every mutation
        call = calls[key]
        if not isinstance(call, dict) or call.get("op") != op or call.get("args") != args:
            raise DispatchError("Saved request does not match its immutable parameters.")
        _canonical_uuid(call.get("request_id"))
        if "response" not in call:
            call["response"] = self._rpc(op, args, call["request_id"], deadline)
            self._save()  # retain response even if process dies before next phase
        return call["response"]

    def _pause(self, deadline):
        if self.stop_event is not None and self.stop_event.is_set():
            raise ResumeLater("Service stopping; preserve the original UUID and inputs.")
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise ResumeLater("Wait timed out; rerun the same command to resume the saved task.")
        if self.stop_event is not None:
            self.stop_event.wait(min(POLL_SECONDS, remaining))
        else:
            self.sleep(min(POLL_SECONDS, remaining))

    def _rotate(self):
        # Called ONLY after definitive no-submit receipt, never on uncertainty.
        if self._rotations_this_run >= MAX_ACQUISITION_ROTATIONS:
            raise ResumeLater("Acquisition retry budget reached; resume with the same journal and inputs.")
        self._rotations_this_run += 1
        self.data["round"] += 1  # Durable grouping only; never a lifetime retry limit.
        self.data["calls"] = {}
        self._save()

    def _release(self, token, deadline):
        response = self._call("release", "lead.release", {"project": self.project, "leader_token": token}, deadline)
        if not response["ok"] and response["error"]["code"] != "STALE_LEADER":
            raise BrokerRejected("Broker rejected leadership release; preserve the journal for review.")

    def _ensure_task(self, deadline):
        while True:
            # A saved successful submit receipt wins even if task_id was not
            # copied before a crash. _call replays only the original acquire.
            lead = self._call("acquire", "lead.acquire", {"project": self.project, "ttl_seconds": 120}, deadline)
            if not lead["ok"]:
                if lead["error"]["code"] == "LEASE_HELD":
                    self._rotate()  # failed receipts cannot later become leases
                    self._pause(deadline)
                    continue
                raise BrokerRejected("Broker rejected leadership acquisition.")
            token = lead["result"].get("leader_token")
            if not isinstance(token, str) or not token or len(token) > 128:
                raise DispatchError("Acquisition receipt is invalid; preserve the journal.")
            if "release" in self.data["calls"] and "submit" not in self.data["calls"]:
                # An already-released acquire receipt grants no present lease.
                self._release(token, deadline)
                self._rotate()
                continue
            submitted = self._call("submit", "task.submit", {
                "project": self.project, "leader_token": token, "to": self.recipient,
                "title": self.config["title"], "prompt": self.prompt,
                "mode": "proposal", "allowed_paths": [],
                **({'images': self.images} if self.images else {}),
            }, deadline)
            # A lost submit response is retried BEFORE release or any new round.
            self._release(token, deadline)
            if submitted["ok"]:
                task_id = _canonical_uuid(submitted["result"].get("task_id"))
                if self.data.get("task_id", task_id) != task_id:
                    raise DispatchError("Saved task identity is inconsistent.")
                self.data["task_id"] = task_id
                self._save()
                return task_id
            if submitted["error"]["code"] == "STALE_LEADER":
                self._rotate()  # broker definitively did not create this task
                self._pause(deadline)
                continue
            raise BrokerRejected("Broker rejected task submission; its receipt is preserved privately.")

    def _public_receipt(self, task):
        if task.get("task_id") != self.data["task_id"] or task.get("status") not in {"completed", "failed"}:
            raise DispatchError("Returned task does not match the saved terminal identity.")
        result = task.get("result")
        if not isinstance(result, dict):
            result = {"ok": False, "error_kind": "missing_cli_result"}
        token = self.data["calls"].get("acquire", {}).get("response", {}).get("result", {}).get("leader_token", "")
        return {"task_id": task["task_id"], "status": task["status"],
                "prompt_sha256": self.config["prompt_sha256"],
                "result": sanitize_result(result, (token,))}

    def _publish(self, receipt):
        # Cached public data still passes the same origin/key/secret filtering.
        receipt = self._public_receipt(receipt)
        output = approved_output(self.output_dir, self.output_roots)
        output.mkdir(parents=True, exist_ok=True)
        approved_output(output, self.output_roots)
        prefix = self.config["name"]
        atomic_write(output / (prefix + "-receipt.json"), _json(receipt))
        body = receipt["result"].get("text")
        if not isinstance(body, str):
            body = json.dumps(receipt["result"].get("structured_output", receipt["result"]), ensure_ascii=False, indent=2)
        reply = "# " + self.recipient.title() + " review\n\nTask: " + receipt["task_id"] + "\n\nStatus: " + receipt["status"] + "\n\n" + body + "\n"
        atomic_write(output / (prefix + "-reply.md"), reply.encode("utf-8"))
        return receipt

    def run(self, timeout=WAIT_SECONDS):
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise DispatchError("Wait timeout must be positive and finite.")
        with JournalLock(self.journal):
            self._rotations_this_run = 0
            self._load()
            if "terminal_receipt" in self.data:
                return self._publish(self.data["terminal_receipt"])
            deadline = self.clock() + timeout
            task_id = self._ensure_task(deadline)
            while True:
                response = self._rpc("task.get", {"task_id": task_id}, str(uuid.uuid4()), deadline)
                if not response["ok"]:
                    raise BrokerRejected("Broker rejected result lookup; the dispatch remains saved.")
                task = response["result"]
                if task.get("task_id") != task_id or task.get("status") not in {"pending", "running", "completed", "failed"}:
                    raise DispatchError("Invalid returned task status or identity.")
                if task["status"] in {"completed", "failed"}:
                    self.data["terminal_receipt"] = self._public_receipt(task)
                    self._save()
                    return self._publish(self.data["terminal_receipt"])
                self._pause(deadline)


