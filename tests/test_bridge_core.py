"""Behavioral tests for dual-agent coordination; no live models or projects."""

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import threading
import unittest
from unittest.mock import patch
import uuid

from relay_collaboration.bridge_core import Broker, MAX_PROMPT_BYTES, MAX_RESULT_BYTES, PROTOCOL


class BrokerTests(unittest.TestCase):
    def setUp(self):
        # Python 3.13 mkdtemp's 0700 ACL can exclude a Windows sandbox token.
        # A normal inherited ACL in this artifact directory works for SQLite.
        self.test_root = Path(__file__).resolve().parents[1] / "work"
        self.temp = self.test_root / (".core-test-" + uuid.uuid4().hex)
        self.temp.mkdir()
        self.addCleanup(self.clean_scratch)
        self.path = self.temp / "coordination.sqlite3"
        self.broker = Broker(self.path)

    def clean_scratch(self):
        self.assertEqual(self.temp.resolve().parent, self.test_root)
        self.assertTrue(self.temp.name.startswith(".core-test-"))
        for child in self.temp.iterdir():
            child.unlink()
        self.temp.rmdir()

    @staticmethod
    def request(actor="codex", op="health", **args):
        if op in {"lead.acquire", "lead.release", "task.submit"}:
            args.setdefault("project", "schematic")
        return {"protocol": PROTOCOL, "request_id": str(uuid.uuid4()),
                "actor": actor, "op": op, "args": args}

    def call(self, actor="codex", op="health", **args):
        return self.broker.handle(self.request(actor, op, **args))

    def success(self, response):
        self.assertTrue(response["ok"], response)
        json.dumps(response, allow_nan=False)
        return response["result"]

    def error(self, response, code):
        self.assertEqual(response["ok"], False, response)
        self.assertEqual(response["error"]["code"], code, response)
        json.dumps(response, allow_nan=False)

    def lead(self, actor="codex", **args):
        return self.success(self.call(actor, "lead.acquire", **args))

    def submit(self, leader, sender="codex", to="claude", **args):
        defaults = {"project": leader["project"], "leader_token": leader["leader_token"],
                    "to": to, "title": "Inspect recognition evidence", "prompt": "Report measured accuracy."}
        defaults.update(args)
        return self.success(self.call(sender, "task.submit", **defaults))

    def test_both_actors_can_control_and_exchange_results(self):
        generation = 0
        for sender, recipient in (("codex", "claude"), ("claude", "codex")):
            leader = self.lead(sender)
            self.assertGreater(leader["generation"], generation)
            generation = leader["generation"]
            task = self.submit(leader, sender, recipient, mode="proposal",
                               allowed_paths=["evidence/共同評量.json"])
            self.assertEqual(task["status"], "pending")
            self.assertEqual(task["sender"], sender)
            self.assertEqual(task["recipient"], recipient)
            self.assertEqual(task["leader_generation"], generation)
            claim = self.success(self.call(recipient, "task.claim", task_id=task["task_id"]))
            completed = self.success(self.call(recipient, "task.complete", task_id=task["task_id"],
                                               claim_token=claim["claim_token"], status="completed",
                                               result={"n": 14, "decision": "人工真值尚未完成"}))
            self.assertEqual(completed["status"], "completed")
            readback = self.success(self.call(sender, "task.get", task_id=task["task_id"]))
            self.assertEqual(readback["result"], completed["result"])
            self.assertNotIn("claim_token", readback)
            self.assertNotIn("leader_token", readback)
            self.success(self.call(sender, "lead.release", leader_token=leader["leader_token"]))

    def test_actor_and_project_capabilities_cannot_be_confused(self):
        leader = self.lead()
        task = self.submit(leader)
        self.error(self.call("claude", "lead.release", leader_token=leader["leader_token"]), "STALE_LEADER")
        self.error(self.call("claude", "task.submit", leader_token=leader["leader_token"],
                             to="codex", title="x", prompt="x"), "STALE_LEADER")
        self.error(self.call("codex", "task.submit", project="other", leader_token=leader["leader_token"],
                             to="claude", title="x", prompt="x"), "STALE_LEADER")
        self.error(self.call("codex", "task.submit", leader_token=leader["leader_token"],
                             to="codex", title="x", prompt="x"), "INVALID_ARGS")
        self.error(self.call("codex", "lead.release", leader_token="非 ASCII 權杖"), "STALE_LEADER")
        self.error(self.call("codex", "task.claim", task_id=task["task_id"]), "NOT_RECIPIENT")
        claim = self.success(self.call("claude", "task.claim", task_id=task["task_id"]))
        self.error(self.call("codex", "task.complete", task_id=task["task_id"],
                             claim_token=claim["claim_token"], status="completed", result={}), "NOT_RECIPIENT")

    def test_leader_expiry_fences_stale_release_and_submission(self):
        with patch("relay_collaboration.bridge_core.time.time", return_value=1000):
            first = self.lead(ttl_seconds=30)
        with patch("relay_collaboration.bridge_core.time.time", return_value=1029):
            self.error(self.call("claude", "lead.acquire"), "LEASE_HELD")
        with patch("relay_collaboration.bridge_core.time.time", return_value=1030):
            second = self.lead("claude")
            self.assertEqual(second["generation"], first["generation"] + 1)
            self.error(self.call("codex", "lead.release", leader_token=first["leader_token"]), "STALE_LEADER")
            self.error(self.call("codex", "task.submit", leader_token=first["leader_token"],
                                 to="claude", title="x", prompt="x"), "STALE_LEADER")
            self.submit(second, sender="claude", to="codex")

    def test_same_actor_reacquisition_also_fences_old_token(self):
        first = self.lead()
        self.success(self.call("codex", "lead.release", leader_token=first["leader_token"]))
        second = self.lead()
        self.assertGreater(second["generation"], first["generation"])
        self.error(self.call("codex", "lead.release", leader_token=first["leader_token"]), "STALE_LEADER")
        self.submit(second)

    def test_expired_claim_rejects_completion_and_can_be_reclaimed(self):
        with patch("relay_collaboration.bridge_core.time.time", return_value=1000):
            task = self.submit(self.lead())
            first = self.success(self.call("claude", "task.claim", task_id=task["task_id"], ttl_seconds=30))
        with patch("relay_collaboration.bridge_core.time.time", return_value=1030):
            self.error(self.call("claude", "task.complete", task_id=task["task_id"],
                                 claim_token=first["claim_token"], status="completed", result={}), "STALE_CLAIM")
            second = self.success(self.call("claude", "task.claim", task_id=task["task_id"]))
            self.assertEqual(second["claim_attempt"], 2)
            self.assertNotEqual(second["claim_token"], first["claim_token"])
            self.error(self.call("claude", "task.complete", task_id=task["task_id"],
                                 claim_token=first["claim_token"], status="completed", result={}), "STALE_CLAIM")
            self.success(self.call("claude", "task.complete", task_id=task["task_id"],
                                   claim_token=second["claim_token"], status="failed", result={"reason": "no truth"}))
            self.error(self.call("claude", "task.claim", task_id=task["task_id"]), "TASK_UNAVAILABLE")

    def race(self, requests):
        barrier = threading.Barrier(len(requests))
        brokers = [Broker(self.path) for _ in requests]

        def invoke(pair):
            broker, request = pair
            barrier.wait(timeout=5)
            return broker.handle(request)

        with ThreadPoolExecutor(max_workers=len(requests)) as pool:
            return list(pool.map(invoke, zip(brokers, requests)))

    def test_concurrent_leaders_have_exactly_one_winner(self):
        responses = self.race([self.request("codex", "lead.acquire"),
                               self.request("claude", "lead.acquire")])
        self.assertEqual(sum(response["ok"] for response in responses), 1)
        self.error(next(response for response in responses if not response["ok"]), "LEASE_HELD")

    def test_concurrent_task_claims_have_exactly_one_winner(self):
        task = self.submit(self.lead())
        responses = self.race([self.request("claude", "task.claim", task_id=task["task_id"])
                               for _ in range(8)])
        self.assertEqual(sum(response["ok"] for response in responses), 1)
        for response in responses:
            if not response["ok"]:
                self.error(response, "TASK_UNAVAILABLE")

    def test_parallel_identical_requests_replay_one_receipt(self):
        leader = self.lead()
        request = self.request("codex", "task.submit", leader_token=leader["leader_token"],
                               to="claude", title="one only", prompt="Inspect")
        responses = self.race([request] * 8)
        self.assertTrue(all(response == responses[0] for response in responses))
        self.assertEqual(len(self.success(self.call(op="task.list"))["tasks"]), 1)

    def test_idempotency_persists_and_rejects_changed_payload_or_actor(self):
        request = self.request("codex", "lead.acquire")
        first = self.broker.handle(request)
        self.assertEqual(Broker(self.path).handle(dict(reversed(list(request.items())))), first)
        altered = dict(request, actor="claude")
        self.error(self.broker.handle(altered), "REQUEST_ID_CONFLICT")
        altered = dict(request, args={"ttl_seconds": 60})
        self.error(self.broker.handle(altered), "REQUEST_ID_CONFLICT")

    def test_replayed_receipts_do_not_renew_expired_leases(self):
        request = self.request("codex", "lead.acquire", ttl_seconds=30)
        with patch("relay_collaboration.bridge_core.time.time", return_value=1000):
            first = self.broker.handle(request)
        with patch("relay_collaboration.bridge_core.time.time", return_value=1100):
            self.assertEqual(self.broker.handle(request), first)
            self.assertEqual(self.lead("claude")["generation"], 2)

    def test_error_receipts_are_stable_and_new_attempt_needs_new_uuid(self):
        leader = self.lead()
        request = self.request("claude", "lead.acquire")
        failed = self.broker.handle(request)
        self.error(failed, "LEASE_HELD")
        self.success(self.call("codex", "lead.release", leader_token=leader["leader_token"]))
        self.assertEqual(self.broker.handle(request), failed)
        self.lead("claude")

    def test_complete_retry_is_idempotent_but_new_stale_completion_rejected(self):
        task = self.submit(self.lead())
        claim = self.success(self.call("claude", "task.claim", task_id=task["task_id"]))
        request = self.request("claude", "task.complete", task_id=task["task_id"],
                               claim_token=claim["claim_token"], status="completed", result={"score": 0.8})
        response = self.broker.handle(request)
        self.success(response)
        self.assertEqual(self.broker.handle(request), response)
        self.error(self.broker.handle(dict(request, request_id=str(uuid.uuid4()))), "STALE_CLAIM")

    def test_task_filters_and_capabilities_are_not_disclosed(self):
        leader = self.lead()
        self.submit(leader, title="first")
        task = self.submit(leader, title="second")
        claim = self.success(self.call("claude", "task.claim", task_id=task["task_id"]))
        response = self.success(self.call("codex", "task.list", to="claude", status="running", limit=1))
        self.assertEqual([item["task_id"] for item in response["tasks"]], [task["task_id"]])
        serialized = json.dumps(response)
        self.assertNotIn(claim["claim_token"], serialized)
        self.assertNotIn(leader["leader_token"], serialized)
        self.assertEqual(self.success(self.call(op="task.list", to="codex"))["tasks"], [])

    def test_invalid_envelopes_and_types_always_return_json_errors(self):
        valid = self.request()
        cases = [None, [], "x", dict(valid, protocol="other"), dict(valid, request_id="bad"),
                 dict(valid, actor="evil"), dict(valid, actor=[]), dict(valid, args=[]),
                 dict(valid, op=[]), dict(valid, extra="x"), {1: "bad"},
                 dict(valid, args={"v": float("nan")}), dict(valid, args={"v": float("inf")}),
                 dict(valid, args={"v": 2**100}), dict(valid, args={"v": (1, 2)}),
                 dict(valid, args={"v": "\ud800"})]
        cyclic = []
        cyclic.append(cyclic)
        cases.append(dict(valid, args={"v": cyclic}))
        for request in cases:
            with self.subTest(request_type=type(request).__name__):
                response = self.broker.handle(request)
                self.assertFalse(response["ok"], response)
                self.assertNotEqual(response["error"]["code"], "INTERNAL_ERROR", response)
                json.dumps(response, allow_nan=False)

    def test_argument_sizes_ranges_and_unknown_fields(self):
        for ttl in (True, 0, 29, 901, 30.0, "30"):
            self.error(self.call(op="lead.acquire", ttl_seconds=ttl), "INVALID_ARGS")
        for limit in (True, 0, 101, "10"):
            self.error(self.call(op="task.list", limit=limit), "INVALID_ARGS")
        self.error(self.call(op="lead.acquire", project=123), "INVALID_ARGS")
        self.error(self.call(op="health", command="danger"), "INVALID_ARGS")
        self.error(self.call(op="shell.exec"), "UNKNOWN_OP")
        self.error(self.call(op="task.get", task_id=str(uuid.uuid4())), "TASK_NOT_FOUND")
        leader = self.lead()
        baseline = {"leader_token": leader["leader_token"], "to": "claude", "title": "title", "prompt": "prompt"}
        for key, value, code in [("title", "界" * 200, "TOO_LARGE"),
                                 ("prompt", "x" * (MAX_PROMPT_BYTES + 1), "TOO_LARGE"),
                                 ("mode", "arbitrary_shell", "INVALID_ARGS"),
                                 ("mode", "implementation", "INVALID_ARGS"),
                                 ("allowed_paths", "C:/", "INVALID_ARGS"),
                                 ("allowed_paths", ["x"] * 129, "INVALID_ARGS"),
                                 ("allowed_paths", [None], "INVALID_ARGS")]:
            self.error(self.call(op="task.submit", **dict(baseline, **{key: value})), code)
        task = self.submit(leader)
        claim = self.success(self.call("claude", "task.claim", task_id=task["task_id"]))
        complete = {"task_id": task["task_id"], "claim_token": claim["claim_token"], "status": "completed"}
        self.error(self.call("claude", "task.complete", **complete, result=[]), "INVALID_ARGS")
        self.error(self.call("claude", "task.complete", **complete,
                             result={"x": "y" * MAX_RESULT_BYTES}), "TOO_LARGE")
        self.assertEqual(self.success(self.call(op="task.get", task_id=task["task_id"]))["status"], "running")

    def test_user_strings_are_stored_as_values_not_sql(self):
        text = "'); DROP TABLE tasks; --"
        leader = self.lead(project=text)
        task = self.submit(leader, title=text, prompt=text)
        self.assertEqual(self.success(self.call(op="task.get", task_id=task["task_id"]))["prompt"], text)
        self.assertEqual(len(self.success(self.call(op="task.list"))["tasks"]), 1)

    def test_failed_storage_is_sanitized_without_input_or_path(self):
        with patch.object(self.broker, "_connect", side_effect=sqlite3.OperationalError("SECRET local path")):
            response = self.call()
        self.error(response, "STORAGE_ERROR")
        self.assertNotIn("SECRET", json.dumps(response))

    def test_project_filtered_pagination_reaches_more_than_100_tasks(self):
        expected = []
        with patch("relay_collaboration.bridge_core.time.time", return_value=1000) as now:
            leader = self.lead(ttl_seconds=900)
            for i in range(205):
                now.return_value = 1000 + i
                expected.append(self.submit(leader))
            other = self.lead(project="other", ttl_seconds=900)
            for i in range(105):
                now.return_value = 1300 + i
                self.submit(other)
        found, cursor, sizes = [], None, []
        while True:
            page = self.success(self.call(op="task.list", project="schematic", to="claude",
                                          status="pending", limit=100, cursor=cursor))
            found.extend(page["tasks"])
            sizes.append(len(page["tasks"]))
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(sizes, [100, 100, 5])
        self.assertEqual([x["task_id"] for x in found],
                         [x["task_id"] for x in sorted(expected, key=lambda x: (-x["created_at"], x["task_id"]))])
        self.assertEqual(len({x["task_id"] for x in found}), 205)
        # Existing callers still receive tasks; without a project filter all projects are visible.
        unfiltered = self.success(self.call(op="task.list", limit=100))
        self.assertTrue(all(x["project"] == "other" for x in unfiltered["tasks"]))
        self.assertIsNotNone(unfiltered["next_cursor"])

    def test_cursor_ties_use_task_uuid_ascending_without_duplicates(self):
        with patch("relay_collaboration.bridge_core.time.time", return_value=1000):
            leader = self.lead()
            expected = sorted(self.submit(leader)["task_id"] for _ in range(7))
        found, cursor = [], None
        while True:
            page = self.success(self.call(op="task.list", project="schematic", limit=3, cursor=cursor))
            found.extend(x["task_id"] for x in page["tasks"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(found, expected)

    def test_cursor_survives_tasks_leaving_pending_filter(self):
        expected = []
        with patch("relay_collaboration.bridge_core.time.time", return_value=1000) as now:
            leader = self.lead()
            for i in range(5):
                now.return_value = 1000 + i
                expected.append(self.submit(leader)["task_id"])
        first = self.success(self.call(op="task.list", project="schematic", status="pending", limit=2))
        for task in first["tasks"]:
            claim = self.success(self.call("claude", "task.claim", task_id=task["task_id"]))
            self.success(self.call("claude", "task.complete", task_id=task["task_id"],
                                   claim_token=claim["claim_token"], status="completed", result={"ok": True}))
        rest = self.success(self.call(op="task.list", project="schematic", status="pending", limit=100,
                                      cursor=first["next_cursor"]))
        self.assertEqual([x["task_id"] for x in first["tasks"] + rest["tasks"]], expected[::-1])
        self.assertIsNone(rest["next_cursor"])

    def test_project_and_cursor_validate_exact_types_and_fields(self):
        task_id = str(uuid.uuid4())
        for cursor in [False, 1, "bad", [], {}, {"created_at": 1},
                       {"created_at": True, "task_id": task_id},
                       {"created_at": "1", "task_id": task_id},
                       {"created_at": {}, "task_id": task_id},
                       {"created_at": 1, "task_id": "bad"},
                       {"created_at": 1, "task_id": task_id, "extra": 0}]:
            with self.subTest(cursor=cursor):
                self.error(self.call(op="task.list", cursor=cursor), "INVALID_ARGS")
        for project in [False, 1, [], "", "\x00"]:
            self.error(self.call(op="task.list", project=project), "INVALID_ARGS")
        for timestamp in [float("nan"), float("inf"), float("-inf")]:
            self.error(self.call(op="task.list", cursor={"created_at": timestamp, "task_id": task_id}),
                       "INVALID_REQUEST")
        empty = self.success(self.call(op="task.list", project="absent", cursor=None))
        self.assertEqual(empty, {"tasks": [], "next_cursor": None})
        wide_timestamp = self.success(self.call(op="task.list", cursor={"created_at": 2**63, "task_id": task_id}))
        self.assertEqual(wide_timestamp, {"tasks": [], "next_cursor": None})

    def test_exact_page_limit_has_no_spurious_next_cursor(self):
        leader = self.lead()
        self.submit(leader)
        self.submit(leader)
        page = self.success(self.call(op="task.list", limit=2, cursor=None))
        self.assertEqual(len(page["tasks"]), 2)
        self.assertIsNone(page["next_cursor"])


if __name__ == "__main__":
    unittest.main()
