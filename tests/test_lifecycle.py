"""Identity failures, provider truthfulness and isolated hidden lifecycle acceptance."""
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid
from urllib.request import build_opener, ProxyHandler

from relay_collaboration import service
from relay_collaboration.bridge_server import atomic_json
from relay_collaboration.config import load_config, initialize
from relay_collaboration.process_identity import inspect_process, matches_process, IdentityUnavailable
from relay_collaboration.worker_core import WorkerLock, WorkerError

WORK = Path(__file__).resolve().parents[1] / 'work'


def free_ports():
    listeners = [socket.socket(), socket.socket()]
    try:
        for listener in listeners:
            listener.bind(('127.0.0.1', 0))
        return [listener.getsockname()[1] for listener in listeners]
    finally:
        for listener in listeners:
            listener.close()


def new_config(folder):
    ports = free_ports()
    path = folder / 'profile.json'
    data = {'schema_version': 1, 'project': {'id': 'lifecycle-test', 'root': str(folder / 'project')},
            'runtime': {'root': str(folder / 'private')}, 'endpoints': {
                'broker': f'http://127.0.0.1:{ports[0]}', 'console': f'http://127.0.0.1:{ports[1]}'},
            'transport': 'http', 'provider': {'kind': 'unavailable'}}
    path.write_text(json.dumps(data), encoding='utf-8')
    return load_config(path)


class ProcessIdentityTests(unittest.TestCase):
    def test_current_process_and_creation_mismatch(self):
        actual = inspect_process(os.getpid())
        self.assertEqual(actual['pid'], os.getpid())
        self.assertTrue(actual['creation_time'])
        self.assertTrue(actual['command_line'])
        self.assertTrue(matches_process(actual, inspect_process(os.getpid())))
        self.assertFalse(matches_process({**actual, 'creation_time': 'different'}, actual))
        self.assertFalse(matches_process({**actual, 'command_line': 'different'}, actual))
        self.assertFalse(matches_process({'pid': os.getpid()}, actual))

    def test_invalid_pid_cannot_be_liveness_proof(self):
        for pid in (True, 0, -1, '123', 0x100000000):
            with self.assertRaises(IdentityUnavailable):
                inspect_process(pid)


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix='lifecycle-record-', dir=WORK)).resolve()
        self.folder.relative_to(WORK.resolve())
        self.config = new_config(self.folder)
        self.config.runtime_root.mkdir()
        self.receipt = {'process': inspect_process(os.getpid()), 'instance_id': str(uuid.uuid4()),
                        'config_sha256': self.config.digest, 'config_path': str(self.config.path),
                        'started_at': time.time(), 'state': 'running'}
        self.save()

    def save(self):
        atomic_json(self.config.runtime_root, self.config.runtime_root / 'service.json', self.receipt)

    def test_receipt_fields_do_not_prove_liveness(self):
        with patch.object(service, 'inspect_process', side_effect=IdentityUnavailable('unreadable')):
            value = service._status(self.config)
        self.assertIsNone(value['running'])
        self.assertFalse(value['process_identity_verified'])
        self.assertIsNone(value['pid'])
        self.assertIsNone(value['instance_id'])
        self.assertEqual(value['state'], 'unknown')

    def test_reused_pid_is_not_owned(self):
        self.receipt['process']['creation_time'] = 'not-current'
        self.save()
        value = service._status(self.config)
        self.assertIsNone(value['running'])
        self.assertFalse(value['process_identity_verified'])
        self.assertIsNone(value['pid'])
        with patch('relay_collaboration.config.require_runtime'):
            with self.assertRaisesRegex(service.ServiceError, 'identity_unavailable'):
                service.stop(self.config, 0)
            with self.assertRaisesRegex(service.ServiceError, 'identity_unavailable'):
                service.start(self.config)
        self.assertFalse((self.config.runtime_root / 'stop.request').exists())

    def test_config_change_blocks_mutation_of_live_process(self):
        changed = replace(self.config, digest='changed')
        self.assertEqual(service._status(changed)['state'], 'config_mismatch')
        with patch('relay_collaboration.config.require_runtime'):
            with self.assertRaisesRegex(service.ServiceError, 'config_mismatch'):
                service.stop(changed, 0)
            with self.assertRaisesRegex(service.ServiceError, 'config_mismatch'):
                service.start(changed)
        self.assertFalse((self.config.runtime_root / 'stop.request').exists())

    def test_stop_request_is_bound_to_instance_and_config(self):
        path = self.config.runtime_root / 'stop.request'
        for identity, digest, expected in ((str(uuid.uuid4()), self.config.digest, False),
                (self.receipt['instance_id'], 'different', False),
                (self.receipt['instance_id'], self.config.digest, True)):
            atomic_json(self.config.runtime_root, path, {'action': 'stop', 'instance_id': identity, 'config_sha256': digest})
            self.assertEqual(service._stop_requested(self.config, self.receipt['instance_id']), expected)
        self.assertTrue(path.exists())  # Checking cannot clear a concurrent stop.

    def test_provider_unavailable_never_constructs_executor(self):
        monitor = service.WorkerMonitor(self.config)
        with patch.object(type(self.config), 'adapter', side_effect=AssertionError('must not execute')):
            value = monitor.snapshot()
            self.assertEqual(value['state'], 'unavailable')
            self.assertFalse(value['verified'])
            self.assertFalse(value['provider_ready'])
            with self.assertRaises(service.ConsoleError):
                monitor.action('start')
            with self.assertRaises(service.ConsoleError):
                monitor.action('arbitrary shell command')
        self.assertIsNone(value['pid'])
        self.assertIsNone(value['executions'])

    def test_endpoint_conflict_does_not_launch_or_stop_owner(self):
        endpoint = service.urlsplit(self.config.console_url)
        listener = socket.socket()
        listener.bind((endpoint.hostname, endpoint.port))
        listener.listen()
        try:
            with self.assertRaisesRegex(service.ServiceError, 'already_in_use'):
                service._probes(self.config)
            self.assertNotEqual(listener.fileno(), -1)
        finally:
            listener.close()


    def test_stop_timeout_never_claims_process_exit(self):
        with patch('relay_collaboration.config.require_runtime'):
            value = service.stop(self.config, 0)
        self.assertFalse(value['ok'])
        self.assertFalse(value['stop_confirmed'])
        self.assertTrue(value['running'])
        self.assertTrue(value['stop_requested'])
        self.assertEqual(value['reason'], 'graceful_stop_pending')
        self.assertFalse(value['hard_kill'])

    def test_monitor_retains_actor_lock_until_callback_exits(self):
        self.config.broker_root.mkdir()
        self.config.project_root.mkdir()
        callback_started, callback_release, closed = threading.Event(), threading.Event(), threading.Event()
        broker_root = self.config.broker_root
        class ActiveWorker:
            def __init__(self, *_):
                self.executor_stop = threading.Event()
                self.active_thread = threading.Thread(target=callback_release.wait)
                self.lock = None
            def run(self, *_):
                self.lock = WorkerLock(broker_root, 'claude')
                self.active_thread.start()
                callback_started.set()
                raise WorkerError('callback remains alive')
            def snapshot(self):
                return {'state': 'stopping', 'model_execution_count': 0}
            def close(self):
                self.assert_callback_dead = not self.active_thread.is_alive()
                if not self.assert_callback_dead:
                    raise AssertionError('actor lock released before callback stopped')
                self.lock.close()
                closed.set()
        configured = replace(self.config, provider={'kind': 'claude_cli', 'model': 'test-only', 'effort': None})
        monitor = service.WorkerMonitor(configured)
        class Adapter:
            def preflight(self): return {'ok': False, 'auth_ready': False}
        with patch.object(type(configured), 'adapter', return_value=Adapter()), patch.object(service, 'Worker', ActiveWorker):
            try:
                monitor.action('start')
                self.assertTrue(callback_started.wait(3))
                monitor.action('stop')
                self.assertFalse(closed.is_set())
                self.assertTrue(monitor.snapshot()['verified'])
                self.assertFalse(monitor.snapshot()['provider_ready'])
                with self.assertRaises(WorkerError):
                    WorkerLock(broker_root, 'claude')
            finally:
                callback_release.set()
                monitor.close()
        self.assertTrue(closed.is_set())
        replacement = WorkerLock(broker_root, 'claude')
        replacement.close()
        final = monitor.snapshot()
        self.assertFalse(final['verified'])
        self.assertIsNone(final['pid'])
        self.assertIsNone(final['last_task_id'])
        self.assertIsNone(final['executions'])
        self.assertIsNone(final['updated_at'])


class LifecycleAcceptanceTests(unittest.TestCase):
    def test_hidden_start_duplicate_stop_restart_fresh_runtime(self):
        WORK.mkdir(exist_ok=True)
        folder = Path(tempfile.mkdtemp(prefix='lifecycle-live-', dir=WORK)).resolve()
        folder.relative_to(WORK.resolve())
        config = new_config(folder)
        initialize(config)
        opener = build_opener(ProxyHandler({}))
        try:
            with patch.object(service.subprocess, 'Popen', wraps=service.subprocess.Popen) as launch:
                started = service.start(config)
            self.assertEqual(launch.call_args.kwargs['cwd'], config.runtime_root)
            self.assertIn('-B', launch.call_args.args[0])
            self.assertEqual(launch.call_args.args[0][-2:], ['--expected-config-digest', config.digest])
            if os.name == 'nt':
                self.assertEqual(launch.call_args.kwargs['creationflags'], service.subprocess.CREATE_NO_WINDOW)
            self.assertTrue(started.get('ok'), started)
            self.assertTrue(started.get('process_identity_verified'), started)
            self.assertEqual(started['state'], 'running')
            again = service.start(config)
            self.assertTrue(again['already_running'])
            self.assertEqual(again['instance_id'], started['instance_id'])
            self.assertEqual(again['pid'], started['pid'])
            with opener.open(config.broker_url + '/health', timeout=5) as response:
                self.assertTrue(json.loads(response.read())['ok'])
            with opener.open(config.console_url + '/', timeout=5) as response:
                self.assertIn(b'Relay', response.read())
            stopped = service.stop(config)
            self.assertFalse(stopped['running'], stopped)
            self.assertFalse(stopped['hard_kill'])
            self.assertTrue(stopped['stop_confirmed'])
            # Old UUID stop request remains, but must not stop the next instance.
            restarted = service.start(config)
            self.assertTrue(restarted.get('ok'), restarted)
            self.assertNotEqual(restarted['instance_id'], started['instance_id'])
            self.assertTrue(service.status(config)['running'])
            self.assertFalse(service.stop(config)['running'])
            self.assertTrue(service.stop(config)['already_stopped'])
            data = json.loads(config.path.read_text(encoding='utf-8'))
            data['worker'] = {'poll_seconds': 3}
            config.path.write_text(json.dumps(data), encoding='utf-8')
            config = load_config(config.path)
            changed = service.start(config)
            self.assertTrue(changed.get('ok'), changed)
            self.assertTrue(changed['config_matches'])
            self.assertTrue(service.stop(config)['stop_confirmed'])
        finally:
            result = service.status(config)
            if result.get('running'):
                service.stop(config, 30)
        (folder / 'acceptance.json').write_text(json.dumps({'hidden_start': True, 'duplicate_prevented': True,
            'restart_ignored_old_stop_uuid': True, 'provider': 'unavailable', 'real_model_test': False,
            'final_running': service.status(config)['running']}), encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
