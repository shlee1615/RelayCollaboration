"""Coordination tests with mock executors, NOT actual model authentication/tests."""
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

from relay_collaboration.worker_core import Worker, WorkerError, WorkerSettings, checked_path

from relay_collaboration.bridge_core import Broker
from relay_collaboration.bridge_client import request as bridge_request
from relay_collaboration.bridge_server import BridgeService


class MockExecutor:
    """A deterministic fake model: no CLI, no credentials, no model request."""
    def __init__(self, callback=None):
        self.calls = []
        self.callback = callback
        self.ready = True
        self.preflight_count = 0

    def preflight(self):
        self.preflight_count += 1
        return {"ok": self.ready, "auth_ready": self.ready, "auth_evidence": "mock_test"}

    def __call__(self, task, context):
        self.calls.append((task, context))
        if self.callback:
            return self.callback(task, context)
        return {"ok": True, "status": "completed", "text": "Mock evidence reviewed.", "execution_kind": "mock_test"}


class CoreTransport:
    """Inject the real broker protocol; real HTTP is covered separately below."""
    def __init__(self, broker):
        self.broker = broker
        self.calls = []
        self.fail_before = None
        self.fail_after = None

    def __call__(self, root, actor, op, args, **kwargs):
        request_id = kwargs.get("request_id") or str(uuid.uuid4())
        self.calls.append((actor, op, request_id))
        if self.fail_before == op:
            self.fail_before = None
            raise TimeoutError("Mock transport interruption")
        result = self.broker.handle({"protocol": "dual-agent/v1", "request_id": request_id,
                                     "actor": actor, "op": op, "args": args})
        if self.fail_after == op:
            self.fail_after = None
            raise TimeoutError("Mock lost receipt")
        return result


class WorkerCoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="worker-core-test-", dir=Path(__file__).resolve().parents[1] / 'work')
        self.root = Path(self.temp.name)
        self.broker_root = self.root / "broker"
        self.broker_root.mkdir()
        (self.broker_root / "runtime").mkdir()
        self.project_root = self.root / "project"
        self.project_root.mkdir()
        (self.project_root / "docs").mkdir()
        self.database = self.broker_root / "broker.sqlite"
        self.broker = Broker(self.database)
        self.transport = CoreTransport(self.broker)
        self.executor = MockExecutor()
        self.workers = []

    def tearDown(self):
        for worker in self.workers:
            worker.close()
        self.temp.cleanup()

    def settings(self, actor="claude", state_name=None, **changes):
        args = dict(actor=actor, project="schematic", broker_root=self.broker_root,
                    state_root=self.root / (state_name or "worker-" + actor), project_root=self.project_root,
                    allowed_paths=("docs",), claim_ttl_seconds=30, task_timeout_seconds=10,
                    safety_margin_seconds=10, request_timeout_seconds=2, shutdown_grace_seconds=1,
                    poll_seconds=0.02, auth_retry_seconds=0.02)
        args.update(changes)
        return WorkerSettings(**args)

    def worker(self, actor="claude", state_name=None, executor=None, **changes):
        executor = executor or self.executor
        worker = Worker(self.settings(actor, state_name, **changes), executor, executor.preflight, self.transport)
        self.workers.append(worker)
        return worker

    def call(self, actor, op, args):
        result = self.transport(self.broker_root, actor, op, args)
        self.assertTrue(result["ok"], result)
        return result["result"]

    def task(self, recipient="claude", mode="proposal", paths=None, project="schematic"):
        sender = "codex" if recipient == "claude" else "claude"
        leader = self.call(sender, "lead.acquire", {"project": project, "ttl_seconds": 30})
        task = self.call(sender, "task.submit", {"project": project, "leader_token": leader["leader_token"],
                         "to": recipient, "title": "Mock review", "prompt": "Review this explicit mock evidence.",
                         "mode": mode, "allowed_paths": [] if paths is None else paths})
        self.call(sender, "lead.release", {"project": project, "leader_token": leader["leader_token"]})
        return task

    def expire(self, task_id):
        connection = sqlite3.connect(self.database)
        connection.execute("UPDATE tasks SET claim_expires_at=0 WHERE task_id=?", (task_id,))
        connection.commit()
        connection.close()

    def test_both_actors_consume_and_complete(self):
        for actor in ("codex", "claude"):
            task = self.task(actor)
            worker = self.worker(actor)
            worker.run_once()
            public = self.call(actor, "task.get", {"task_id": task["task_id"]})
            self.assertEqual(public["status"], "completed")
            self.assertEqual(public["result"]["execution_kind"], "mock_test")
            self.assertNotIn("claim_token", self.executor.calls[-1][0])
            self.assertLessEqual(self.executor.calls[-1][1].timeout_seconds + 10, 30)

    def test_auth_failure_never_claims(self):
        task = self.task()
        self.executor.ready = False
        worker = self.worker()
        status = worker.run_once()
        self.assertEqual(status["state"], "auth_blocked")
        self.assertFalse(any(op == "task.claim" for _, op, _ in self.transport.calls))
        self.assertEqual(self.call("claude", "task.get", {"task_id": task["task_id"]})["status"], "pending")
        self.assertEqual(self.executor.calls, [])

    def test_auth_can_become_ready_after_recheck(self):
        task = self.task()
        self.executor.ready = False
        worker = self.worker()
        worker.run_once()
        self.executor.ready = True
        time.sleep(0.03)
        worker.run_once()
        self.assertEqual(self.call("claude", "task.get", {"task_id": task["task_id"]})["status"], "completed")

    def test_idle_poll_does_not_repeat_cli_preflight(self):
        worker = self.worker()
        for _ in range(4):
            worker.run_once()
        self.assertEqual(self.executor.preflight_count, 1)
        self.task()
        worker.run_once()
        self.assertEqual(self.executor.preflight_count, 2)

    def test_implementation_and_scope_rejected_without_claim(self):
        tasks = [self.task(mode="implementation", paths=["docs"]), self.task(paths=["../outside"]),
                 self.task(paths=["secret.txt"]), self.task(paths=["docs/**"])]
        worker = self.worker()
        worker.run_once()
        self.assertEqual(self.executor.calls, [])
        for task in tasks:
            self.assertEqual(worker._row(task["task_id"])["state"], "rejected")
            self.assertEqual(self.call("claude", "task.get", {"task_id": task["task_id"]})["status"], "pending")
        with self.assertRaises(WorkerError):
            self.settings(allowed_modes=("implementation",))

    def test_wrong_project_not_consumed_and_allowed_scope_passes(self):
        wrong = self.task(project="other")
        good = self.task(paths=["docs/example.md"])
        worker = self.worker()
        worker.run_once()
        self.assertIsNone(worker._row(wrong["task_id"]))
        self.assertEqual(worker._row(good["task_id"])["state"], "completed")

    def test_actor_lock_cannot_be_evaded_by_different_state_root(self):
        first = self.worker(state_name="one")
        second = self.worker(state_name="two")
        first.start()
        with self.assertRaisesRegex(WorkerError, "Another worker"):
            second.start()
        first.close()
        second.start()

    def test_actor_lock_is_visible_to_another_process(self):
        worker = self.worker()
        worker.start()
        code = "from pathlib import Path; from relay_collaboration.worker_core import WorkerLock; import sys; WorkerLock(Path(sys.argv[1]),'claude')"
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
        result = subprocess.run([sys.executable, "-B", "-X", "utf8", "-c", code, str(self.broker_root)],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=10, **options)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b"Another worker", result.stderr)

    def test_lost_claim_receipt_reuses_uuid_without_duplicate_execution(self):
        task = self.task()
        self.transport.fail_after = "task.claim"
        worker = self.worker()
        self.assertEqual(worker.run_once()["state"], "transport_error")
        self.assertEqual(self.executor.calls, [])
        worker.run_once()
        claims = [identifier for _, op, identifier in self.transport.calls if op == "task.claim"]
        self.assertEqual(claims[0], claims[1])
        self.assertEqual(len(self.executor.calls), 1)
        self.assertEqual(self.call("claude", "task.get", {"task_id": task["task_id"]})["claim_attempt"], 1)

    def test_lost_completion_receipt_survives_worker_restart(self):
        task = self.task()
        self.transport.fail_after = "task.complete"
        first = self.worker()
        first.run_once()
        self.assertEqual(first._row(task["task_id"])["state"], "result_ready")
        first.close()
        second = self.worker()
        second.run_once()
        completes = [identifier for _, op, identifier in self.transport.calls if op == "task.complete"]
        self.assertEqual(completes[0], completes[1])
        self.assertEqual(len(self.executor.calls), 1)
        self.assertEqual(second._row(task["task_id"])["state"], "completed")

    def test_saved_completion_reports_when_model_auth_is_now_unavailable(self):
        task = self.task()
        first = self.worker()
        self.transport.fail_before = "task.complete"
        first.run_once()
        first.close()
        self.executor.ready = False
        second = self.worker()
        second.run_once()
        self.assertEqual(second._row(task["task_id"])["state"], "completed")
        self.assertEqual(len(self.executor.calls), 1)

    def test_permanent_completion_rejection_does_not_poison_queue(self):
        task = self.task()
        worker = self.worker()
        original = worker.request_fn
        def permanent(root, actor, op, args, **kwargs):
            if op == "task.complete":
                return {"ok": False, "error": {"code": "INVALID_REQUEST", "message": "Mock permanent rejection"}}
            return original(root, actor, op, args, **kwargs)
        worker.request_fn = permanent
        worker.run_once()
        self.assertEqual(worker._row(task["task_id"])["reason"], "completion_permanently_rejected")
        worker.run_once()
        self.assertEqual(len(self.executor.calls), 1)

    def test_broker_incompatible_result_is_quarantined_before_completion(self):
        task = self.task()
        executor = MockExecutor(lambda task, context: {"ok": True, "large_integer": 2 ** 100})
        worker = self.worker(executor=executor)
        worker.run_once()
        self.assertEqual(worker._row(task["task_id"])["reason"], "invalid_executor_result")
        self.assertFalse(any(op == "task.complete" for _, op, _ in self.transport.calls))

    def test_expired_result_reclaimed_only_to_report_saved_result(self):
        task = self.task()
        worker = self.worker()
        self.transport.fail_before = "task.complete"
        worker.run_once()
        self.expire(task["task_id"])
        worker.run_once()
        public = self.call("claude", "task.get", {"task_id": task["task_id"]})
        self.assertEqual(public["status"], "completed")
        self.assertEqual(public["claim_attempt"], 2)
        self.assertEqual(len(self.executor.calls), 1)

    def test_changed_claim_generation_blocks_cached_result_overwrite(self):
        task = self.task()
        worker = self.worker()
        self.transport.fail_before = "task.complete"
        worker.run_once()
        self.expire(task["task_id"])
        self.call("claude", "task.claim", {"task_id": task["task_id"], "ttl_seconds": 30})
        worker.run_once()
        row = worker._row(task["task_id"])
        self.assertEqual(row["state"], "manual_review")
        self.assertEqual(row["reason"], "claim_generation_changed")
        self.assertEqual(len(self.executor.calls), 1)

    def test_crash_after_execution_start_never_reruns(self):
        task = self.task()
        first = self.worker()
        first.start()
        first._register(task, "claiming")
        first._claim(task["task_id"])
        first._update(task["task_id"], state="executing", execution_started_at=time.time())
        first.close()
        second = self.worker()
        second.run_once()
        self.assertEqual(second._row(task["task_id"])["state"], "manual_review")
        self.assertEqual(self.executor.calls, [])

    def test_unknown_expired_claim_is_not_automatically_reexecuted(self):
        task = self.task()
        self.call("claude", "task.claim", {"task_id": task["task_id"], "ttl_seconds": 30})
        self.expire(task["task_id"])
        worker = self.worker()
        worker.run_once()
        self.assertEqual(worker._row(task["task_id"])["reason"], "untracked_previous_claim")
        self.assertEqual(self.executor.calls, [])

    def test_timeout_cancels_executor_and_requires_review(self):
        def wait_for_stop(task, context):
            context.stop_event.wait(2)
            return {"ok": False, "status": "cancelled", "execution_kind": "mock_test"}
        executor = MockExecutor(wait_for_stop)
        task = self.task()
        worker = self.worker(executor=executor, task_timeout_seconds=0.1)
        started = time.monotonic()
        worker.run_once()
        self.assertLess(time.monotonic() - started, 2)
        self.assertEqual(worker._row(task["task_id"])["state"], "manual_review")
        worker.run_once()
        self.assertEqual(len(executor.calls), 1)

    def test_noncooperative_executor_retains_actor_lock(self):
        release = threading.Event()
        executor = MockExecutor(lambda task, context: release.wait(3) or {"ok": True})
        task = self.task()
        worker = self.worker(executor=executor, task_timeout_seconds=0.05, shutdown_grace_seconds=0.05)
        try:
            with self.assertRaisesRegex(WorkerError, "Executor did not stop"):
                worker.run_once()
            with self.assertRaisesRegex(WorkerError, "lock retained"):
                worker.close()
            second = self.worker(state_name="other")
            with self.assertRaisesRegex(WorkerError, "Another worker"):
                second.start()
            self.assertEqual(worker._row(task["task_id"])["reason"], "executor_did_not_stop")
        finally:
            release.set()
            worker.active_thread.join(2)

    def test_stop_event_cleanly_stops_idle_run(self):
        worker = self.worker()
        event = threading.Event()
        thread = threading.Thread(target=worker.run, args=(event,))
        thread.start()
        time.sleep(0.15)
        event.set()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(worker.snapshot()["state"], "stopped")
        self.assertIsNone(worker.lock)

    def test_status_and_evidence_do_not_publish_capabilities(self):
        task = self.task()
        def result(task, context):
            connection = sqlite3.connect(self.database)
            row = connection.execute("SELECT claim_token FROM tasks WHERE task_id=?", (task["task_id"],)).fetchone()
            connection.close()
            return {"ok": True, "text": row[0] + " Bearer forbidden sk-0123456789abcdefghijkl", "api_key": "private", "execution_kind": "mock_test"}
        worker = self.worker(executor=MockExecutor(result))
        worker.run_once()
        public = self.call("claude", "task.get", {"task_id": task["task_id"]})
        self.assertEqual(public["result"]["api_key"], "[redacted]")
        self.assertNotIn("forbidden", public["result"]["text"])
        evidence = (worker.settings.state_root / "evidence" / (task["task_id"] + ".json")).read_text(encoding="utf-8")
        status = (worker.settings.state_root / "status.json").read_text(encoding="utf-8")
        self.assertNotIn("text", evidence)
        self.assertNotIn("claim_token", status + evidence)

    def test_intervening_expired_generation_during_reclaim_requires_review(self):
        task = self.task()
        worker = self.worker()
        self.transport.fail_before = "task.complete"
        worker.run_once()
        self.expire(task["task_id"])
        original = worker.request_fn
        armed = [True]

        def race(root, actor, op, args, **kwargs):
            if op == "task.claim" and armed[0]:
                armed[0] = False
                self.call("claude", "task.claim", {"task_id": task["task_id"], "ttl_seconds": 30})
                self.expire(task["task_id"])
            return original(root, actor, op, args, **kwargs)

        worker.request_fn = race
        worker.run_once()
        self.assertEqual(worker._row(task["task_id"])["reason"], "intervening_claim_generation")
        self.assertEqual(len(self.executor.calls), 1)

    def test_lost_racing_reclaim_receipt_retains_generation_guard_after_restart(self):
        task = self.task()
        first = self.worker()
        self.transport.fail_before = "task.complete"
        first.run_once()
        self.expire(task["task_id"])
        original = self.transport
        armed = [True]
        def race_and_drop(root, actor, op, args, **kwargs):
            if op == "task.claim" and armed[0]:
                armed[0] = False
                self.call("claude", "task.claim", {"task_id": task["task_id"], "ttl_seconds": 30})
                self.expire(task["task_id"])
                original(root, actor, op, args, **kwargs)
                raise TimeoutError("Mock lost racing reclaim receipt")
            return original(root, actor, op, args, **kwargs)
        first.request_fn = race_and_drop
        first.run_once()
        self.assertEqual(first._row(task["task_id"])["state"], "claiming")
        self.assertEqual(first._row(task["task_id"])["expected_claim_attempt"], 2)
        first.close()
        second = self.worker()
        second.run_once()
        self.assertEqual(second._row(task["task_id"])["reason"], "intervening_claim_generation")
        self.assertEqual(len(self.executor.calls), 1)

    def test_normal_lost_reclaim_receipt_recovers_completion_with_new_uuid(self):
        task = self.task()
        first = self.worker()
        self.transport.fail_before = "task.complete"
        first.run_once()
        old_completion_id = first._row(task["task_id"])["complete_request_id"]
        self.expire(task["task_id"])
        self.transport.fail_after = "task.claim"
        first.run_once()
        self.assertEqual(first._row(task["task_id"])["state"], "claiming")
        self.assertIsNone(first._row(task["task_id"])["complete_request_id"])
        first.close()
        second = self.worker()
        second.run_once()
        row = second._row(task["task_id"])
        self.assertEqual(row["state"], "completed")
        self.assertNotEqual(row["complete_request_id"], old_completion_id)
        self.assertEqual(self.call("claude", "task.get", {"task_id": task["task_id"]})["status"], "completed")
        self.assertEqual(len(self.executor.calls), 1)

    def test_initial_claim_race_never_executes_unknown_previous_attempt(self):
        task = self.task()
        worker = self.worker()
        original = worker.request_fn
        armed = [True]
        def race(root, actor, op, args, **kwargs):
            if op == "task.claim" and armed[0]:
                armed[0] = False
                self.call("claude", "task.claim", {"task_id": task["task_id"], "ttl_seconds": 30})
                self.expire(task["task_id"])
            return original(root, actor, op, args, **kwargs)
        worker.request_fn = race
        worker.run_once()
        self.assertEqual(worker._row(task["task_id"])["reason"], "intervening_claim_generation")
        self.assertEqual(self.executor.calls, [])

    def test_full_wrong_project_window_is_reported_not_empty(self):
        valid = self.task()
        for _ in range(100):
            self.task(project="other")
        worker = self.worker()
        original = worker.request_fn
        def old_broker(root, actor, op, args, **kwargs):
            if op == "task.list" and ("project" in args or "cursor" in args):
                return {"ok": False, "error": {"code": "INVALID_ARGS", "message": "Mock old broker"}}
            response = original(root, actor, op, args, **kwargs)
            if op == "task.list" and response.get("ok"):
                response["result"].pop("next_cursor", None)
            return response
        worker.request_fn = old_broker
        status = worker.run_once()
        self.assertEqual(status["state"], "attention_required")
        self.assertEqual(status["reason"], "queue_window_full_no_cursor")
        self.assertEqual(self.executor.calls, [])
        self.assertIsNone(worker._row(valid["task_id"]))

    def test_project_filter_reaches_task_behind_other_project_backlog(self):
        valid = self.task()
        for _ in range(100):
            self.task(project="other")
        worker = self.worker()
        worker.run_once()
        self.assertEqual(worker._row(valid["task_id"])["state"], "completed")
        self.assertEqual(len(self.executor.calls), 1)

    def test_cursor_reaches_task_behind_locally_rejected_backlog(self):
        valid = self.task()
        for _ in range(100):
            self.task(mode="implementation", paths=["docs"])
        worker = self.worker()
        worker.run_once()
        self.assertEqual(worker._row(valid["task_id"])["state"], "completed")
        self.assertEqual(len(self.executor.calls), 1)

    def test_reparse_ancestor_rejected(self):
        alias = self.root / "alias"
        if os.name == "nt":
            result = subprocess.run(["cmd.exe", "/c", "mklink", "/J", str(alias), str(self.project_root)],
                                    capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode:
                self.skipTest("Junction creation unavailable")
        else:
            alias.symlink_to(self.project_root, target_is_directory=True)
        with self.assertRaisesRegex(WorkerError, "reparse|Symlink"):
            checked_path(alias / "docs" / "future.txt")

    def test_real_http_transport_with_mock_executor(self):
        actual_root = self.root / "actual-bridge"
        service = BridgeService(actual_root)
        port = service.start(port=0)
        try:
            def rpc(actor, op, args):
                response = bridge_request(actual_root, actor, op, args, transport="http", url=f"http://127.0.0.1:{port}")
                self.assertTrue(response["ok"], response)
                return response["result"]
            leader = rpc("codex", "lead.acquire", {"project": "schematic", "ttl_seconds": 30})
            task = rpc("codex", "task.submit", {"project": "schematic", "leader_token": leader["leader_token"], "to": "claude", "title": "HTTP mock", "prompt": "Test explicit mock evidence.", "mode": "proposal"})
            settings = self.settings(broker_root=actual_root, state_name="http-worker", url=f"http://127.0.0.1:{port}")
            worker = Worker(settings, self.executor, self.executor.preflight)
            self.workers.append(worker)
            worker.run_once()
            self.assertEqual(rpc("claude", "task.get", {"task_id": task["task_id"]})["status"], "completed")
            worker.close()
        finally:
            service.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
