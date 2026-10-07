"""Standalone standard-library client for the local dual-agent coordination broker."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid

MAX_BODY_BYTES = 4 * 1024 * 1024


class BridgeClientError(RuntimeError):
    pass


def _safe(root, path):
    path = Path(path).absolute()
    try:
        parts = path.relative_to(root).parts
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise BridgeClientError("path is outside broker root") from exc
    cursor = root
    for part in parts:
        cursor /= part
        if cursor.is_symlink() or (hasattr(cursor, "is_junction") and cursor.is_junction()):
            raise BridgeClientError("symlinks and junctions are not permitted")
    return path


def _read(root, path, maximum):
    path = _safe(root, path)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as source:
        metadata = os.fstat(source.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > maximum:
            raise BridgeClientError("response or credential file is invalid or too large")
        data = source.read(maximum + 1)
        if len(data) > maximum:
            raise BridgeClientError("response or credential file exceeds limit")
        return data


def _atomic(root, destination, data):
    destination = _safe(root, destination)
    temporary = _safe(root, destination.with_name(f".{destination.name}.{secrets.token_hex(8)}.tmp"))
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _safe(root, destination)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def request(root, actor, op, args, transport="file", url="http://127.0.0.1:9041", timeout=30, request_id=None):
    if actor not in ("codex", "claude"):
        raise BridgeClientError("actor must be codex or claude")
    if not isinstance(args, dict):
        raise BridgeClientError("args must be a JSON object")
    if timeout <= 0:
        raise BridgeClientError("timeout must be positive")
    request_id = str(uuid.uuid4()) if request_id is None else request_id
    try:
        if not isinstance(request_id, str) or str(uuid.UUID(request_id)) != request_id:
            raise ValueError()
    except (ValueError, AttributeError) as exc:
        raise BridgeClientError("request_id must be a canonical UUID") from exc
    original_root = Path(root).absolute()
    if original_root.is_symlink() or (hasattr(original_root, "is_junction") and original_root.is_junction()):
        raise BridgeClientError("broker root cannot be a symlink or junction")
    root = original_root.resolve(strict=True)
    token = _read(root, root / "credentials" / f"{actor}.token", 256).decode("ascii").strip()
    envelope = {"protocol": "dual-agent/v1", "request_id": request_id, "actor": actor, "op": op, "args": args}
    digest = hashlib.sha256(json.dumps(envelope, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":")).encode("utf-8")).hexdigest()
    if transport == "file":
        envelope["auth_token"] = token
    payload = json.dumps(envelope, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_BODY_BYTES:
        raise BridgeClientError("request body exceeds size limit")
    if transport == "http":
        parsed = urlsplit(url)
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost")
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise BridgeClientError("HTTP URL must be a plain loopback origin")
        req = Request(url.rstrip("/") + "/rpc", data=payload, method="POST", headers={
            "Content-Type": "application/json", "Authorization": "Bearer " + token,
        })
        opener = build_opener(ProxyHandler({}), _NoRedirect())
        try:
            response = opener.open(req, timeout=timeout)
        except HTTPError as exc:
            response = exc
        with response:
            raw = response.read(MAX_BODY_BYTES + 1)
        if len(raw) > MAX_BODY_BYTES:
            raise BridgeClientError("HTTP response exceeds size limit")
    elif transport == "file":
        response_path = _safe(root, root / "responses" / f"{request_id}.json")
        # Preserve shared responses; match their request digest to avoid stale replies.
        _atomic(root, root / "requests" / f"{request_id}.json", payload)
        deadline = time.monotonic() + timeout
        while True:
            try:
                raw = _read(root, response_path, MAX_BODY_BYTES)
                candidate = json.loads(raw.decode("utf-8"))
                metadata = candidate.get("_transport", {}) if isinstance(candidate, dict) else {}
                if metadata.get("request_id") == request_id and metadata.get("request_digest") == digest:
                    break
            except FileNotFoundError:
                pass
            if time.monotonic() >= deadline:
                raise TimeoutError(f"broker did not respond before timeout; retry request_id={request_id}")
            time.sleep(min(0.1, max(0.001, deadline - time.monotonic())))
    else:
        raise BridgeClientError("transport must be file or http")
    try:
        result = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise BridgeClientError("broker returned invalid JSON") from exc
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        raise BridgeClientError("broker returned an invalid response envelope")
    result.pop("_transport", None)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--actor", required=True, choices=["codex", "claude"])
    parser.add_argument("--transport", default="file", choices=["file", "http"])
    parser.add_argument("--url", default="http://127.0.0.1:9041")
    parser.add_argument("--timeout", type=float, default=30)
    parser.add_argument("--request-id")
    sub = parser.add_subparsers(dest="command", required=True)
    call = sub.add_parser("call")
    call.add_argument("op")
    arguments = call.add_mutually_exclusive_group()
    arguments.add_argument("--args-json")
    arguments.add_argument("--args-file")
    parsed = parser.parse_args(argv)
    try:
        raw = Path(parsed.args_file).read_text(encoding="utf-8-sig") if parsed.args_file else parsed.args_json or "{}"
        result = request(parsed.root, parsed.actor, parsed.op, json.loads(raw), parsed.transport,
                         parsed.url, parsed.timeout, parsed.request_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 2
    except (BridgeClientError, OSError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": {"code": "client_error", "message": str(exc)}}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
