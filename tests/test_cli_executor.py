"""Fixture CLI subprocess tests: never authenticate or call a real model."""
import json
import os
from pathlib import Path
import subprocess
import sys
import shutil
import threading
import time
from types import SimpleNamespace
import unittest
import uuid
from unittest.mock import patch

from relay_collaboration.cli_executor import (ClaudeCLIExecutor, ExecutorConfigurationError,
                          ProtocolError, parse_claude_output, run_bounded, strict_json)


FIXTURE = r'''
import json, pathlib, subprocess, sys, time
mode, args = sys.argv[1], sys.argv[2:]
if args == ["--version"]:
    print("2.1.236 (Claude Code fixture)")
elif args == ["--help"]:
    flags = json.loads(pathlib.Path(__file__).with_suffix(".flags").read_text())
    print(" ".join(flags[:-1] if mode == "missing_flag" else flags))
elif args == ["auth", "status", "--json"]:
    if mode == "malformed_auth":
        print("not JSON")
    else:
        print(json.dumps({"loggedIn": mode != "unauthenticated", "authMethod": "claude.ai",
                          "email": "private@example.invalid", "access_token": "fixture-secret"}))
    sys.exit(1 if mode == "unauthenticated" else 0)
else:
    prompt = sys.stdin.read()
    if mode == "timeout":
        time.sleep(20)
    if mode == "overflow":
        print("x" * 500000)
        sys.exit(0)
    if mode == "malformed":
        print("surprise non-json stdout")
        sys.exit(0)
    if mode == "auth_error":
        print(json.dumps({"type":"result","subtype":"error_during_execution",
                          "is_error":True,"result":"Not logged in"}))
        sys.exit(1)
    model = "claude-opus-5-5" if mode == "opus55" else "claude-opus-4-8" if mode != "other_model" else "claude-sonnet-5"
    init = {"type":"system","subtype":"init","tools":[],"mcp_servers":[],"plugins":[],
            "model": model, "effort":"max"}
    if mode == "context_suffix":
        init["model"] += "[1m]"
    if mode == "tools":
        init["tools"] = ["Bash"]
    print(json.dumps(init))
    print(json.dumps({"type":"assistant","message":{"model":model,
                                                      "content":[{"type":"text","text":"fixture"}]}}))
    result = {"type":"result","subtype":"success","is_error":False,
              "result":"fixture response: " + prompt,"modelUsage":{model:{"inputTokens":3}},
              "usage":{"input_tokens":3,"output_tokens":5},"total_cost_usd":0.0}
    if mode == "is_error":
        result["is_error"] = True
    print(json.dumps(result))
    if mode == "duplicate":
        print(json.dumps(result))
    sys.exit(7 if mode == "nonzero" else 0)
'''


def context(seconds=8, event=None):
    return SimpleNamespace(timeout_seconds=seconds, deadline_epoch=time.time() + seconds,
                           stop_event=event or threading.Event(), attempt_id="fixture-attempt",
                           project_root="never_used_as_cwd", allowed_paths=[], evidence_dir=None)


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        # Python 3.13 mkdtemp(0700) creates an ACL inaccessible to this Windows
        # restricted token. Ordinary mkdir uses the approved parent ACL.
        self.root = Path(__file__).resolve().parents[1] / "work" / ("executor-test-" + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.runtime = self.root / "runtime"
        self.config = self.root / "config"
        self.runtime.mkdir()
        self.config.mkdir()
        self.fixture = self.root / "fixture.py"
        self.fixture.write_text(FIXTURE, encoding="utf-8")
        self.fixture.with_suffix(".flags").write_text(json.dumps([
            *ClaudeCLIExecutor.REQUIRED_FLAGS, "--model", "--effort"]), encoding="utf-8")

    def executor(self, mode="success", **overrides):
        values = dict(runtime_dir=self.runtime, config_dir=self.config,
                      model="claude-opus-4-8", effort="max", fixture_mode=True)
        values.update(overrides)
        return ClaudeCLIExecutor([sys.executable, "-B", "-X", "utf8", str(self.fixture), mode], **values)

    def run_task(self, mode="success", **overrides):
        executor = self.executor(mode, **overrides)
        self.assertTrue(executor.preflight()["ok"])
        return executor({"task_id":"fixture", "mode":"proposal", "prompt":"sample context"}, context())

    def test_success_preserves_model_effort_but_labels_fixture(self):
        result = self.run_task()
        self.assertTrue(result["ok"])
        self.assertEqual(result["reported_model"], "claude-opus-4-8")
        self.assertEqual(result["requested_effort"], "max")
        self.assertEqual(result["execution_kind"], "fixture_cli")
        self.assertFalse(result["model_execution_verified"])
        self.assertIn("sample context", result["text"])
        self.assertNotIn("stdout", result)
        self.assertNotIn("stderr", result)

    def test_context_suffix_is_normalized_only_for_identity_comparison(self):
        # Parent live smoke: init claude-opus-5[1m], usage claude-opus-5.
        result = self.run_task("context_suffix")
        self.assertTrue(result["ok"])
        self.assertEqual(result["reported_models"], ["claude-opus-4-8[1m]", "claude-opus-4-8"])
        result = self.run_task("context_suffix", model="claude-opus-4-8[1m]")
        self.assertTrue(result["ok"])

    def test_opus55_exact_model_and_alias_acceptance_rejects_other_version(self):
        for selected in ('claude-opus-5-5', 'opus'):
            result = self.run_task('opus55', model=selected)
            self.assertTrue(result['ok'])
            self.assertEqual(result['reported_model'], 'claude-opus-5-5')
            self.assertFalse(result['model_execution_verified'])
        result = self.run_task(model='claude-opus-5-5')
        self.assertEqual(result['error_kind'], 'reported_model_mismatch')

    def test_account_default_is_explicit_null_and_actual_model_recorded(self):
        executor = self.executor(model=None, effort=None)
        self.assertNotIn("--model", executor.build_command())
        self.assertNotIn("--effort", executor.build_command())
        self.assertTrue(executor.preflight()["ok"])
        result = executor({"mode":"read_only", "prompt":"context"}, context())
        self.assertTrue(result["ok"])
        self.assertIsNone(result["requested_model"])
        self.assertEqual(result["model_selection"], "account_default")
        self.assertEqual(result["reported_model"], "claude-opus-4-8")

    def test_task_cannot_override_configuration_or_cwd(self):
        executor = self.executor()
        self.assertTrue(executor.preflight()["ok"])
        result = executor({"mode":"proposal", "prompt":"context", "model":"cheap",
                           "effort":"low", "cwd":"C:\\forbidden", "command":"git status"}, context())
        self.assertTrue(result["ok"])
        self.assertEqual(result["requested_model"], "claude-opus-4-8")
        self.assertEqual(executor.runtime_dir, self.runtime)

    def test_preflight_is_required_and_auth_output_is_sanitized(self):
        executor = self.executor()
        self.assertEqual(executor({"mode":"proposal", "prompt":"context"}, context())["error_kind"],
                         "preflight_required")
        result = executor.preflight()
        serialized = json.dumps(result)
        self.assertNotIn("private@example", serialized)
        self.assertNotIn("fixture-secret", serialized)

    def test_auth_and_missing_flags_fail_before_model(self):
        for mode in ("unauthenticated", "malformed_auth", "missing_flag"):
            with self.subTest(mode=mode):
                result = self.executor(mode).preflight()
                self.assertFalse(result["ok"])
                self.assertFalse(result["auth_ready"])

    def test_success_shaped_nonzero_exit_is_failure(self):
        result = self.run_task("nonzero")
        self.assertFalse(result["ok"])
        self.assertEqual(result["exit_code"], 7)
        self.assertNotIn("text", result)

    def test_is_error_true_auth_and_changed_model_fail(self):
        for mode, expected in (("is_error", "cli_failed"), ("auth_error", "authentication_failed"),
                               ("other_model", "reported_model_mismatch")):
            with self.subTest(mode=mode):
                result = self.run_task(mode)
                self.assertFalse(result["ok"])
                self.assertEqual(result["error_kind"], expected)

    def test_bad_output_duplicate_results_and_tools_fail_closed(self):
        for mode in ("malformed", "duplicate", "tools"):
            with self.subTest(mode=mode):
                self.assertEqual(self.run_task(mode)["error_kind"], "invalid_cli_output")

    def test_implementation_and_excessive_prompt_are_rejected(self):
        executor = self.executor()
        self.assertEqual(executor({"mode":"implementation", "prompt":"edit source"}, context())["error_kind"],
                         "unsupported_task")
        self.assertEqual(executor({"mode":"proposal", "prompt":"x" * 2_000_001}, context())["error_kind"],
                         "prompt_too_large")

    def test_failed_capture_is_retained_in_controller_evidence_directory(self):
        executor = self.executor('malformed')
        self.assertTrue(executor.preflight()['ok'])
        ctx = context()
        ctx.evidence_dir = self.runtime
        result = executor({'mode':'proposal', 'prompt':'sample'}, ctx)
        self.assertEqual(result['error_kind'], 'invalid_cli_output')
        self.assertEqual(result['parse_error'], 'JSONDecodeError')
        path = Path(result['diagnostic_file'])
        self.assertEqual(path.parent, self.runtime)
        self.assertEqual(path.read_bytes(), b'surprise non-json stdout\r\n' if os.name == 'nt' else b'surprise non-json stdout\n')
        # Public result carries no raw model output or stderr.
        self.assertNotIn('stdout', result)
        self.assertNotIn('stderr', result)

    def test_expired_deadline_and_cancelled_event_do_not_execute(self):
        executor = self.executor()
        self.assertTrue(executor.preflight()["ok"])
        task = {"mode":"proposal", "prompt":"context"}
        self.assertEqual(executor(task, context(-1))["error_kind"], "deadline_expired")
        stop = threading.Event()
        stop.set()
        self.assertEqual(executor(task, context(event=stop))["error_kind"], "cancelled")

    def test_timeout_and_output_cap_terminate_subprocess(self):
        executor = self.executor("timeout")
        self.assertTrue(executor.preflight()["ok"])
        started = time.monotonic()
        result = executor({"mode":"proposal", "prompt":"context"}, context(.25))
        self.assertEqual(result["error_kind"], "timeout")
        self.assertLess(time.monotonic() - started, 4)
        result = self.run_task("overflow", max_output_bytes=2048)
        self.assertEqual(result["error_kind"], "output_limit")
        self.assertLessEqual(result["stdout_bytes"], 2048)

    def test_cancellation_is_observed_while_running(self):
        executor = self.executor("timeout")
        self.assertTrue(executor.preflight()["ok"])
        stop = threading.Event()
        timer = threading.Timer(.25, stop.set)
        timer.start()
        self.addCleanup(timer.cancel)
        started = time.monotonic()
        result = executor({"mode":"proposal", "prompt":"context"}, context(event=stop))
        self.assertEqual(result["error_kind"], "cancelled")
        self.assertLess(time.monotonic() - started, 4)

    def test_git_ancestor_is_rejected_without_running_git(self):
        (self.root / ".git").write_text("fixture pointer; do not read", encoding="utf-8")
        result = self.executor().preflight()
        self.assertEqual(result["error_kind"], "runtime_isolation_failed")

    def test_configured_environment_is_not_inherited_auth_or_model(self):
        executor = self.executor()
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY":"do-not-inherit", "CLAUDE_CODE_OAUTH_TOKEN":"private",
                                     "ANTHROPIC_MODEL":"other", "NODE_OPTIONS":"malicious preload"}):
            env = executor.environment()
        self.assertNotIn("ANTHROPIC_API_KEY", env)
        self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", env)
        self.assertNotIn("ANTHROPIC_MODEL", env)
        self.assertNotIn("NODE_OPTIONS", env)
        self.assertEqual(env["CLAUDE_CONFIG_DIR"], str(self.config))
        self.assertEqual(env["CLAUDE_CODE_DISABLE_ATTACHMENTS"], "1")

    def test_shell_wrappers_and_unmarked_fixtures_are_rejected(self):
        with self.assertRaises(ExecutorConfigurationError):
            ClaudeCLIExecutor([str(self.root / "claude.cmd")], runtime_dir=self.runtime, config_dir=self.config)
        with self.assertRaises(ExecutorConfigurationError):
            self.executor(fixture_mode=False)

    def test_process_tree_child_cannot_write_after_timeout(self):
        marker = self.root / "descendant_was_not_terminated.txt"
        child = self.root / "child.py"
        child.write_text("import time\nfrom pathlib import Path\ntime.sleep(1.2)\nPath(" + repr(str(marker)) +
                         ").write_text('unexpected')\n", encoding="utf-8")
        parent = self.root / "parent.py"
        parent.write_text("import subprocess,sys,time\nsubprocess.Popen([sys.executable," + repr(str(child)) +
                          "])\nprint('child-started',flush=True)\ntime.sleep(20)\n", encoding="utf-8")
        capture = run_bounded([sys.executable, str(parent)], cwd=self.runtime,
                              env=self.executor().environment(), timeout=.4)
        self.assertEqual(capture.reason, "timeout")
        self.assertIn(b"child-started", capture.stdout)
        time.sleep(1.3)
        self.assertFalse(marker.exists())


class ParserTests(unittest.TestCase):
    def test_builtin_agents_md_metadata_only_is_accepted(self):
        result = {"type":"result", "subtype":"success", "is_error":False,
                  "result":"answer", "modelUsage":{"claude-fable-5-1":{}}}
        builtin = {"name":"agents-md", "path":"builtin", "source":"agents-md@builtin"}
        init = {"type":"system", "subtype":"init", "tools":[],
                "mcp_servers":[], "plugins":[builtin]}
        self.assertEqual(parse_claude_output(json.dumps([init, result]))['text'], 'answer')
        for plugins in ([{**builtin, 'path':'/external'}],
                        [{**builtin, 'source':'other'}],
                        [{**builtin, 'name':'other'}],
                        [builtin, builtin], [builtin, {'name':'other'}]):
            with self.subTest(plugins=plugins), self.assertRaises(ProtocolError):
                parse_claude_output(json.dumps([{**init, 'plugins':plugins}, result]))
        for event in ({**init, 'tools':['Bash']}, {**init, 'mcp_servers':[{'name':'server'}]},
                      {'type':'system','subtype':'hook_started'},
                      {'type':'assistant','message':{'content':[{'type':'tool_use','name':'Read'}]}}):
            with self.subTest(event=event), self.assertRaises(ProtocolError):
                parse_claude_output(json.dumps([init, event, result]))

    def test_strict_json_rejects_duplicate_keys_and_nan(self):
        for text in ('{"a":1,"a":2}', '{"a":NaN}'):
            with self.assertRaises(ValueError):
                strict_json(text)

    def test_structured_json_result_accepted_without_fabricating_effort(self):
        value = {"type":"result", "subtype":"success", "is_error":False,
                 "result":"answer", "modelUsage":{"claude-opus-4-8":{}}}
        parsed = parse_claude_output(json.dumps(value))
        self.assertEqual(parsed["reported_models"], ["claude-opus-4-8"])
        self.assertEqual(parsed["reported_efforts"], [])

    def test_result_terminal_and_boolean_contract(self):
        result = {"type":"result", "subtype":"success", "is_error":False, "result":"answer"}
        invalid = [json.dumps(result) + '\n{"type":"system"}',
                   json.dumps({**result, "is_error":0}), json.dumps([result, result]),
                   json.dumps({**result, "model":["not-a-model"]})]
        for text in invalid:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_claude_output(text)

    def test_assistant_tools_and_hooks_rejected(self):
        result = {"type":"result", "subtype":"success", "is_error":False, "result":"answer"}
        for event in ({"type":"assistant", "message":{"content":[{"type":"tool_use", "name":"Read"}]}},
                      {"type":"system", "subtype":"hook_started"}):
            with self.assertRaises(ProtocolError):
                parse_claude_output(json.dumps([event, result]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
