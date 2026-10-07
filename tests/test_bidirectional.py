"""Both directions through HTTP console, durable controller and real broker.

Only executor callbacks are fixtures, clearly labelled as such.
"""
from dataclasses import replace
from http.server import ThreadingHTTPServer
from http.cookiejar import CookieJar
import json
from pathlib import Path
import shutil
import threading
import time
import unittest
import uuid
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPCookieProcessor, ProxyHandler

from relay_collaboration import cli, service
from relay_collaboration.bridge_server import BridgeService
from relay_collaboration.config import initialize, load_config, ConfigError
from relay_collaboration.console_backend import Backend, Observer, Reviews, ConsoleError
from relay_collaboration.console_server import handler_for
from tests.test_lifecycle import new_config


class TwoWayTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / 'work' / ('two-way-' + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.config = new_config(self.root)
        initialize(self.config)

    def payload(self, actor):
        return {'client_request_id':str(uuid.uuid4()), 'recipient':actor, 'title':'雙向測試 ' + actor,
                'prompt':'請審查明確附件。', 'contexts':[{'name':'sample.txt','text':'中文 <script>資料</script>'}]}

    def test_invalid_direction_and_direction_uuid_conflict(self):
        reviews = Reviews(self.config.console_root, self.config.output_root, object(), False, self.config)
        body = self.payload('codex')
        original = reviews.submit(body)
        self.assertEqual(original['recipient'], 'codex')
        self.assertTrue(reviews.submit(body)['deduped'])
        with self.assertRaises(ConsoleError): reviews.submit({**body, 'recipient':'claude'})
        for recipient in ('other', '', [], None):
            with self.subTest(recipient=recipient), self.assertRaises(ConsoleError):
                reviews.submit({**body, 'recipient':recipient})
        # Recovery verifies saved direction against the original payload.
        saved = reviews.runtime / 'submissions' / body['client_request_id'] / 'job.json'
        job = json.loads(saved.read_text(encoding='utf-8')); job['recipient'] = 'claude'
        saved.write_text(json.dumps(job), encoding='utf-8')
        recovered = Reviews(self.config.console_root, self.config.output_root, object(), False, self.config)
        self.assertIn(body['client_request_id'], recovered.blocked)

    def test_additive_configuration_and_separate_actor_paths(self):
        raw = json.loads(self.config.path.read_text(encoding='utf-8'))
        raw['codex_provider'] = {'kind':'codex_cli', 'command':[str(self.root / 'codex.exe')], 'effort':'high'}
        self.config.path.write_text(json.dumps(raw), encoding='utf-8')
        config = load_config(self.config.path)
        self.assertTrue(config.public()['codex_daemon'])
        self.assertNotEqual(config.worker_settings().state_root, config.worker_settings('codex').state_root)
        self.assertEqual(config.worker_settings('codex').actor, 'codex')
        self.assertNotEqual(config.adapter('codex').config_dir, config.auth_root)
        for value in ({'kind':'claude_cli'}, {'kind':'unavailable','command':['x']},
                      {'kind':'codex_cli','command':['relative/codex.exe']},
                      {'kind':'codex_cli','command':[str(self.root/'cmd.exe')]},
                      {'kind':'unavailable','auth_profile':'api_key'}):
            raw['codex_provider'] = value
            self.config.path.write_text(json.dumps(raw), encoding='utf-8')
            with self.subTest(value=value), self.assertRaises(ConfigError): load_config(self.config.path)

    def test_localhost_profiles_keep_independent_browser_cookies(self):
        from types import SimpleNamespace
        servers = []
        opener = build_opener(ProxyHandler({}), HTTPCookieProcessor(CookieJar()))
        try:
            for index in range(2):
                backend = SimpleNamespace(profile_id=str(uuid.uuid4()), config=self.config,
                                          summary=lambda: {'ok':True})
                console = ThreadingHTTPServer(('127.0.0.1',0),
                    handler_for(backend, 'cookie-' + str(index), 'csrf', 'activity'))
                thread = threading.Thread(target=console.serve_forever, daemon=True); thread.start()
                servers.append((console, thread))
                with opener.open(f'http://127.0.0.1:{console.server_port}/api/session') as response:
                    self.assertIn('relay_session_' + backend.profile_id.replace('-', ''), response.headers['Set-Cookie'])
            # Browser's single jar now contains both profiles; both still work.
            for console, _ in servers:
                with opener.open(f'http://127.0.0.1:{console.server_port}/api/summary') as response:
                    self.assertTrue(json.load(response)['ok'])
        finally:
            for console, thread in servers:
                console.shutdown(); console.server_close(); thread.join()

    def test_http_and_file_roundtrip_each_worker_once_then_restart(self):
        for transport in ('http', 'file'):
            with self.subTest(transport=transport):
                self.roundtrip(transport)

    def roundtrip(self, transport):
        calls = []
        stop = threading.Event()
        class FixtureExecutor:
            def __init__(self, actor): self.actor = actor
            def preflight(self): return {'ok':True, 'auth_ready':True}
            def __call__(self, task, context):
                calls.append((self.actor, task['task_id']))
                return {'ok':True, 'status':'completed', 'text':'測試回覆 ' + self.actor,
                        'execution_kind':'mock_test', 'model_execution_verified':False}
        config = replace(self.config, transport=transport,
                         worker={**self.config.worker, 'poll_seconds':.03},
                         provider={'kind':'claude_cli'}, codex_provider={'kind':'codex_cli'})
        bridge = BridgeService(config.broker_root, poll_seconds=.02)
        port = urlsplit(config.broker_url).port
        bridge.start(port=port)
        workers = service.WorkerGroup(config, stop)
        backend = Backend(config.console_root, config.output_root,
                          Observer(config.broker_root, config.broker_url, config.project), workers, config=config, stop_event=stop)
        console = ThreadingHTTPServer(('127.0.0.1',urlsplit(config.console_url).port),
                                     handler_for(backend,'fixture-cookie','fixture-csrf','fixture-activity'))
        thread = threading.Thread(target=console.serve_forever, daemon=True); thread.start()
        try:
            with patch.object(type(config), 'adapter', lambda _, actor='claude': FixtureExecutor(actor)):
                workers.start_available()
                bodies = [self.payload(actor) for actor in ('claude', 'codex')]
                session = cli.ConsoleSession(config)
                for body in bodies:
                    result = session.post('/api/review', json.dumps(body).encode('utf-8'))
                    self.assertEqual(result['recipient'], body['recipient'])
                deadline = time.monotonic() + 15
                while time.monotonic() < deadline:
                    if all(backend.reviews.jobs[b['client_request_id']]['state'] == 'completed' for b in bodies): break
                    time.sleep(.05)
                for body in bodies:
                    job = backend.reviews.jobs[body['client_request_id']]
                    self.assertEqual(job['state'], 'completed', job)
                    task = backend.detail(job['task_id'])['task']
                    self.assertEqual(task['recipient'], body['recipient'])
                    self.assertEqual(task['sender'], 'claude' if body['recipient']=='codex' else 'codex')
                    self.assertEqual(task['result']['text'], '測試回覆 ' + body['recipient'])
                    self.assertEqual(task['result']['execution_kind'], 'mock_test')
                    self.assertTrue(session.post('/api/review', json.dumps(body).encode('utf-8'))['deduped'])
                self.assertEqual(len(calls), 2)
                self.assertEqual(set(a for a, _ in calls), {'claude', 'codex'})
                session.post('/api/worker', b'{"action":"stop","actor":"codex"}')
                workers.monitors['codex'].close()
                self.assertFalse(workers.snapshots()['codex']['running'])
                self.assertTrue(workers.snapshots()['claude']['running'])
                # New controller/worker objects read persisted terminal receipts.
                workers.monitors['codex'].action('start')
                recovered = Reviews(config.console_root, config.output_root, backend.observer, False, config)
                for body in bodies:
                    receipt = recovered.retry(body['client_request_id'])
                    self.assertEqual(receipt['state'], 'completed')
                    self.assertTrue(receipt['deduped'])
                time.sleep(.15)
                self.assertEqual(len(calls), 2)
        finally:
            stop.set(); console.shutdown(); console.server_close(); thread.join()
            backend.reviews.pool.shutdown(wait=True, cancel_futures=True)
            workers.close(); bridge.close()
