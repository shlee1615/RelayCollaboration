"""End-to-end transport and boundary tests against the real SQLite broker."""

import http.client
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from relay_collaboration.bridge_client import BridgeClientError, request
from relay_collaboration.bridge_server import BridgeService, MAX_BODY_BYTES


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dual-agent-test-", dir=Path(__file__).resolve().parents[1] / 'work')
        self.root = Path(self.temp.name)
        self.service = BridgeService(self.root, poll_seconds=0.02)
        self.port = self.service.start(port=0)
        self.url = f"http://127.0.0.1:{self.port}"

    def tearDown(self):
        self.service.close()
        self.temp.cleanup()

    def envelope(self, actor="codex", op="health", args=None, request_id=None):
        return {"protocol": "dual-agent/v1", "request_id": request_id or str(uuid.uuid4()),
                "actor": actor, "op": op, "args": args or {}}

    def raw(self, body=None, headers=None, method="POST", path="/rpc"):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        supplied = {"Content-Type": "application/json",
                    "Authorization": "Bearer " + self.service.tokens["codex"]}
        if headers:
            supplied.update(headers)
        data = json.dumps(self.envelope()).encode() if body is None else body
        connection.request(method, path, body=data, headers=supplied)
        response = connection.getresponse()
        status, response_headers = response.status, dict(response.getheaders())
        parsed = json.loads(response.read().decode())
        connection.close()
        return status, response_headers, parsed

    def call(self, actor, op="health", args=None, transport="http", request_id=None):
        return request(self.root, actor, op, args or {}, transport=transport,
                       url=self.url, timeout=5, request_id=request_id)

    def test_http_health_for_each_actor(self):
        for actor in ("codex", "claude"):
            with self.subTest(actor=actor):
                self.assertTrue(self.call(actor)["ok"])

    def test_public_health_has_no_credentials_or_paths(self):
        status, headers, result = self.raw(method="GET", path="/health")
        self.assertEqual(status, 200)
        self.assertEqual(set(result), {"ok", "protocol", "service"})
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_http_rejects_missing_wrong_and_other_actor_credentials(self):
        for value in ("", "Bearer invalid", "Bearer " + self.service.tokens["claude"]):
            with self.subTest(credential_kind=len(value)):
                status, _, result = self.raw(headers={"Authorization": value})
                self.assertEqual(status, 401)
                self.assertEqual(result["error"]["code"], "unauthorized")

    def test_http_rejects_remote_host_and_wrong_port(self):
        for host in ("example.com", f"127.0.0.1.evil.example:{self.port}", "127.0.0.1:1"):
            with self.subTest(host=host):
                self.assertEqual(self.raw(headers={"Host": host})[0], 403)

    def test_http_rejects_remote_null_and_malformed_origins(self):
        for origin in ("https://example.com", "null", self.url + "/path"):
            with self.subTest(origin=origin):
                self.assertEqual(self.raw(headers={"Origin": origin})[0], 403)
        self.assertEqual(self.raw(headers={"Origin": self.url})[0], 200)

    def test_http_rejects_content_type_body_and_transfer_encoding(self):
        self.assertEqual(self.raw(headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.raw(body=b"{")[0], 400)
        self.assertEqual(self.raw(body=b"\xff")[0], 400)
        self.assertEqual(self.raw(body=b"", headers={"Content-Length": str(MAX_BODY_BYTES + 1)})[0], 413)
        self.assertEqual(self.raw(headers={"Transfer-Encoding": "chunked"})[0], 400)

    def test_http_rejects_duplicate_headers(self):
        for header, value in (("Host", f"127.0.0.1:{self.port}"), ("Content-Length", "0")):
            connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            body = json.dumps(self.envelope()).encode()
            connection.putrequest("POST", "/rpc")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(len(body)))
            connection.putheader("Authorization", "Bearer " + self.service.tokens["codex"])
            connection.putheader(header, value)
            connection.endheaders(body)
            response = connection.getresponse()
            self.assertIn(response.status, (400, 403))
            response.read()
            connection.close()

    def test_http_does_not_enable_cors_or_unknown_endpoints(self):
        self.assertEqual(self.raw(method="OPTIONS")[0], 405)
        self.assertEqual(self.raw(path="/execute")[0], 404)

    def test_client_rejects_remote_url_and_oversized_request(self):
        for url in ("https://127.0.0.1", "http://example.com", "http://user@127.0.0.1", self.url + "/rpc"):
            with self.subTest(url=url), self.assertRaises(BridgeClientError):
                request(self.root, "codex", "health", {}, transport="http", url=url)
        with self.assertRaises(BridgeClientError):
            self.call("codex", args={"large": "x" * MAX_BODY_BYTES})

    def test_file_roundtrip_and_credential_free_archive(self):
        request_id = str(uuid.uuid4())
        self.assertTrue(self.call("claude", transport="file", request_id=request_id)["ok"])
        archived = self.root / "processed" / f"{request_id}.json"
        # The response is published before the archive; wait briefly for the file worker.
        import time
        deadline = time.monotonic() + 2
        while not archived.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        text = archived.read_text(encoding="utf-8")
        self.assertNotIn("auth_token", text)
        self.assertNotIn(self.service.tokens["claude"], text)

    def test_malformed_file_isolated_from_good_request(self):
        bad_id = str(uuid.uuid4())
        (self.root / "requests" / f"{bad_id}.json").write_bytes(b"not JSON")
        self.assertTrue(self.call("codex", transport="file")["ok"])
        result = json.loads((self.root / "responses" / f"{bad_id}.json").read_text(encoding="utf-8"))
        self.assertEqual(result["error"]["code"], "bad_request")

    def test_file_auth_filename_and_size_checks(self):
        self.service.stop_event.set()
        self.service.file_thread.join(timeout=2)
        for kind in ("auth", "filename", "size"):
            request_id = str(uuid.uuid4())
            envelope = self.envelope(request_id=request_id)
            envelope["auth_token"] = self.service.tokens["codex"]
            if kind == "auth":
                envelope["auth_token"] = self.service.tokens["claude"]
            elif kind == "filename":
                envelope["request_id"] = str(uuid.uuid4())
            body = b"x" * (MAX_BODY_BYTES + 1) if kind == "size" else json.dumps(envelope).encode()
            (self.root / "requests" / f"{request_id}.json").write_bytes(body)
            self.service.process_files_once()
            result = json.loads((self.root / "responses" / f"{request_id}.json").read_text(encoding="utf-8"))
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"]["code"], "unauthorized" if kind == "auth" else "bad_request")

    def test_unicode_token_and_deep_json_do_not_stop_file_worker(self):
        for kind in ("unicode-token", "deep-json"):
            request_id = str(uuid.uuid4())
            envelope = self.envelope(request_id=request_id)
            envelope["auth_token"] = "\u975eASCII"
            body = b"[" * 2000 + b"0" + b"]" * 2000 if kind == "deep-json" else json.dumps(envelope).encode()
            (self.root / "requests" / f"{request_id}.json").write_bytes(body)
            self.assertTrue(self.call("codex", transport="file")["ok"])
            result = json.loads((self.root / "responses" / f"{request_id}.json").read_text(encoding="utf-8"))
            self.assertFalse(result["ok"])
        # Python versions differ in parser recursion thresholds; either parser or
        # envelope validation must reject the input without losing the service.
        status, _, result = self.raw(body=b"[" * 2000 + b"0" + b"]" * 2000)
        self.assertIn(status, (200, 400))
        self.assertFalse(result["ok"])
        self.assertTrue(self.call("codex")["ok"])

    def test_inflight_capture_preserves_concurrent_changed_request(self):
        self.service.stop_event.set()
        self.service.file_thread.join(timeout=2)
        request_id = str(uuid.uuid4())
        first = self.envelope(request_id=request_id)
        first["auth_token"] = self.service.tokens["codex"]
        replacement = self.envelope(op="lead.acquire", args={"project": "schematic", "ttl_seconds": 60}, request_id=request_id)
        replacement["auth_token"] = self.service.tokens["codex"]
        queued = self.root / "requests" / f"{request_id}.json"
        queued.write_text(json.dumps(first), encoding="utf-8")
        original = self.service.dispatch

        def dispatch_with_retry(envelope, token):
            queued.write_text(json.dumps(replacement), encoding="utf-8")
            self.service.dispatch = original
            return original(envelope, token)

        self.service.dispatch = dispatch_with_retry
        self.service.process_files_once()
        self.assertTrue(queued.exists(), "captured request must not delete a concurrently queued retry")
        self.service.process_files_once()
        response = json.loads((self.root / "responses" / f"{request_id}.json").read_text(encoding="utf-8"))
        self.assertFalse(response["ok"], response)
        self.assertFalse(queued.exists())

    def test_one_live_server_per_root_and_lock_released(self):
        with self.assertRaisesRegex(RuntimeError, "another broker"):
            BridgeService(self.root)
        self.service.close()
        self.service = BridgeService(self.root)
        self.service.start(port=0)

    def test_tokens_persist_after_restart(self):
        before = dict(self.service.tokens)
        self.service.close()
        self.service = BridgeService(self.root)
        self.port = self.service.start(port=0)
        self.url = f"http://127.0.0.1:{self.port}"
        self.assertEqual(before, self.service.tokens)
        self.assertTrue(self.call("claude")["ok"])

    def test_both_actors_can_lead_and_delegate_across_transports(self):
        for leader, worker, transport in (("codex", "claude", "file"), ("claude", "codex", "http")):
            lease = self.call(leader, "lead.acquire", {"project": "schematic", "ttl_seconds": 60}, transport)
            self.assertTrue(lease["ok"], lease)
            token = lease["result"]["leader_token"]
            submitted = self.call(leader, "task.submit", {
                "project": "schematic", "leader_token": token, "to": worker,
                "title": "Review shared evidence", "prompt": "Read the declared evidence and report limitations.",
                "mode": "read_only", "allowed_paths": [],
            }, transport)
            self.assertTrue(submitted["ok"], submitted)
            task_id = submitted["result"]["task_id"]
            claimed = self.call(worker, "task.claim", {"task_id": task_id, "ttl_seconds": 60}, transport)
            self.assertTrue(claimed["ok"], claimed)
            completed = self.call(worker, "task.complete", {
                "task_id": task_id, "claim_token": claimed["result"]["claim_token"],
                "status": "completed", "result": {"summary": "Evidence reviewed."},
            }, transport)
            self.assertTrue(completed["ok"], completed)
            self.assertEqual(self.call(leader, "task.get", {"task_id": task_id})["result"]["status"], "completed")
            self.assertTrue(self.call(leader, "lead.release", {"project": "schematic", "leader_token": token})["ok"])

    def test_request_replay_is_idempotent_across_transports(self):
        request_id = str(uuid.uuid4())
        args = {"project": "schematic", "ttl_seconds": 60}
        first = self.call("codex", "lead.acquire", args, "file", request_id)
        second = self.call("codex", "lead.acquire", args, "http", request_id)
        third = self.call("codex", "lead.acquire", args, "file", request_id)
        self.assertTrue(first["ok"], first)
        self.assertEqual(first, second)
        self.assertEqual(first, third)
        altered = self.call("codex", "lead.acquire", {"project": "changed", "ttl_seconds": 60}, "file", request_id)
        self.assertFalse(altered["ok"], altered)


if __name__ == "__main__":
    unittest.main(verbosity=2)
