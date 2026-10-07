"""Independent instance creation/selection, with no auth or runtime cloning."""
import json
import io
import os
from pathlib import Path
import shutil
import socket
import time
import unittest
import uuid
from unittest.mock import patch

from relay_collaboration.config import ConfigError, initialize, load_config
from relay_collaboration.instances import create, select, list_instances
from tests.test_lifecycle import new_config, free_ports


class InstanceTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / 'work' / ('instance-test-' + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.base = new_config(self.root)

    def test_create_two_independent_profiles_and_reject_duplicate(self):
        before = self.base.path.read_bytes()
        one = create(self.base.path, 'one')
        two = create(self.base.path, 'two')
        a, b = load_config(one['config']), load_config(two['config'])
        self.assertNotEqual(a.runtime_root, b.runtime_root)
        self.assertNotEqual(a.project_root, b.project_root)
        self.assertTrue({a.console_url, a.broker_url}.isdisjoint({b.console_url, b.broker_url}))
        self.assertFalse(a.runtime_root.exists())
        self.assertFalse(b.runtime_root.exists())
        self.assertEqual(before, self.base.path.read_bytes())
        self.assertEqual(a.codex_provider['kind'], 'codex_app')
        initialize(a); initialize(b)
        from relay_collaboration.config import require_runtime
        self.assertNotEqual(require_runtime(a)['runtime_id'], require_runtime(b)['runtime_id'])
        self.assertEqual(list(a.auth_root.iterdir()), [])
        self.assertEqual(list(b.auth_root.iterdir()), [])
        self.assertEqual(select(self.base.path, 'one'), a.path)
        self.assertEqual(len(list_instances(self.base.path)['instances']), 2)
        saved = a.path.read_bytes()
        with self.assertRaises(ConfigError): create(self.base.path, 'one')
        self.assertEqual(saved, a.path.read_bytes())

    def test_reserved_and_occupied_ports_preserve_existing(self):
        ports = free_ports()
        create(self.base.path, 'one', *ports)
        with self.assertRaises(ConfigError): create(self.base.path, 'two', *ports)
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', 0))
            occupied = listener.getsockname()[1]
            other = next(p for p in free_ports() if p != occupied)
            with self.assertRaises(ConfigError): create(self.base.path, 'three', occupied, other)
        self.assertFalse((self.root / 'instances' / 'three').exists())

    def test_names_missing_selector_and_partial_config_are_fail_closed(self):
        for name in ('../escape', 'CON', 'con', 'UPPER', 'a/b', 'a\\b', '', 'a' * 49):
            with self.assertRaises(ConfigError): create(self.base.path, name)
        with self.assertRaises(ConfigError): select(self.base.path, 'missing')
        partial = self.root / 'instances' / 'incomplete'
        partial.mkdir(parents=True)
        with self.assertRaises(ConfigError): create(self.base.path, 'valid')
        self.assertEqual(list_instances(self.base.path)['instances'][0]['state'], 'invalid_or_incomplete')

    def test_interrupted_creation_never_publishes_partial_name(self):
        with patch('relay_collaboration.instances.os.rename', side_effect=OSError('simulated interruption')):
            with self.assertRaises(OSError): create(self.base.path, 'one')
        self.assertFalse((self.root / 'instances' / 'one').exists())
        self.assertEqual(list_instances(self.base.path)['instances'], [])
        self.assertTrue(create(self.base.path, 'one')['ok'])
        with self.assertRaises(ConfigError): create(select(self.base.path, 'one'), 'nested')

    def test_empty_selector_recipient_guard_and_argument_errors_are_json(self):
        from relay_collaboration import cli
        path = self.root / 'wrong-recipient.json'
        path.write_text(json.dumps({'client_request_id': str(uuid.uuid4()), 'recipient': 'claude',
                                   'title': 'fixture', 'prompt': 'fixture', 'contexts': []}), encoding='utf-8')
        for arguments in (['--instance', '', 'status'], ['status', '--instance', 'one'],
                          ['submit', '--require-recipient', 'codex', '--payload', str(path)]):
            with patch('sys.stderr', new_callable=io.StringIO) as output, patch.object(cli, 'submit') as submit:
                self.assertEqual(cli.main(['--config', str(self.base.path), *arguments]), 2)
                self.assertFalse(json.loads(output.getvalue())['ok'])
                submit.assert_not_called()

    def test_creation_never_reads_runtime_or_auth(self):
        initialize(self.base)
        # A read guard, not a fake credential copied into a new profile.
        original = Path.read_bytes
        def guarded(path):
            if path.is_relative_to(self.base.runtime_root):
                raise AssertionError('instance creation must not read base runtime')
            return original(path)
        with patch.object(Path, 'read_bytes', guarded):
            result = create(self.base.path, 'fresh')
        self.assertFalse(load_config(result['config']).runtime_root.exists())

    def test_selector_rejects_runtime_alias_and_changed_cwd_does_not_change_selection(self):
        result = create(self.base.path, 'one')
        path = Path(result['config'])
        with patch('pathlib.Path.cwd', return_value=self.root / 'unrelated'):
            self.assertEqual(select(self.base.path, 'one'), path)
        value = json.loads(path.read_text(encoding='utf-8'))
        value['runtime']['root'] = str(self.base.runtime_root)
        path.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaises(ConfigError): select(self.base.path, 'one')

    def test_two_live_instances_bidirectional_cli_and_independent_stop(self):
        """Real hidden services/HTTP/CLI; the Claude result is an explicit fixture."""
        from relay_collaboration import cli, service
        from relay_collaboration.bridge_client import request
        from relay_collaboration.cli import ConsoleSession
        configs, owners, sessions = {}, {}, {}
        def command(name, *args, expected=0):
            out, err = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, {'CODEX_THREAD_ID': owners[name]}), \
                    patch('sys.stdout', out), patch('sys.stderr', err):
                code = cli.main(['--config', str(self.base.path), '--instance', name, *args])
            self.assertEqual(code, expected, err.getvalue())
            return json.loads(out.getvalue() if code == 0 else err.getvalue())
        def await_value(callback):
            end = time.monotonic() + 20
            while time.monotonic() < end:
                value = callback()
                if value: return value
                time.sleep(.1)
            self.fail('Fixture state did not settle')
        def rpc(name, actor, op, args):
            config = configs[name]
            value = request(config.broker_root, actor, op, args, url=config.broker_url,
                            request_id=str(uuid.uuid4()))
            self.assertTrue(value['ok'], value)
            return value['result']
        def saved(name, direction, request_id):
            path = self.root / (name + '-' + direction + '.json')
            path.write_text(json.dumps({'client_request_id': request_id,
                'recipient': 'claude' if direction == 'forward' else 'codex',
                'title': name + '-' + direction, 'prompt': 'Only ' + name + ' fixture context',
                'contexts': []}), encoding='utf-8')
            return str(path)
        for name in ('one', 'two'):
            result = create(self.base.path, name)
            path = Path(result['config'])
            raw = json.loads(path.read_text(encoding='utf-8'))
            raw['worker'].update(poll_seconds=.05, auth_retry_seconds=.1)
            path.write_text(json.dumps(raw), encoding='utf-8')
            configs[name] = load_config(path)
            owners[name] = str(uuid.uuid4())
            command(name, 'init')
            self.addCleanup(service.stop, configs[name], 15)
            self.assertTrue(command(name, 'start')['running'])
            self.assertTrue(command(name, 'app', 'attach')['connected'])
            sessions[name] = ConsoleSession(configs[name]).bootstrap()
        task_ids, receipts = {}, {}
        # Deliberately reuse a client UUID across runtimes: their broker scopes
        # are independent, while same-runtime retries must stay deduplicated.
        forward_id, reverse_id = str(uuid.uuid4()), str(uuid.uuid4())
        for name in ('one', 'two'):
            path = saved(name, 'forward', forward_id)
            first = command(name, 'app', 'send', '--file', path)
            self.assertTrue(command(name, 'app', 'send', '--file', path)['deduped'])
            self.assertEqual(first['request_id'], forward_id)
            task = await_value(lambda: next((x for x in sessions[name]._request('/api/tasks')['items']
                                            if x['title'] == name + '-forward'), None))
            task_ids[name] = task['task_id']
            claim = rpc(name, 'claude', 'task.claim', {'task_id': task['task_id'], 'ttl_seconds': 30})
            rpc(name, 'claude', 'task.complete', {'task_id': task['task_id'], 'claim_token': claim['claim_token'],
                'status': 'completed', 'result': {'ok': True, 'text': name + ' Claude FIXTURE', 'execution_kind': 'mock_test'}})
        self.assertNotEqual(task_ids['one'], task_ids['two'])
        for name, other in (('one', 'two'), ('two', 'one')):
            replies = await_value(lambda: command(name, 'app', 'inbox')['replies'])
            self.assertEqual([r['task_id'] for r in replies], [task_ids[name]])
            self.assertEqual(replies[0]['result']['text'], name + ' Claude FIXTURE')
            command(name, 'app', 'ack', '--task-id', task_ids[other], expected=2)
            self.assertFalse(command(name, 'app', 'ack', '--task-id', task_ids[name])['deduped'])
            self.assertTrue(command(name, 'app', 'ack', '--task-id', task_ids[name])['deduped'])
            self.assertEqual(command(name, 'app', 'inbox')['replies'], [])
            path = saved(name, 'reverse', reverse_id)
            command(name, 'submit', '--require-recipient', 'codex', '--payload', path)  # Claude-side public CLI.
            self.assertTrue(command(name, 'submit', '--payload', path)['deduped'])
            delivery = await_value(lambda: command(name, 'app', 'inbox')['requests'])[0]
            answer = self.root / (name + '-reply.txt')
            answer.write_text(name + ' App FIXTURE', encoding='utf-8')
            command(other, 'app', 'reply', '--delivery-id', delivery['delivery_id'], '--file', str(answer), expected=2)
            command(name, 'app', 'reply', '--delivery-id', delivery['delivery_id'], '--file', str(answer))
            terminal = await_value(lambda: next((x for x in sessions[name]._request('/api/tasks')['items']
                                      if x['title'] == name + '-reverse' and x['status'] == 'completed'), None))
            result = rpc(name, 'claude', 'task.get', {'task_id': terminal['task_id']})
            self.assertEqual(result['result']['text'], name + ' App FIXTURE')
            self.assertEqual(result['claim_attempt'], 1)
            observed = command(name, 'receipt', '--request-id', reverse_id)
            self.assertTrue(observed['read_only'])
            self.assertEqual(observed['result']['text'], name + ' App FIXTURE')
            self.assertEqual(observed['selection']['instance'], name)
            self.assertEqual(observed['selection']['runtime_root'], str(configs[name].runtime_root))
            # A Claude operator can resume/read the original dispatch receipt.
            receipts[name] = await_value(lambda: (r if (r := command(name, 'retry', '--request-id', reverse_id))['state'] == 'completed' else None))
        before = command('two', 'status')
        self.assertFalse(command('one', 'stop')['running'])
        after = command('two', 'status')
        self.assertEqual(before['instance_id'], after['instance_id'])
        self.assertTrue(after['running'])
        self.assertTrue(command('two', 'app', 'status')['connected'])
        self.assertTrue(command('one', 'start')['running'])
        self.assertEqual(command('one', 'app', 'inbox')['replies'], [])
        again = command('one', 'retry', '--request-id', reverse_id)
        self.assertEqual(again['task_id'], receipts['one']['task_id'])


if __name__ == '__main__':
    unittest.main()
