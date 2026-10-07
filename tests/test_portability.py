"""Portable configuration and durable CLI fixtures; never call a model/provider."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from relay_collaboration import cli
from relay_collaboration.config import ConfigError, initialize, load_config, require_runtime
from relay_collaboration.console_backend import Backend


class PortabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'work', prefix='portability-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.make_config('first')

    def make_config(self, name, changes=None):
        folder = self.root / name / 'config'
        folder.mkdir(parents=True)
        body = {'schema_version': 1, 'project': {'id': 'portable-fixture', 'root': '../project'},
                'runtime': {'root': '../private'}, 'endpoints': {'broker': 'http://127.0.0.1:19041', 'console': 'http://127.0.0.1:19042'},
                'transport': 'http', 'provider': {'kind': 'unavailable'}}
        if changes:
            changes(body)
        path = folder / 'relay.json'
        path.write_text(json.dumps(body), encoding='utf-8')
        return load_config(path)

    def initialized(self):
        initialize(self.config)
        return self.config

    def payload(self):
        value = {'client_request_id': str(uuid.uuid4()), 'title': 'Durable fixture',
                 'prompt': 'Explicit fixture context; no real model.', 'contexts': [{'name': 'example.py', 'text': 'print("fixture")'}]}
        return value, (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')

    def test_relative_config_relocates_and_initializes_distinct_runtime(self):
        second = self.make_config('second')
        self.assertEqual(self.config.path.read_bytes(), second.path.read_bytes())
        self.assertEqual(self.config.project_root, self.root / 'first/project')
        self.assertEqual(second.project_root, self.root / 'second/project')
        first_id = initialize(self.config)['runtime_id']
        second_id = initialize(second)['runtime_id']
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(initialize(self.config)['runtime_id'], first_id)
        self.assertFalse(initialize(self.config)['initialized'])
        for config in (self.config, second):
            self.assertEqual(list(config.auth_root.iterdir()), [])
            self.assertIsNone(config.adapter())

    def test_copied_runtime_marker_fails_identity_and_is_preserved(self):
        self.initialized()
        second = self.make_config('second')
        initialize(second)
        copied = (self.config.runtime_root / 'relay-runtime.json').read_bytes()
        marker = second.runtime_root / 'relay-runtime.json'
        marker.write_bytes(copied)
        with self.assertRaisesRegex(ConfigError, 'identity'):
            require_runtime(second)
        with self.assertRaisesRegex(ConfigError, 'identity'):
            initialize(second)
        self.assertEqual(marker.read_bytes(), copied)

    def test_session_profile_is_stable_and_changes_with_fresh_runtime(self):
        self.initialized()
        second = self.make_config('second')
        initialize(second)
        def backend(config):
            return Backend(config.console_root, config.output_root, object(), object(), execute=False, config=config)
        first_id = backend(self.config).profile_id
        self.assertEqual(first_id, require_runtime(self.config)['runtime_id'])
        self.assertEqual(backend(self.config).profile_id, first_id)
        self.assertNotEqual(backend(second).profile_id, first_id)

    def test_cli_rejects_same_endpoint_with_different_runtime_profile(self):
        self.initialized()
        session = cli.ConsoleSession(self.config)
        with patch.object(session, '_request', return_value={'csrf': 'fixture-csrf', 'profile_id': str(uuid.uuid4())}) as request:
            with self.assertRaisesRegex(cli.CLIError, 'profile identity'):
                session.post('/api/review', b'{}')
            self.assertEqual(request.call_count, 1)
            self.assertEqual(request.call_args.args[0], '/api/session')
        own_id = require_runtime(self.config)['runtime_id']
        with patch.object(session, '_request', return_value={'csrf': 'fixture-csrf', 'profile_id': own_id, 'config_sha256': '0' * 64}) as request:
            with self.assertRaisesRegex(cli.CLIError, 'different configuration'):
                session.post('/api/review', b'{}')
            self.assertEqual(request.call_count, 1)
        with patch.object(session, '_request', side_effect=[{'csrf': 'fixture-csrf', 'profile_id': own_id, 'config_sha256': self.config.digest}, {'state': 'accepted'}]) as request:
            self.assertEqual(session.post('/api/review', b'{}'), {'state': 'accepted'})
            self.assertEqual(request.call_count, 2)

    def test_unconfigured_provider_remains_unavailable_after_initialization(self):
        self.initialized()
        with patch('relay_collaboration.config.Config.adapter', side_effect=AssertionError('must not invoke adapter')):
            result = cli.doctor(self.config)
        self.assertTrue(result['runtime_ready'])
        self.assertEqual(result['provider_status']['state'], 'unavailable')
        self.assertFalse(result['provider_status']['auth_ready'])
        self.assertFalse(result['provider_status']['execution_ready'])
        self.assertEqual(result['model_acceptance'], 'not_run')
        self.assertFalse(result['codex_daemon'])

    def test_durable_original_payload_precedes_post_and_identity_cannot_change(self):
        self.initialized()
        body, raw = self.payload()
        folder = self.config.runtime_root / 'cli-submissions' / body['client_request_id']
        def post(path, sent):
            self.assertEqual(path, '/api/review')
            self.assertEqual(json.loads(sent), body)
            self.assertEqual((folder / 'payload.json').read_bytes(), raw)
            self.assertTrue((folder / 'request.json').is_file())
            return {'state': 'completed', 'client_request_id': body['client_request_id']}
        with patch.object(cli.ConsoleSession, 'post', side_effect=post) as mocked:
            cli.submit(self.config, raw)
            cli.submit(self.config, json.dumps(body).encode('utf-8'))
            self.assertEqual(mocked.call_count, 2)
            changed = {**body, 'prompt': 'changed contents'}
            with self.assertRaisesRegex(cli.CLIError, 'request_conflict'):
                cli.submit(self.config, json.dumps(changed).encode('utf-8'))
            self.assertEqual(mocked.call_count, 2)
        self.assertEqual((folder / 'payload.json').read_bytes(), raw)

    def test_lost_response_retry_preserves_uuid_payload_and_failed_receipt(self):
        self.initialized()
        body, raw = self.payload()
        folder = self.config.runtime_root / 'cli-submissions' / body['client_request_id']
        failed = {'state': 'failed', 'client_request_id': body['client_request_id'], 'result': {'model_execution_verified': False}}
        with patch.object(cli.ConsoleSession, 'post', side_effect=cli.CLIError('uncertain fixture')):
            with self.assertRaises(cli.CLIError):
                cli.submit(self.config, raw)
        self.assertEqual(json.loads((folder / 'attempt.json').read_bytes())['state'], 'uncertain')
        with patch.object(cli.ConsoleSession, 'post', return_value=failed) as post:
            self.assertEqual(cli.retry(self.config, body['client_request_id']), failed)
            self.assertEqual(post.call_count, 1)
            self.assertEqual(json.loads(post.call_args.args[1]), body)
        self.assertEqual((folder / 'payload.json').read_bytes(), raw)
        self.assertEqual(json.loads((folder / 'receipt.json').read_bytes()), failed)
        self.assertEqual(len(list(folder.parent.iterdir())), 1)

    def test_failed_cli_exit_code_preserves_receipt(self):
        self.initialized()
        body, raw = self.payload()
        with patch.object(cli.ConsoleSession, 'post', return_value={'state': 'failed'}), contextlib.redirect_stdout(io.StringIO()):
            code = cli.main(['--config', str(self.config.path), 'submit', '--payload', raw.decode('utf-8')])
        self.assertEqual(code, 2)
        folder = self.config.runtime_root / 'cli-submissions' / body['client_request_id']
        self.assertEqual(json.loads((folder / 'receipt.json').read_bytes())['state'], 'failed')
        self.assertEqual((folder / 'payload.json').read_bytes(), raw)

    def test_normalized_allowed_paths_can_create_worker_settings(self):
        config = self.make_config('normalized', lambda b: b.update(worker={'allowed_paths': ['a/../b']}))
        initialize(config)
        settings = config.worker_settings()
        self.assertEqual(tuple(settings.allowed_paths), (str(config.project_root / 'b'),))

    def test_serve_rejects_changed_config_before_service_entry(self):
        self.initialized()
        with patch('relay_collaboration.service.serve') as serve, contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(['--config', str(self.config.path), 'serve', '--instance-id', str(uuid.uuid4()),
                             '--expected-config-digest', '0' * 64])
        self.assertEqual(code, 2)
        serve.assert_not_called()

    def test_rejects_endpoint_overlap_remote_origin_and_private_project_overlap(self):
        changes = [lambda b: b['endpoints'].update(console=b['endpoints']['broker']),
                   lambda b: b['endpoints'].update(broker='http://example.invalid:19041'),
                   lambda b: b['endpoints'].update(broker='http://user@127.0.0.1:19041'),
                   lambda b: b['endpoints'].update(broker='http://127.0.0.1:19041/rpc'),
                   lambda b: b['runtime'].update(root='../project/private'),
                   lambda b: b.update(schema_version=True), lambda b: b.update(unknown=True)]
        for index, mutate in enumerate(changes):
            with self.subTest(index=index), self.assertRaises(ConfigError):
                self.make_config('invalid-' + str(index), mutate)

    @unittest.skip('real_model_acceptance: not configured; fixtures never constitute real model acceptance')
    def test_real_model_acceptance_not_configured(self):
        pass


if __name__ == '__main__':
    unittest.main()
