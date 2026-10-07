"""Hidden local supervisor with verified identity and graceful UUID-scoped stop."""
from __future__ import annotations
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit
from .bridge_server import BridgeService, atomic_json, bounded_read, private_permissions, safe_path
from .console_backend import Backend, ConsoleError, Observer
from .console_server import handler_for
from .context_dispatch import JournalLock
from .process_identity import IdentityUnavailable, inspect_process, matches_process
from .worker_core import Worker
from .execution import command_prefix, supervisor_environment


class ServiceError(RuntimeError):
    pass


def _record(root, name):
    try:
        value = json.loads(bounded_read(root, Path(root) / name, 65536).decode('utf-8'))
    except FileNotFoundError:
        return None
    if not isinstance(value, dict):
        raise ServiceError('invalid_service_record')
    return value


def _receipt(config):
    service = _record(config.runtime_root, 'service.json')
    launch = _record(config.runtime_root, 'launch.json')
    if launch and (not service or launch.get('started_at', 0) > service.get('started_at', 0)):
        return launch
    return service


def _status(config):
    base = {'ok': True, 'schema': 'relay-service-status/v1', 'running': False,
            'process_identity_verified': False, 'state': 'stopped', 'pid': None,
            'instance_id': None, 'config_matches': True, 'checked_at': time.time(),
            'console_url': config.console_url, 'broker_url': config.broker_url,
            'codex_daemon': config.public()['codex_daemon']}
    try:
        record = _receipt(config)
        if record is None:
            return base
        identity = record['process']
        instance_id = record['instance_id']
        if str(uuid.UUID(instance_id)) != instance_id:
            raise ValueError()
        actual = inspect_process(identity['pid'])
        config_matches = (record.get('config_sha256') == config.digest and
                          os.path.normcase(record.get('config_path', '')) == os.path.normcase(str(config.path)))
        if actual is None:
            return {**base, 'state': 'stopped', 'reason': 'recorded_process_exited', 'config_matches': config_matches}
        if not matches_process(identity, actual):
            return {**base, 'ok': False, 'running': None, 'state': 'unknown',
                    'reason': 'process_identity_mismatch', 'config_matches': config_matches}
        return {**base, 'running': True, 'process_identity_verified': True,
                'state': record.get('state', 'unknown') if config_matches else 'config_mismatch',
                'pid': actual['pid'], 'instance_id': instance_id, 'config_matches': config_matches,
                'provider': record.get('provider', 'unavailable')}
    except (IdentityUnavailable, OSError, ValueError, KeyError, TypeError, ServiceError):
        return {**base, 'ok': False, 'running': None, 'state': 'unknown', 'reason': 'process_identity_unavailable',
                'config_matches': None}


def status(config):
    from .config import require_runtime
    require_runtime(config)
    return _status(config)


def _lock_path(config, kind):
    # Keep launch and supervisor ownership in distinct named lock locations.
    folder = safe_path(config.runtime_root, config.runtime_root / 'lifecycle' / kind)
    folder.mkdir(parents=True, exist_ok=True)
    return folder / 'owner'


def _probes(config):
    sockets = []
    try:
        for endpoint in (config.broker_url, config.console_url):
            parsed = urlsplit(endpoint)
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sockets.append(probe)
            if os.name == 'nt':
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind((parsed.hostname, parsed.port))
        return sockets
    except OSError:
        for probe in sockets:
            probe.close()
        raise ServiceError('configured_endpoint_already_in_use') from None


def _command(config, instance_id):
    return [*command_prefix(), '--config', str(config.path), 'serve', '--instance-id', instance_id,
            '--expected-config-digest', config.digest]


def start(config):
    from .config import require_runtime
    require_runtime(config)
    with JournalLock(_lock_path(config, 'launch')):
        current = _status(config)
        if current['running'] is None:
            raise ServiceError('existing_process_identity_unavailable')
        if current['running']:
            if not current['config_matches']:
                raise ServiceError('running_service_config_mismatch')
            return {**current, 'already_running': True}
        # Probe without disturbing the process that may already own an endpoint.
        for probe in _probes(config):
            probe.close()
        instance_id, started = str(uuid.uuid4()), time.time()
        command = _command(config, instance_id)
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
        log_path = safe_path(config.runtime_root, config.runtime_root / 'service.log')
        with log_path.open('ab', buffering=0) as log:
            process = subprocess.Popen(command, cwd=config.runtime_root, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=log, close_fds=True, env=supervisor_environment(), **options)
        # Keep a separate launch receipt; never overwrite a child's ready receipt.
        try:
            identity = inspect_process(process.pid)
            if identity:
                atomic_json(config.runtime_root, config.runtime_root / 'launch.json', {
                    'schema': 'relay-service/v1', 'process': identity, 'instance_id': instance_id,
                    'config_path': str(config.path), 'config_sha256': config.digest,
                    'started_at': started, 'state': 'starting', 'provider': config.provider['kind']})
        except IdentityUnavailable:
            pass  # Child must provide its own OS-verified receipt before ready.
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return {'ok': False, 'state': 'startup_failed', 'reason': 'supervisor_exited', 'exit_code': process.returncode}
            result = _status(config)
            if result['instance_id'] == instance_id and result['state'] == 'running' and result['process_identity_verified']:
                threading.Thread(target=process.wait, daemon=True, name='relay-child-reaper').start()
                return result
            time.sleep(.1)
        threading.Thread(target=process.wait, daemon=True, name='relay-child-reaper').start()
        return {'ok': False, 'state': 'startup_pending', 'instance_id': instance_id,
                'reason': 'readiness_not_yet_verified', 'hard_kill': False}


def stop(config, wait_seconds=15):
    from .config import require_runtime
    require_runtime(config)
    if type(wait_seconds) not in (int, float) or not 0 <= wait_seconds <= 60:
        raise ServiceError('stop_wait_must_be_between_0_and_60_seconds')
    with JournalLock(_lock_path(config, 'launch')):
        current = _status(config)
        if current['running'] is None:
            raise ServiceError('existing_process_identity_unavailable')
        if not current['running']:
            return {**current, 'already_stopped': True, 'hard_kill': False, 'stop_confirmed': True}
        if not current['config_matches']:
            raise ServiceError('running_service_config_mismatch')
        atomic_json(config.runtime_root, config.runtime_root / 'stop.request', {
            'schema': 'relay-stop/v1', 'action': 'stop', 'instance_id': current['instance_id'],
            'config_sha256': config.digest, 'requested_at': time.time()})
        deadline = time.monotonic() + wait_seconds
        while time.monotonic() < deadline:
            result = _status(config)
            if result['running'] is False:
                return {**result, 'stop_requested': True, 'hard_kill': False, 'stop_confirmed': True}
            if result['instance_id'] not in (None, current['instance_id']):
                raise ServiceError('service_instance_changed_during_stop')
            time.sleep(.1)
        final = _status(config)
        if final['running'] is False:
            return {**final, 'stop_requested': True, 'hard_kill': False, 'stop_confirmed': True}
        return {**final, 'ok': False, 'state': 'stopping', 'reason': 'graceful_stop_pending',
                'stop_requested': True, 'hard_kill': False, 'stop_confirmed': False}


def _stop_requested(config, instance_id):
    value = _record(config.runtime_root, 'stop.request')
    if value is None:
        return False
    return (value.get('action') == 'stop' and value.get('instance_id') == instance_id and
            value.get('config_sha256') == config.digest)

class WorkerMonitor:
    """Own one actor's consumer thread and its execution journal."""
    def __init__(self, config, stop_event=None, actor='claude'):
        self.config, self.stop_event = config, stop_event or threading.Event()
        self.actor, self.provider = actor, config.provider_for(actor)
        self.lock = threading.RLock()
        self.thread, self.worker = None, None
        self.worker_stop = threading.Event()
        self.last_state, self.reason, self.control = 'stopped', None, None
        self.available = self.provider.get('kind') != 'unavailable'

    def _run(self):
        worker = None
        try:
            adapter = self.config.adapter(self.actor)
            if adapter is None:
                with self.lock:
                    self.last_state = 'unavailable'
                return
            worker = Worker(self.config.worker_settings(self.actor), adapter, adapter.preflight)
            with self.lock:
                self.worker = worker
            # SQLite connection is opened, used and closed on this same thread.
            worker.run(self.worker_stop)
        except BaseException:
            with self.lock:
                self.last_state, self.reason = 'attention_required', 'worker_interrupted_check_journal'
        finally:
            # A cancellation-resistant callback must retain its actor lock.
            if worker is not None:
                if worker.executor_stop is not None:
                    worker.executor_stop.set()
                while worker.active_thread is not None and worker.active_thread.is_alive():
                    with self.lock:
                        self.last_state, self.reason = 'stopping', 'waiting_for_executor_exit_actor_lock_retained'
                    worker.active_thread.join(.25)
                try:
                    worker.close()
                except Exception:
                    with self.lock:
                        self.last_state, self.reason = 'attention_required', 'worker_cleanup_failed'
            with self.lock:
                self.worker = None
                if self.last_state not in ('attention_required', 'unavailable'):
                    self.last_state, self.reason = 'stopped', None
                self.control = {'state': 'completed', 'action': 'stop', 'at': time.time()}

    def snapshot(self):
        with self.lock:
            live = self.thread is not None and self.thread.is_alive()
            state = self.worker.snapshot() if live and self.worker is not None else {}
            label = ('unavailable' if not self.available else
                     'stopping' if live and self.worker_stop.is_set() else state.get('state', self.last_state))
            app = None
            if self.provider['kind'] in ('codex_app', 'claude_web'):
                try:
                    from .app_mailbox import AppMailbox
                    app = AppMailbox(self.config, self.actor).status()
                except (ValueError, OSError, RuntimeError):
                    app = {'state': 'unavailable', 'connected': False, 'login_required': False}
            return {'state': label, 'reason': state.get('reason', self.reason), 'verified': live,
                    'running': live, 'identity_kind': 'supervisor_owned_thread' if live else None,
                    'provider_ready': live and state.get('auth_ready') is True,
                    'pid': os.getpid() if live else None, 'checked_at': time.time(),
                    'last_task_id': state.get('last_task_id') if live else None,
                    'executions': state.get('model_execution_count') if live else None,
                    'updated_at': state.get('updated_at') if live else None,
                    'actor': self.actor, 'provider': self.provider['kind'],
                    'model': self.provider.get('model'), 'effort': self.provider.get('effort'),
                    'control': self.control, 'app': app,
                    'codex_daemon': self.actor == 'codex' and self.provider['kind'] == 'codex_cli' and live}

    def action(self, action):
        if action not in ('start', 'stop'):
            raise ConsoleError('invalid_action', 'Only worker start and stop are supported.')
        with self.lock:
            if not self.available:
                raise ConsoleError('provider_unavailable', 'Configure a provider before starting a worker.', 503)
            if action == 'start':
                if self.stop_event.is_set():
                    raise ConsoleError('service_stopping', 'The supervisor is stopping.', 409)
                if self.thread is not None and self.thread.is_alive():
                    return self.snapshot()
                self.worker_stop = threading.Event()
                self.last_state, self.reason = 'starting', None
                self.control = {'state': 'completed', 'action': 'start', 'at': time.time()}
                self.thread = threading.Thread(target=self._run, daemon=False, name='relay-' + self.actor + '-worker')
                self.thread.start()
            else:
                self.worker_stop.set()
                self.control = {'state': 'running' if self.thread and self.thread.is_alive() else 'completed',
                                'action': 'stop', 'at': time.time()}
            return self.snapshot()

    def close(self):
        self.worker_stop.set()
        thread = self.thread
        if thread is not None:
            while thread.is_alive():
                thread.join(.25)


class WorkerGroup:
    """Two separately controlled workers; the legacy snapshot denotes Claude."""
    def __init__(self, config, stop_event):
        self.monitors = {actor: WorkerMonitor(config, stop_event, actor) for actor in ('claude', 'codex')}

    @property
    def available(self):
        return any(m.available for m in self.monitors.values())

    def snapshot(self):
        return self.monitors['claude'].snapshot()

    def snapshots(self):
        return {actor: monitor.snapshot() for actor, monitor in self.monitors.items()}

    def action(self, action, actor='claude'):
        if actor not in self.monitors:
            raise ConsoleError('invalid_actor', 'Worker actor must be codex or claude.')
        return self.monitors[actor].action(action)

    def start_available(self):
        for monitor in self.monitors.values():
            if monitor.available:
                monitor.action('start')

    def close(self):
        # Cancel both before waiting on either, so neither consumes new work.
        for monitor in self.monitors.values():
            monitor.worker_stop.set()
        for monitor in self.monitors.values():
            monitor.close()


def _activity_secret(config):
    path = safe_path(config.runtime_root, config.console_root / 'activity.token')
    if not path.exists():
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'w', encoding='ascii') as stream:
            stream.write(secrets.token_urlsafe(48) + '\n')
    private_permissions(path)
    token = bounded_read(config.runtime_root, path, 256).decode('ascii').strip()
    if len(token) < 43 or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-' for c in token):
        raise ServiceError('invalid_activity_token')
    return token


def serve(config, instance_id=None):
    from .config import require_runtime
    require_runtime(config)
    instance_id = instance_id or str(uuid.uuid4())
    if str(uuid.UUID(instance_id)) != instance_id:
        raise ServiceError('invalid_instance_uuid')
    with JournalLock(_lock_path(config, 'supervisor')):
        identity = inspect_process(os.getpid())
        if identity is None:
            raise ServiceError('supervisor_identity_unavailable')
        existing = _status(config)
        if existing['running'] is None:
            raise ServiceError('existing_process_identity_unavailable')
        if existing['running'] and existing['instance_id'] != instance_id:
            raise ServiceError('another_verified_service_owns_runtime')
        record = {'schema': 'relay-service/v1', 'process': identity, 'instance_id': instance_id,
                  'started_at': time.time(), 'config_path': str(config.path), 'config_sha256': config.digest,
                  'provider': config.provider['kind'], 'state': 'starting'}
        def save(state):
            record.update(state=state, updated_at=time.time())
            atomic_json(config.runtime_root, config.runtime_root / 'service.json', record)
        save('starting')
        stop_event = threading.Event()
        bridge = console = backend = monitor = console_thread = None
        probes = []
        old_signals = {}
        try:
            if threading.current_thread() is threading.main_thread():
                for signum in (signal.SIGINT, signal.SIGTERM):
                    old_signals[signum] = signal.signal(signum, lambda *_: stop_event.set())
            probes = _probes(config)
            bridge = BridgeService(config.broker_root)
            broker_address = urlsplit(config.broker_url)
            probes[0].close()
            bridge.start(broker_address.hostname, broker_address.port)
            monitor = WorkerGroup(config, stop_event)
            observer = Observer(config.broker_root, config.broker_url, config.project)
            backend = Backend(config.console_root, config.output_root, observer, monitor,
                              config=config, stop_event=stop_event)
            console_address = urlsplit(config.console_url)
            activity_token = _activity_secret(config)
            probes[1].close()
            console = ThreadingHTTPServer((console_address.hostname, console_address.port),
                        handler_for(backend, secrets.token_urlsafe(48), secrets.token_urlsafe(48), activity_token))
            console.daemon_threads = True
            console_thread = threading.Thread(target=console.serve_forever, daemon=True, name='relay-console')
            console_thread.start()
            if _stop_requested(config, instance_id):
                stop_event.set()
            if monitor.available and not stop_event.is_set():
                monitor.start_available()
            save('stopping' if stop_event.is_set() else 'running')
            while not stop_event.wait(.2):
                if _stop_requested(config, instance_id):
                    stop_event.set()
        except BaseException:
            record['reason'] = 'supervisor_startup_or_runtime_failed'
            raise
        finally:
            stop_event.set()
            save('stopping')
            for probe in probes:
                probe.close()
            if console is not None:
                if console_thread is not None:
                    console.shutdown()
                console.server_close()
            # Interrupt controller waits before waiting for their pool to exit.
            if backend is not None and backend.reviews.pool is not None:
                backend.reviews.pool.shutdown(wait=True, cancel_futures=True)
            if monitor is not None:
                monitor.close()
            if bridge is not None:
                bridge.close()
            save('stopped')
            for signum, handler in old_signals.items():
                signal.signal(signum, handler)
        return 0
