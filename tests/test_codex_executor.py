"""Codex protocol/subprocess fixtures; no account or real model calls."""
import json
import os
from pathlib import Path
import shutil
import sys
import unittest
import uuid
from unittest.mock import patch

from relay_collaboration.codex_executor import CodexCLIExecutor, parse_codex_output
from relay_collaboration.cli_executor import ProtocolError, ExecutorConfigurationError
from tests.test_cli_executor import context

FIXTURE = r'''
import json, sys, time
mode, args = sys.argv[1], sys.argv[2:]
if args == ['--version']:
    print('codex-cli 0.120.0')
elif args == ['exec', '--help']:
    print('--json --sandbox --skip-git-repo-check --ephemeral --ignore-user-config --ignore-rules --config --cd')
elif args[-2:] == ['login', 'status']:
    print('Not logged in' if mode == 'unauth' else 'Logged in using ChatGPT', file=sys.stderr)
    sys.exit(1 if mode == 'unauth' else 0)
elif args[-2:] == ['features', 'list']:
    for item in args:
        if item.startswith('features.'):
            name, value = item.removeprefix('features.').split('=', 1)
            print(name, 'stable', 'true' if mode == 'unsafe' and name == 'shell_tool' else value)
else:
    prompt = sys.stdin.read()
    if mode == 'timeout': time.sleep(20)
    print(json.dumps({'type':'thread.started','thread_id':'fixture'}))
    print(json.dumps({'type':'turn.started', **({'model':'wrong'} if mode == 'mismatch' else {})}))
    if mode == 'tool':
        print(json.dumps({'type':'item.completed','item':{'type':'command_execution','command':'never executed fixture'}}))
    print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':'fixture reply: '+prompt}}))
    print(json.dumps({'type':'turn.completed','usage':{'input_tokens':4,'output_tokens':8}}))
    if mode == 'duplicate': print(json.dumps({'type':'turn.completed'}))
'''


class CodexExecutorTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / 'work' / ('codex-fixture-' + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.run, self.auth = self.root / 'run', self.root / 'auth'
        self.run.mkdir(); self.auth.mkdir()
        self.fixture = self.root / 'fixture.py'
        self.fixture.write_text(FIXTURE, encoding='utf-8')

    def executor(self, mode='success', **extra):
        return CodexCLIExecutor([sys.executable, str(self.fixture), mode], runtime_dir=self.run,
                                config_dir=self.auth, fixture_mode=True, **extra)

    def test_success_does_not_invent_unreported_model_or_effort(self):
        exe = self.executor(model='example-model', effort='high')
        self.assertTrue(exe.preflight()['auth_ready'])
        result = exe({'prompt': '中文內容', 'mode': 'proposal'}, context())
        self.assertTrue(result['ok'])
        self.assertEqual(result['text'], 'fixture reply: 中文內容')
        self.assertEqual(result['execution_kind'], 'fixture_cli')
        self.assertFalse(result['model_execution_verified'])
        self.assertFalse(result['cli_execution_verified'])
        self.assertIsNone(result['reported_model'])
        self.assertEqual(result['effort_verification'], 'not_reported')

    def test_auth_blocks_before_execution(self):
        exe = self.executor('unauth')
        self.assertFalse(exe.preflight()['auth_ready'])
        with patch.object(exe, '_invoke', side_effect=AssertionError('must not execute')):
            self.assertEqual(exe({'prompt': 'x', 'mode': 'proposal'}, context())['error_kind'], 'preflight_required')

    def test_capability_readback_blocks_unsafe_cli(self):
        exe = self.executor('unsafe')
        self.assertEqual(exe.preflight()['error_kind'], 'capability_gates_unverified')
        self.assertFalse(exe({'prompt':'x', 'mode':'proposal'}, context())['ok'])

    def test_failed_duplicate_and_tool_events_rejected(self):
        for mode in ('tool', 'duplicate', 'mismatch'):
            with self.subTest(mode=mode):
                exe = self.executor(mode, model='expected')
                self.assertTrue(exe.preflight()['ok'])
                self.assertFalse(exe({'prompt':'text', 'mode':'proposal'}, context())['ok'])
        for events in ([{'type':'turn.completed'}], [{'type':'turn.failed'}],
                       [{'type':'turn.started'}, {'type':'error'}, {'type':'turn.completed'}]):
            with self.assertRaises(ProtocolError):
                parse_codex_output('\n'.join(json.dumps(e) for e in events))

    def test_timeout_and_implementation_rejection(self):
        exe = self.executor('timeout'); exe.preflight()
        self.assertEqual(exe({'prompt':'text', 'mode':'proposal'}, context(.2))['error_kind'], 'timeout')
        with patch.object(exe, '_invoke', side_effect=AssertionError('must not execute')):
            self.assertEqual(exe({'prompt':'text', 'mode':'implementation'}, context())['error_kind'], 'unsupported_task')

    def test_command_and_environment_are_fixed_not_prompt_controlled(self):
        exe = self.executor()
        command = exe.build_command()
        self.assertIn('read-only', command)
        for flag in ('--ignore-user-config', '--ignore-rules', '--ephemeral', '--skip-git-repo-check'):
            self.assertIn(flag, command)
        self.assertIn('features.shell_tool=false', command)
        self.assertIn('features.hooks=false', command)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', command)
        with patch.dict(os.environ, {'CODEX_HOME': 'parent', 'OPENAI_API_KEY': 'never-inherit',
                                     'CODEX_THREAD_ID':'parent-thread', 'ANTHROPIC_API_KEY':'secret'}):
            env = exe.environment()
        self.assertEqual(env['CODEX_HOME'], str(self.auth))
        for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'CODEX_THREAD_ID', 'CLAUDE_CONFIG_DIR'):
            self.assertNotIn(key, env)
        with self.assertRaises(ExecutorConfigurationError):
            CodexCLIExecutor(['shell.cmd'], runtime_dir=self.run, config_dir=self.auth)
