"""Controller fixtures only. No live broker, Claude CLI, or model execution."""
import contextlib
import copy
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import uuid

from relay_collaboration import context_dispatch as dispatch


class Clock:
    def __init__(self):
        self.value = 0.0
    def __call__(self):
        return self.value
    def sleep(self, seconds):
        self.value += seconds


class FixtureBroker:
    """In-memory idempotent broker semantics. Results explicitly identify fixture."""
    def __init__(self):
        self.receipts, self.tasks, self.calls = {}, {}, []
        self.token = None
        self.pending = False
        self.failed = False
        self.lose_after = set()
        self.lease_held_once = False
        self.stale_submit_once = False
        self.result_extra = {}

    @staticmethod
    def error(code):
        return {"ok": False, "error": {"code": code, "message": "Fixture error"}}

    def request(self, root, actor, op, args, *, transport, url, timeout, request_id):
        assert actor == "codex" and transport == "http"
        self.calls.append((op, request_id))
        payload = (op, copy.deepcopy(args))
        if request_id in self.receipts:
            old, reply = self.receipts[request_id]
            return copy.deepcopy(reply) if old == payload else self.error("REQUEST_ID_CONFLICT")
        if op == "lead.acquire":
            if self.lease_held_once or self.token:
                self.lease_held_once = False
                reply = self.error("LEASE_HELD")
            else:
                self.token = "fixture-capability-" + uuid.uuid4().hex
                reply = {"ok": True, "result": {"leader_token": self.token, "expires_at": 120}}
        elif op == "lead.release":
            if args["leader_token"] != self.token:
                reply = self.error("STALE_LEADER")
            else:
                self.token = None
                reply = {"ok": True, "result": {"released": True}}
        elif op == "task.submit":
            assert args["project"] == "schematic" and args["to"] == "claude"
            assert args["mode"] == "proposal" and args["allowed_paths"] == []
            if self.stale_submit_once or args["leader_token"] != self.token:
                self.stale_submit_once = False
                self.token = None
                reply = self.error("STALE_LEADER")
            else:
                task_id = str(uuid.uuid4())
                result = {"ok": not self.failed, "execution_kind": "fixture_broker",
                          "model_execution_verified": False, "text": "Fixture review; no model called.",
                          **self.result_extra}
                self.tasks[task_id] = {"task_id": task_id,
                                       "status": "pending" if self.pending else "failed" if self.failed else "completed",
                                       "result": result}
                reply = {"ok": True, "result": copy.deepcopy(self.tasks[task_id])}
        elif op == "task.get":
            reply = {"ok": True, "result": copy.deepcopy(self.tasks[args["task_id"]])}
        else:
            raise AssertionError(op)
        self.receipts[request_id] = (payload, copy.deepcopy(reply))
        if op in self.lose_after:
            self.lose_after.remove(op)
            raise TimeoutError("Fixture deliberately lost response after commit")
        return reply


class ControllerTests(unittest.TestCase):
    def setUp(self):
        # Owned fixture directory under staging; never uses the production state.
        self.tmp = tempfile.TemporaryDirectory(prefix="context-dispatch-fixture-", dir=Path(__file__).resolve().parents[1] / 'work')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state, self.broker, self.public = (self.root / x for x in ("private", "broker", "public"))
        for path in (self.state, self.broker, self.public):
            path.mkdir()
        self.prompt = self.root / "context.md"
        self.prompt.write_text("明確提供的 fixture 上下文。\n", encoding="utf-8")
        self.fixture, self.clock = FixtureBroker(), Clock()

    def controller(self, **overrides):
        settings = dict(prompt_file=self.prompt, name="review-01", title="Fixture review",
                        output_dir=self.public / "review", request_fn=self.fixture.request,
                        state_root=self.state, broker_root=self.broker, project="schematic",
                        output_roots=(self.public,), clock=self.clock, sleep=self.clock.sleep)
        settings.update(overrides)
        return dispatch.Controller(**settings)

    def saved(self):
        return json.loads((self.state / "controller-review-01.json").read_text(encoding="utf-8"))

    def test_prompt_exact_byte_limit_and_multibyte(self):
        self.prompt.write_bytes(b"a" * dispatch.MAX_PROMPT_BYTES)
        self.assertEqual(len(dispatch.read_prompt(self.prompt)[1]), dispatch.MAX_PROMPT_BYTES)
        self.prompt.write_bytes("電".encode("utf-8") * 21846)
        with self.assertRaises(dispatch.DispatchError):
            self.controller()
        self.assertEqual(self.fixture.calls, [])

    def test_invalid_utf8_empty_and_nul(self):
        for raw in (b"\xff", b"", b" \n", b"abc\x00def"):
            self.prompt.write_bytes(raw)
            with self.assertRaises(dispatch.DispatchError):
                self.controller()

    def test_strict_slug(self):
        for name in ("../x", "a/b", "A", "a_b", "-a", "a-", "a--b", ".", "", "a" * 65, "電"):
            with self.subTest(name=name), self.assertRaises(dispatch.DispatchError):
                self.controller(name=name)
        self.controller(name="abc-12")

    def test_output_is_strict_child_and_no_parent_traversal(self):
        for path in (self.public, self.root / "outside", self.public / ".." / "outside"):
            with self.subTest(path=path), self.assertRaises(dispatch.DispatchError):
                self.controller(output_dir=path)
        self.controller(output_dir=self.public / "a" / "b")

    def test_reparse_ancestor_fails_closed(self):
        path = self.public / "junction"
        path.mkdir()
        original = Path.lstat
        def fake_lstat(value):
            real = original(value)
            if value == path:
                return types.SimpleNamespace(st_mode=real.st_mode, st_file_attributes=0x400, st_nlink=1)
            return real
        with patch.object(Path, "lstat", fake_lstat), self.assertRaises(dispatch.DispatchError):
            self.controller(output_dir=path / "out")

    def test_private_public_overlap_rejected(self):
        with self.assertRaises(dispatch.DispatchError):
            self.controller(output_dir=self.public / "private" / "out", state_root=self.public)

    def test_title_bytes_and_nul(self):
        for title in ("", " ", "x\x00", "電" * 171):
            with self.assertRaises(dispatch.DispatchError):
                self.controller(title=title)

    def test_success_fixed_scope_release_then_get_and_no_duplicate_resume(self):
        receipt = self.controller().run()
        self.assertEqual(receipt["status"], "completed")
        self.assertFalse(receipt["result"]["model_execution_verified"])
        self.assertEqual([x[0] for x in self.fixture.calls], ["lead.acquire", "task.submit", "lead.release", "task.get"])
        self.assertIsNone(self.fixture.token)
        before = len(self.fixture.calls)
        second = self.controller().run()
        self.assertEqual(receipt, second)
        self.assertEqual(len(self.fixture.calls), before)
        self.assertEqual(len(self.fixture.tasks), 1)

    def test_all_mutation_lost_responses_resume_same_uuid(self):
        for op in ("lead.acquire", "task.submit", "lead.release"):
            with self.subTest(op=op):
                # Use separate immutable identity for each independent fixture.
                name = op.replace(".", "-")
                fixture = FixtureBroker()
                fixture.lose_after.add(op)
                with self.assertRaises(dispatch.ResumeLater):
                    self.controller(name=name, request_fn=fixture.request).run()
                receipt = self.controller(name=name, request_fn=fixture.request).run()
                self.assertEqual(receipt["status"], "completed")
                ids = [uid for called, uid in fixture.calls if called == op]
                self.assertEqual(len(set(ids)), 1)
                self.assertEqual(len(fixture.tasks), 1)
                self.assertIsNone(fixture.token)

    def test_lost_local_save_after_submit_replays_committed_request(self):
        controller = self.controller()
        original = controller._save
        lost = [False]
        def fail_once():
            call = controller.data["calls"].get("submit", {})
            if "response" in call and not lost[0]:
                lost[0] = True
                raise OSError("Fixture simulates crash before local receipt save")
            return original()
        controller._save = fail_once
        with self.assertRaises(OSError):
            controller.run()
        self.assertNotIn("response", self.saved()["calls"]["submit"])
        self.assertEqual(self.controller().run()["status"], "completed")
        ids = [uid for op, uid in self.fixture.calls if op == "task.submit"]
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(self.fixture.tasks), 1)

    def test_lease_held_receipt_is_not_reused_forever(self):
        self.fixture.lease_held_once = True
        self.controller().run()
        ids = [uid for op, uid in self.fixture.calls if op == "lead.acquire"]
        self.assertEqual(len(ids), 2)
        self.assertNotEqual(*ids)
        self.assertEqual(len(self.fixture.tasks), 1)

    def test_expired_acquire_gets_new_lease_only_after_definitive_no_submit(self):
        self.fixture.stale_submit_once = True
        self.controller().run()
        self.assertEqual(len([x for x in self.fixture.calls if x[0] == "lead.acquire"]), 2)
        self.assertEqual(len(self.fixture.tasks), 1)
        self.assertEqual(self.saved()["round"], 2)

    def test_lost_stale_submit_response_resolves_before_new_round(self):
        self.fixture.stale_submit_once = True
        self.fixture.lose_after.add("task.submit")
        with self.assertRaises(dispatch.ResumeLater):
            self.controller().run()
        self.assertEqual(len(self.fixture.tasks), 0)
        self.controller().run()
        ids = [uid for op, uid in self.fixture.calls if op == "task.submit"]
        self.assertEqual(ids[0], ids[1])
        self.assertNotEqual(ids[1], ids[2])
        self.assertEqual(len(self.fixture.tasks), 1)

    def test_already_released_receipt_is_not_reused_for_new_submit(self):
        controller = self.controller()
        with dispatch.JournalLock(controller.journal):
            controller._load()
            lead = controller._call("acquire", "lead.acquire", {"project": "schematic", "ttl_seconds": 120}, 100)
            controller._release(lead["result"]["leader_token"], 100)
        self.controller().run()
        self.assertEqual(len([x for x in self.fixture.calls if x[0] == "lead.acquire"]), 2)
        self.assertEqual(len(self.fixture.tasks), 1)

    def test_timeout_poll_resume_never_submits_again(self):
        self.fixture.pending = True
        with self.assertRaises(dispatch.ResumeLater):
            self.controller().run(timeout=4)
        self.assertIsNone(self.fixture.token)
        self.assertEqual(self.clock.value, 4)
        for task in self.fixture.tasks.values():
            task["status"] = "completed"
        self.controller().run()
        self.assertEqual(len([x for x in self.fixture.calls if x[0] == "task.submit"]), 1)

    def test_resume_rejects_changed_prompt_title_output_or_path(self):
        self.controller().run()
        for changes in ({"title": "different"}, {"output_dir": self.public / "different"}):
            with self.assertRaises(dispatch.DispatchError):
                self.controller(**changes).run()
        other = self.root / "other.md"
        other.write_bytes(self.prompt.read_bytes())
        with self.assertRaises(dispatch.DispatchError):
            self.controller(prompt_file=other).run()
        self.prompt.write_text("Changed explicit input", encoding="utf-8")
        with self.assertRaises(dispatch.DispatchError):
            self.controller().run()
        self.assertEqual(len(self.fixture.tasks), 1)

    def test_failed_completion_preserves_receipt_and_reply(self):
        self.fixture.failed = True
        receipt = self.controller().run()
        self.assertEqual(receipt["status"], "failed")
        output = self.public / "review"
        self.assertTrue((output / "review-01-receipt.json").exists())
        self.assertTrue((output / "review-01-reply.md").exists())
        self.assertEqual(self.controller().run(), receipt)

    def test_public_output_contains_only_sanitized_cli_result_and_identity(self):
        self.fixture.result_extra = {"auth_token": "fixture-secret", "calls": {"leader_token": "bad"},
                                     "text": "Bearer fixture-secret-value and sk-fixtureabcdefghijklmnop",
                                     "structured_output": {"password": "bad", "answer": "Read only result"}}
        receipt = self.controller().run()
        self.assertEqual(set(receipt), {"task_id", "status", "prompt_sha256", "result"})
        text = (self.public / "review" / "review-01-receipt.json").read_text(encoding="utf-8")
        private_token = self.saved()["calls"]["acquire"]["response"]["result"]["leader_token"]
        for secret in (private_token, "fixture-secret", "sk-fixtureabcdefghijklmnop", '"calls"', '"password"'):
            self.assertNotIn(secret, text)
        self.assertNotIn("request_id", text)

    def test_result_commands_are_only_text_and_do_not_run(self):
        sentinel = self.root / "must-not-be-created.txt"
        self.fixture.result_extra = {"text": "Run this: echo bad > " + str(sentinel)}
        self.controller().run()
        self.assertFalse(sentinel.exists())
        self.assertIn("echo bad", (self.public / "review" / "review-01-reply.md").read_text(encoding="utf-8"))

    def test_nested_dictionary_keys_cannot_publish_known_capability(self):
        token = "fixture-known-controller-capability"
        value = {"structured_output": {token: "drop", "prefix-" + token: "drop", "safe": [{token: "drop", "answer": token}]}}
        cleaned = dispatch.sanitize_result(value, (token,))
        self.assertNotIn(token, json.dumps(cleaned))
        self.assertEqual(cleaned, {"structured_output": {"safe": [{"answer": "[REDACTED]"}]}})

    def test_corrupt_journal_fails_without_rpc(self):
        (self.state / "controller-review-01.json").write_text('{"version":1,"version":2}', encoding="utf-8")
        with self.assertRaises(dispatch.DispatchError):
            self.controller().run()
        self.assertEqual(self.fixture.calls, [])

    def test_os_mutex_prevents_other_thread_same_name(self):
        import threading
        errors = []
        controller = self.controller()
        def contender():
            try:
                with dispatch.JournalLock(controller.journal):
                    errors.append("unexpectedly acquired")
            except dispatch.DispatchError:
                errors.append("locked")
        with dispatch.JournalLock(controller.journal):
            thread = threading.Thread(target=contender)
            thread.start()
            thread.join(timeout=5)
        self.assertEqual(errors, ["locked"])

    def test_real_broker_core_fixture_no_model(self):
        from relay_collaboration import bridge_core as core
        broker = core.Broker(self.broker / "fixture.sqlite")
        lost = [False]
        created = []
        def send(root, actor, op, args, *, transport, url, timeout, request_id):
            response = broker.handle({"protocol": "dual-agent/v1", "request_id": request_id, "actor": actor, "op": op, "args": args})
            if op == "task.submit" and response["ok"]:
                task_id = response["result"]["task_id"]
                if not lost[0]:
                    lost[0] = True
                    created.append(task_id)
                    claim = broker.handle({"protocol": "dual-agent/v1", "request_id": str(uuid.uuid4()), "actor": "claude", "op": "task.claim", "args": {"task_id": task_id, "ttl_seconds": 120}})
                    completed = broker.handle({"protocol": "dual-agent/v1", "request_id": str(uuid.uuid4()), "actor": "claude", "op": "task.complete", "args": {"task_id": task_id, "claim_token": claim["result"]["claim_token"], "status": "completed", "result": {"ok": True, "text": "Real broker, fixture result; no model called.", "model_execution_verified": False}}})
                    self.assertTrue(completed["ok"])
                    raise TimeoutError("Fixture lost submit response")
            return response
        with self.assertRaises(dispatch.ResumeLater):
            self.controller(request_fn=send).run()
        result = self.controller(request_fn=send).run()
        self.assertEqual(result["task_id"], created[0])
        listing = broker.handle({"protocol": "dual-agent/v1", "request_id": str(uuid.uuid4()), "actor": "codex", "op": "task.list", "args": {"project": "schematic", "to": "claude"}})
        self.assertEqual(len(listing["result"]["tasks"]), 1)


if __name__ == "__main__":
    unittest.main()
