"""Bounded Codex exec adapter with isolated login and proposal-only policy.

JSONL contract: https://learn.chatgpt.com/docs/non-interactive-mode
CLI output may omit model/effort; never infer them from the requested values.
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import re
import time

from .cli_executor import (ClaudeCLIExecutor, ExecutorConfigurationError,
                           ProtocolError, strict_json)


def parse_codex_output(stdout):
    events = [strict_json(line) for line in stdout.splitlines() if line.strip()]
    if not events or any(not isinstance(event, dict) for event in events):
        raise ProtocolError('invalid_event_shape')
    if (sum(event.get('type') == 'turn.completed' for event in events) != 1
            or events[-1].get('type') != 'turn.completed'):
        raise ProtocolError('missing_duplicate_or_nonterminal_result')
    messages, models, efforts = [], [], []
    started = 0
    for event in events:
        kind = event.get('type')
        if kind not in {'thread.started', 'turn.started', 'turn.completed',
                        'item.started', 'item.updated', 'item.completed'}:
            raise ProtocolError('unexpected_or_failed_event')
        if kind == 'turn.started':
            started += 1
        if kind.startswith('item.'):
            item = event.get('item')
            if not isinstance(item, dict) or item.get('type') not in {'agent_message', 'reasoning'}:
                raise ProtocolError('tool_event_in_proposal_profile')
            if kind == 'item.completed' and item['type'] == 'agent_message':
                if not isinstance(item.get('text'), str):
                    raise ProtocolError('invalid_message_text')
                messages.append(item['text'])
        for key, values in (('model', models), ('effort', efforts)):
            if event.get(key) is not None:
                if not isinstance(event[key], str) or not event[key]:
                    raise ProtocolError('invalid_reported_metadata')
                values.append(event[key])
    if started != 1 or not messages or not messages[-1].strip():
        raise ProtocolError('empty_or_multiple_turns')
    return {'text': messages[-1], 'models': list(dict.fromkeys(models)),
            'efforts': list(dict.fromkeys(efforts)), 'usage': events[-1].get('usage')}


class CodexCLIExecutor(ClaudeCLIExecutor):
    REQUIRED_FLAGS = ('--json', '--sandbox', '--skip-git-repo-check', '--ephemeral',
                      '--ignore-user-config', '--ignore-rules', '--config', '--cd')
    SETTINGS = {
        'approval_policy': 'never', 'web_search': 'disabled', 'project_doc_max_bytes': 0,
        'features.shell_tool': False, 'features.unified_exec': False,
        'features.multi_agent': False, 'features.hooks': False,
        'features.memories': False, 'features.apps': False,
        'features.plugins': False, 'features.js_repl': False,
        'features.apply_patch_freeform': False,
        'features.browser_use': False, 'features.computer_use': False,
        'features.in_app_browser': False, 'features.image_generation': False,
        'features.view_image': False, 'features.workspace_dependencies': False,
        'features.goals': False, 'features.skill_search': False,
        'features.skill_mcp_dependency_install': False,
        'features.skip_host_skill_discovery': True,
        'mcp_servers': {}, 'developer_instructions': ClaudeCLIExecutor.SYSTEM_PROMPT,
        'cli_auth_credentials_store': 'file',
    }
    # unified_exec may be pinned on by a CLI build; shell_tool is the gate that
    # removes shell/exec tools. Require concrete readback of capability gates.
    REQUIRED_DISABLED = ('shell_tool', 'hooks', 'multi_agent', 'apps', 'plugins',
                         'browser_use', 'computer_use', 'in_app_browser',
                         'image_generation', 'view_image', 'workspace_dependencies',
                         'goals', 'skill_search', 'skill_mcp_dependency_install')

    def __init__(self, command, *, fixture_mode=False, effort=None, **kwargs):
        if (not fixture_mode and (not isinstance(command, (list, tuple)) or len(command) != 1
                or Path(command[0]).name.lower() not in {'codex', 'codex.exe'})):
            raise ExecutorConfigurationError('Production Codex requires one native executable')
        if effort not in {None, 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'}:
            raise ExecutorConfigurationError('Unsupported Codex effort')
        super().__init__(command, fixture_mode=True, effort=None, **kwargs)
        self.fixture_mode, self.effort = fixture_mode, effort

    def environment(self):
        env = super().environment()
        env = {k: v for k, v in env.items() if not k.startswith('CLAUDE_')}
        env['CODEX_HOME'] = str(self.config_dir)
        # Parent thread IDs, API credentials and desktop channels are not inherited.
        return env

    def preflight(self):
        self._preflight = None
        result = {'ok': False, 'available': False, 'auth_ready': False, 'provider': 'codex',
                  'execution_kind': 'fixture_cli' if self.fixture_mode else 'real_cli'}
        try:
            self._validate_runtime()
            version = self._invoke(['--version'])
            if version.reason or version.exit_code != 0:
                return {**result, 'error_kind': version.reason or 'version_failed'}
            match = re.search(r'\b\d+\.\d+\.\d+(?:[-+][\w.-]+)?\b', version.stdout.decode('utf-8'))
            if not match:
                return {**result, 'error_kind': 'version_unrecognized'}
            result.update(available=True, cli_version=match.group(0))
            help_result = self._invoke(['exec', '--help'])
            help_text = help_result.stdout.decode('utf-8')
            missing = [flag for flag in self.REQUIRED_FLAGS if not re.search(re.escape(flag) + r'(?:[\s,=<]|$)', help_text)]
            if help_result.reason or help_result.exit_code != 0 or missing:
                return {**result, 'error_kind': 'required_cli_flags_unverified', 'missing_flags': missing}
            features = self._invoke([*self.config_overrides(), 'features', 'list'])
            reported = {}
            for line in features.stdout.decode('utf-8').splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[-1] in ('true', 'false'):
                    reported[parts[0]] = parts[-1] == 'true'
            if (features.reason or features.exit_code != 0 or
                    any(reported.get(name) is not False for name in self.REQUIRED_DISABLED) or
                    reported.get('skip_host_skill_discovery') is not True):
                return {**result, 'error_kind': 'capability_gates_unverified'}
            result['capability_gates_verified'] = True
            auth = self._invoke(['-c', 'cli_auth_credentials_store="file"', 'login', 'status'])
            ready = (auth.reason is None and auth.exit_code == 0 and
                     'logged in using chatgpt' in (auth.stdout + auth.stderr).decode('utf-8').lower())
            result.update(ok=ready, auth_ready=ready,
                          auth_evidence='codex_login_status_chatgpt' if ready else 'codex_login_not_ready')
            self._preflight = result
            return dict(result)
        except (OSError, ValueError, UnicodeError):
            return {**result, 'error_kind': 'provider_preflight_failed'}

    def config_overrides(self):
        args = []
        for key, value in self.SETTINGS.items():
            # Empty table syntax is valid TOML; other values are JSON/TOML scalars.
            args.extend(['-c', key + '=' + ('{}' if value == {} else json.dumps(value, ensure_ascii=False))])
        return args

    def build_command(self):
        args = ['exec', '--json', '--sandbox', 'read-only', '--skip-git-repo-check',
                '--ephemeral', '--ignore-user-config', '--ignore-rules', '--cd', str(self.runtime_dir),
                *self.config_overrides()]
        if self.model is not None:
            args.extend(['--model', self.model])
        if self.effort is not None:
            args.extend(['-c', 'model_reasoning_effort=' + json.dumps(self.effort)])
        return [*args, '-']

    def __call__(self, task, context):
        result = {'ok': False, 'status': 'failed', 'provider': 'codex',
                  'execution_kind': 'fixture_cli' if self.fixture_mode else 'real_cli',
                  'model_execution_verified': False, 'cli_execution_verified': False,
                  'profile': 'read_only_proposal', 'requested_model': self.model,
                  'requested_effort': self.effort, 'reported_model': None, 'reported_effort': None,
                  'model_selection': 'explicit' if self.model else 'cli_default',
                  'model_evidence': 'not_reported', 'effort_verification': 'not_reported',
                  'cli_version': (self._preflight or {}).get('cli_version')}
        prompt = task.get('prompt')
        if task.get('mode') not in {'read_only', 'proposal'} or not isinstance(prompt, str) or not prompt.strip():
            return {**result, 'error_kind': 'unsupported_task'}
        if len(prompt.encode('utf-8')) > 2_000_000:
            return {**result, 'error_kind': 'prompt_too_large'}
        if not (self._preflight or {}).get('auth_ready'):
            return {**result, 'error_kind': 'preflight_required'}
        try:
            self._validate_runtime()
            timeout = min(float(context.timeout_seconds), float(context.deadline_epoch) - time.time())
            if not math.isfinite(timeout) or timeout <= 0:
                return {**result, 'error_kind': 'deadline_expired'}
            capture = self._invoke(self.build_command(), stdin=prompt.encode('utf-8'),
                                   timeout=timeout, stop_event=context.stop_event)
        except (OSError, ValueError, AttributeError):
            return {**result, 'error_kind': 'invalid_execution_context_or_runtime'}
        result.update(exit_code=capture.exit_code, duration_seconds=capture.duration_seconds,
                      stdout_sha256=hashlib.sha256(capture.stdout).hexdigest(),
                      stderr_sha256=hashlib.sha256(capture.stderr).hexdigest(),
                      stdout_bytes=len(capture.stdout), stderr_bytes=len(capture.stderr))
        if capture.reason or capture.exit_code != 0:
            return {**result, 'error_kind': capture.reason or 'cli_failed'}
        try:
            parsed = parse_codex_output(capture.stdout.decode('utf-8'))
        except (ValueError, UnicodeError, TypeError, AttributeError):
            return {**result, 'error_kind': 'invalid_cli_output'}
        models, efforts = parsed['models'], parsed['efforts']
        result.update(reported_model=models[-1] if models else None, reported_models=models,
                      reported_effort=efforts[-1] if efforts else None)
        if self.model and any(model != self.model for model in models):
            return {**result, 'error_kind': 'reported_model_mismatch'}
        if self.effort and any(effort != self.effort for effort in efforts):
            return {**result, 'error_kind': 'reported_effort_mismatch'}
        return {**result, 'ok': True, 'status': 'completed', 'text': parsed['text'], 'usage': parsed['usage'],
                'cli_execution_verified': not self.fixture_mode,
                'model_execution_verified': bool(models) and not self.fixture_mode,
                'model_evidence': 'cli_reported_not_independently_attested' if models else 'not_reported',
                'effort_verification': 'reported' if efforts else 'not_reported'}
