"""Versioned, strictly validated local configuration and fresh private runtime."""
from __future__ import annotations
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import uuid
from urllib.parse import urlsplit

from .bridge_server import private_permissions, atomic_json
from .privacy import require_private_state


class ConfigError(ValueError):
    """Configuration cannot be used safely; messages never include secret data."""


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ConfigError("Duplicate JSON field")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ConfigError("Nonfinite JSON value")))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ConfigError("Expected strict UTF-8 JSON with unique fields") from exc


def _fields(value, allowed, required, label):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ConfigError(f"Invalid or missing fields in {label}")


def safe_path(value, base=None):
    if not isinstance(value, (str, Path)) or not str(value).strip() or '\0' in str(value):
        raise ConfigError("Expected a nonempty filesystem path")
    path = Path(value)
    if not path.is_absolute():
        path = (Path(base) if base is not None else Path.cwd()) / path
    # Inspect before resolve(), which would conceal junctions or symlinks.
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        try:
            info = item.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ConfigError("Symlink, junction or reparse paths are not permitted")
    return path.resolve()


def _inside(path, root):
    return path == root or root in path.parents


def _endpoint(value):
    if not isinstance(value, str):
        raise ConfigError("Endpoint must be an HTTP loopback origin")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or
                parsed.username is not None or parsed.password is not None or
                parsed.path not in ('', '/') or parsed.query or parsed.fragment or
                parsed.port is None or not 1024 <= parsed.port <= 65535):
            raise ValueError()
    except ValueError as exc:
        raise ConfigError("Endpoint must be http://127.0.0.1:PORT with port 1024..65535") from exc
    return f'http://127.0.0.1:{parsed.port}'


WORKER_DEFAULTS = {
    'allowed_paths': [], 'allowed_modes': ['read_only', 'proposal'],
    'claim_ttl_seconds': 900, 'task_timeout_seconds': 600,
    'safety_margin_seconds': 30, 'poll_seconds': 2,
    'request_timeout_seconds': 10, 'shutdown_grace_seconds': 5,
    'auth_retry_seconds': 30,
}


@dataclass(frozen=True)
class Config:
    path: Path
    project: str
    project_root: Path
    runtime_root: Path
    broker_url: str
    console_url: str
    transport: str
    provider: dict
    worker: dict
    digest: str
    codex_provider: dict = field(default_factory=lambda: {'kind': 'unavailable', 'model': None, 'effort': None})

    @property
    def broker_root(self): return self.runtime_root / 'broker'
    @property
    def controller_root(self): return self.runtime_root / 'controllers'
    @property
    def console_root(self): return self.runtime_root / 'console'
    @property
    def output_root(self): return self.runtime_root / 'results'
    @property
    def worker_root(self): return self.runtime_root / 'worker'
    @property
    def run_root(self): return self.runtime_root / 'provider-run'
    @property
    def auth_root(self): return self.runtime_root / 'provider-auth'

    def provider_for(self, actor='claude'):
        if actor not in ('codex', 'claude'):
            raise ConfigError('Unknown worker actor')
        return self.provider if actor == 'claude' else self.codex_provider

    def actor_paths(self, actor='claude'):
        self.provider_for(actor)
        if actor == 'claude':
            return self.worker_root, self.run_root, self.auth_root
        return tuple(self.runtime_root / name for name in ('codex-worker', 'codex-run', 'codex-auth'))

    def public(self):
        return {'project': self.project, 'broker_url': self.broker_url,
                'console_url': self.console_url, 'transport': self.transport,
                'provider': {k: self.provider.get(k) for k in ('kind', 'model', 'effort')},
                'providers': {actor: {k: self.provider_for(actor).get(k) for k in ('kind', 'model', 'effort')}
                              for actor in ('claude', 'codex')},
                'codex_daemon': self.codex_provider['kind'] == 'codex_cli',
                'codex_app': self.codex_provider['kind'] == 'codex_app'}

    def identity(self):
        return {'project': self.project, 'project_root': str(self.project_root),
                'runtime_root': str(self.runtime_root)}

    def worker_settings(self, actor='claude'):
        from .worker_core import WorkerSettings
        return WorkerSettings(actor=actor, project=self.project,
                              project_root=self.project_root, broker_root=self.broker_root,
                              state_root=self.actor_paths(actor)[0], transport=self.transport,
                              url=self.broker_url, **self.worker)

    def adapter(self, actor='claude'):
        provider = self.provider_for(actor)
        if provider['kind'] == 'unavailable':
            return None
        if provider['kind'] in ('codex_app', 'claude_web'):
            from .app_mailbox import AppExecutor
            return AppExecutor(self, actor)
        from .cli_executor import ClaudeCLIExecutor
        from .codex_executor import CodexCLIExecutor
        executor = ClaudeCLIExecutor if actor == 'claude' else CodexCLIExecutor
        _, run, auth = self.actor_paths(actor)
        return executor(provider['command'], runtime_dir=run,
                        config_dir=auth, model=provider['model'], effort=provider['effort'],
                        auth_profile='subscription')


def load_config(path):
    path = safe_path(path)
    if path.stat().st_size > 65536:
        raise ConfigError("Configuration exceeds 64 KiB")
    raw = path.read_bytes()
    data = strict_json(raw.decode('utf-8-sig'))
    _fields(data, {'schema_version', 'project', 'runtime', 'endpoints', 'transport', 'provider', 'codex_provider', 'worker'},
            {'schema_version', 'project', 'runtime', 'endpoints', 'transport', 'provider'}, 'config')
    if type(data['schema_version']) is not int or data['schema_version'] != 1:
        raise ConfigError("Unsupported schema_version; expected integer 1")
    _fields(data['project'], {'id', 'root'}, {'id', 'root'}, 'project')
    _fields(data['runtime'], {'root'}, {'root'}, 'runtime')
    _fields(data['endpoints'], {'broker', 'console'}, {'broker', 'console'}, 'endpoints')
    project = data['project']['id']
    if not isinstance(project, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,159}', project):
        raise ConfigError("project.id must be a 1..160 character ASCII slug")
    project_root = safe_path(data['project']['root'], path.parent)
    runtime_root = safe_path(data['runtime']['root'], path.parent)
    if _inside(project_root, runtime_root) or _inside(runtime_root, project_root):
        raise ConfigError("Project and private runtime paths must not overlap")
    for ancestor in (runtime_root, *runtime_root.parents):
        if (ancestor / '.git').exists():
            raise ConfigError("Private runtime must have no .git ancestor")
    for candidate in (project_root, runtime_root):
        if candidate.exists() and not candidate.is_dir():
            raise ConfigError("Project/runtime root must be a directory")
    broker_url = _endpoint(data['endpoints']['broker'])
    console_url = _endpoint(data['endpoints']['console'])
    if broker_url == console_url:
        raise ConfigError("Broker and console require different ports")
    if data['transport'] not in ('http', 'file'):
        raise ConfigError("transport must be http or file")
    provider = data['provider']
    _fields(provider, {'kind', 'command', 'model', 'effort', 'auth_profile'}, {'kind'}, 'provider')
    if provider['kind'] not in ('unavailable', 'claude_cli', 'claude_web'):
        raise ConfigError("provider.kind must be unavailable, claude_cli or claude_web")
    provider = {'command': [], 'model': None, 'effort': None, 'auth_profile': 'subscription', **provider}
    if not isinstance(provider['command'], list):
        raise ConfigError("provider.command must be an argument list")
    if provider['kind'] in ('unavailable', 'claude_web'):
        if provider['command'] or provider['model'] is not None or provider['effort'] is not None:
            raise ConfigError("Unavailable or web provider cannot declare executable/model/effort; web retains its settings")
    else:
        command = provider['command']
        if (not isinstance(command, list) or len(command) != 1 or not isinstance(command[0], str) or
                not Path(command[0]).is_absolute() or Path(command[0]).name.lower() not in ('claude', 'claude.exe')):
            raise ConfigError("Claude provider requires one absolute native claude executable; shell wrappers are forbidden")
        provider['command'] = [str(safe_path(command[0]))]
    if provider['auth_profile'] != 'subscription':
        raise ConfigError("Only isolated subscription authentication is supported in schema v1")
    if provider['model'] is not None and (not isinstance(provider['model'], str) or
            not re.fullmatch(r'[A-Za-z0-9_.:\[\]-]{1,160}', provider['model'])):
        raise ConfigError("Invalid explicit provider model")
    if provider['effort'] not in (None, 'low', 'medium', 'high', 'xhigh', 'max'):
        raise ConfigError("Unsupported explicit effort; no silent conversion")
    codex_provider = data.get('codex_provider', {'kind': 'unavailable'})
    _fields(codex_provider, {'kind', 'command', 'model', 'effort', 'auth_profile'}, {'kind'}, 'codex_provider')
    codex_provider = {'command': [], 'model': None, 'effort': None, 'auth_profile': 'subscription', **codex_provider}
    if codex_provider['kind'] not in ('unavailable', 'codex_cli', 'codex_app'):
        raise ConfigError('codex_provider.kind must be unavailable, codex_cli or codex_app')
    command = codex_provider['command']
    if not isinstance(command, list):
        raise ConfigError('codex_provider.command must be an argument list')
    if codex_provider['kind'] in ('unavailable', 'codex_app'):
        if command or codex_provider['model'] is not None or codex_provider['effort'] is not None:
            raise ConfigError('Unavailable or App Codex cannot declare command/model/effort; App retains its own settings')
    else:
        if (len(command) != 1 or not isinstance(command[0], str) or not Path(command[0]).is_absolute()
                or Path(command[0]).name.lower() not in ('codex', 'codex.exe')):
            raise ConfigError('Codex requires one absolute native codex executable')
        codex_provider['command'] = [str(safe_path(command[0]))]
    if codex_provider['auth_profile'] != 'subscription':
        raise ConfigError('Codex requires isolated subscription authentication')
    if codex_provider['model'] is not None and (not isinstance(codex_provider['model'], str) or
            not re.fullmatch(r'[A-Za-z0-9_.:-]{1,160}', codex_provider['model'])):
        raise ConfigError('Invalid explicit Codex model')
    if codex_provider['effort'] not in (None, 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'):
        raise ConfigError('Unsupported explicit Codex effort')
    worker = data.get('worker', {})
    _fields(worker, WORKER_DEFAULTS, (), 'worker')
    worker = {**WORKER_DEFAULTS, **worker}
    if not isinstance(worker['allowed_paths'], list) or not all(isinstance(v, str) for v in worker['allowed_paths']):
        raise ConfigError("worker.allowed_paths must be a list of paths")
    scopes = []
    for value in worker['allowed_paths']:
        resolved = safe_path(value, project_root)
        if not _inside(resolved, project_root):
            raise ConfigError("Worker allowed path escapes project")
        scopes.append(str(resolved))
    worker['allowed_paths'] = scopes
    if (not isinstance(worker['allowed_modes'], list) or not worker['allowed_modes'] or
            any(mode not in ('read_only', 'proposal') for mode in worker['allowed_modes'])):
        raise ConfigError("Worker modes must contain read_only and/or proposal")
    ttl = worker['claim_ttl_seconds']
    if type(ttl) is not int or not 30 <= ttl <= 900:
        raise ConfigError("Claim TTL must be integer 30..900")
    timings = [value for key, value in worker.items() if key.endswith('_seconds')]
    if any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0 for v in timings):
        raise ConfigError("Worker timings must be positive finite numbers")
    margin = worker['safety_margin_seconds']
    if (margin < 5 or worker['task_timeout_seconds'] + margin > ttl or
            worker['request_timeout_seconds'] > margin / 2 or worker['shutdown_grace_seconds'] > margin / 2):
        raise ConfigError("Task and shutdown timeouts must fit claim TTL and safety margin")
    return Config(path, project, project_root, runtime_root, broker_url, console_url,
                  data['transport'], provider, worker, hashlib.sha256(raw).hexdigest(), codex_provider)


def require_runtime(config):
    """Verify identity and existing ACLs without repairing or adopting state."""
    root = safe_path(config.runtime_root)
    if not root.is_dir():
        raise ConfigError("Runtime is not initialized; run init with this configuration")
    require_private_state(root)
    marker = safe_path(root / 'relay-runtime.json')
    if not marker.is_file() or marker.stat().st_size > 8192:
        raise ConfigError("Existing runtime is not an initialized Relay runtime")
    try:
        saved = strict_json(marker.read_text(encoding='utf-8'))
        if (saved.get('format') != 'relay-runtime/v1' or saved.get('identity') != config.identity() or
                str(uuid.UUID(saved['runtime_id'])) != saved['runtime_id']):
            raise ValueError()
    except (ValueError, TypeError, AttributeError, KeyError) as exc:
        raise ConfigError("Runtime identity does not match project and runtime paths") from exc
    require_private_state(marker)
    for child in (config.broker_root, config.controller_root, config.console_root, config.output_root,
                  config.worker_root, config.run_root, config.auth_root, root / 'cli-submissions'):
        if not safe_path(child).is_dir():
            raise ConfigError("Runtime is incomplete; initialization was interrupted")
        require_private_state(child)
    for child in config.actor_paths('codex'):
        child = safe_path(child)
        if child.exists():
            if not child.is_dir():
                raise ConfigError('Codex runtime path is not a directory')
            require_private_state(child)
    if config.codex_provider['kind'] == 'codex_app':
        # During init these are created only after the common runtime is verified.
        for child in (root / 'app-mailbox', root / 'app-mailbox' / 'deliveries', root / 'app-mailbox' / 'notifications'):
            child = safe_path(child)
            if child.exists():
                if not child.is_dir():
                    raise ConfigError('App mailbox path is not a directory')
                require_private_state(child)
    return saved


def initialize(config):
    """Prepare only new directories; never adopt another runtime or identity."""
    if config.runtime_root.exists():
        saved = require_runtime(config)
        from .service import _status
        current = _status(config)
        if current['running'] is not False:
            raise ConfigError('Stop the profile before initializing additional actor directories')
        _initialize_codex_paths(config)
        return {'ok': True, 'initialized': False, 'runtime_id': saved['runtime_id'], **config.public()}
    config.project_root.mkdir(parents=True, exist_ok=True)
    safe_path(config.project_root)
    root = config.runtime_root
    root.mkdir(parents=True, exist_ok=False)
    private_permissions(root, directory=True)
    require_private_state(root)
    for child in (config.broker_root, config.controller_root, config.console_root, config.output_root,
                  config.worker_root, config.run_root, config.auth_root, root / 'cli-submissions'):
        child.mkdir()
        private_permissions(child, directory=True)
    saved = {'format': 'relay-runtime/v1', 'runtime_id': str(uuid.uuid4()), 'identity': config.identity()}
    atomic_json(root, root / 'relay-runtime.json', saved)
    private_permissions(root / 'relay-runtime.json')
    require_runtime(config)
    _initialize_codex_paths(config)
    return {'ok': True, 'initialized': True, 'runtime_id': saved['runtime_id'], **config.public()}


def _initialize_codex_paths(config):
    # Additive upgrade inside an already verified runtime; no credentials copied.
    for child in (*config.actor_paths('codex'), config.runtime_root / 'app-mailbox',
                  config.runtime_root / 'app-mailbox' / 'deliveries',
                  config.runtime_root / 'app-mailbox' / 'notifications',
                  config.runtime_root / 'claude-web-mailbox',
                  config.runtime_root / 'claude-web-mailbox' / 'deliveries',
                  config.runtime_root / 'claude-web-mailbox' / 'notifications'):
        child = safe_path(child)
        if child.exists():
            if not child.is_dir():
                raise ConfigError('Codex runtime path is not a directory')
            require_private_state(child)
        else:
            child.mkdir()
            private_permissions(child, directory=True)
            require_private_state(child)
