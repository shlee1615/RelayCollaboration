"""Stale recovery tests use fresh fixture runtimes, never real task identities."""
import io
import json
import os
import threading
import time
import unittest
import uuid
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from relay_collaboration.app_mailbox import AppMailbox, AppError, digest
from relay_collaboration.cli import ConsoleSession, CLIError, main
from relay_collaboration.console_backend import Backend, ConsoleError
from tests import test_app_mailbox
from tests.test_console import FakeObserver, FakeWorker


class RebindTests(unittest.TestCase):
    setUp = test_app_mailbox.AppMailboxTests.setUp

    def stale(self):
        self.box.attach(self.thread)
        with self.box._lock():
            value = self.box._read('binding.json')
            value['last_seen_at'] -= 1000
            self.box._write('binding.json', value)
        status = self.box.status()
        return {'request_id': str(uuid.uuid4()), 'binding_id': status['binding_id'],
                'last_seen_at': status['last_seen_at'], 'reason': 'User requested stale recovery',
                'confirm_release': True}

    def delivery(self, state):
        task = {'task_id': str(uuid.uuid4()), 'prompt': 'fixture only'}
        delivery_id = str(uuid.uuid4())
        value = {'binding_id': self.box.status()['binding_id'], 'delivery_id': delivery_id,
                 'task': task, 'task_sha256': digest(task), 'state': state,
                 'created_at': time.time() - 1000, 'deadline_epoch': time.time() - 1}
        if state in ('answered', 'consumed'):
            value['result'] = {'text': 'original fixture reply'}
        with self.box._lock():
            self.box._write('deliveries/' + delivery_id + '.json', value)
        return delivery_id

    def test_release_retry_after_restart_and_successor_is_fenced(self):
        payload = self.stale()
        successor = str(uuid.uuid4())
        delivery_id = self.delivery('consumed')
        delivery_path = self.box.root / 'deliveries' / (delivery_id + '.json')
        before = delivery_path.read_bytes()
        first = self.box.release_stale(successor, payload)
        self.assertFalse(first['deduped'])
        with self.assertRaisesRegex(AppError, 'handoff_reserved'):
            self.box.attach(self.thread)
        box = AppMailbox(self.config)
        box.attach(successor)
        binding = (box.root / 'binding.json').read_bytes()
        self.assertNotEqual(box.status()['binding_id'], payload['binding_id'])
        again = box.release_stale(successor, payload)
        self.assertTrue(again['deduped'])
        self.assertEqual(first['released_at'], again['released_at'])
        self.assertEqual(binding, (box.root / 'binding.json').read_bytes())
        self.assertEqual(before, delivery_path.read_bytes())
        for owner in (successor, self.thread):
            with self.assertRaises(AppError):
                box.reply(owner, delivery_id, 'must not replace old delivery')
        for op in (box.attach, box.detach, lambda t: box.inbox(t, include_replies=False)):
            with self.assertRaises(AppError):
                op(self.thread)

    def test_fresh_owner_and_future_clock_are_not_releasable(self):
        payload = self.stale()
        for offset in (0, 100):
            with self.box._lock():
                value = self.box._read('binding.json')
                value['last_seen_at'] = time.time() + offset
                self.box._write('binding.json', value)
            payload['last_seen_at'] = value['last_seen_at']
            with self.assertRaisesRegex(AppError, 'binding_not_stale'):
                self.box.release_stale(str(uuid.uuid4()), payload)

    def test_refresh_after_snapshot_and_generation_change_reject_release(self):
        payload = self.stale()
        self.box.inbox(self.thread, include_replies=False)
        with self.assertRaisesRegex(AppError, 'binding_changed'):
            self.box.release_stale(str(uuid.uuid4()), payload)
        self.box.detach(self.thread)
        self.box.attach(self.thread)
        with self.assertRaisesRegex(AppError, 'binding_changed'):
            self.box.release_stale(str(uuid.uuid4()), payload)

    def test_stale_does_not_allow_ordinary_attach_or_foreign_detach(self):
        self.stale()
        for op in (self.box.attach, self.box.detach):
            with self.assertRaisesRegex(AppError, 'different_app_task'):
                op(str(uuid.uuid4()))
        self.box.detach(self.thread)
        self.box.attach(str(uuid.uuid4()))

    def test_unresolved_even_overdue_deliveries_block_without_modification(self):
        payload = self.stale()
        for state in ('waiting', 'delivered', 'answered'):
            self.delivery(state)
        originals = {p: p.read_bytes() for p in (self.box.root / 'deliveries').glob('*.json')}
        self.assertEqual(self.box.status()['release_blockers'], {'waiting': 1, 'delivered': 1, 'answered': 1})
        with self.assertRaisesRegex(AppError, 'release_blocked'):
            self.box.release_stale(str(uuid.uuid4()), payload)
        self.assertEqual(originals, {p: p.read_bytes() for p in originals})
        self.assertFalse((self.box.root / 'release-pending.json').exists())

    def test_changed_payload_caller_and_target_do_not_reuse_receipt(self):
        payload = self.stale()
        successor = str(uuid.uuid4())
        self.box.release_stale(successor, payload)
        for changes in ({'reason': 'changed'}, {'binding_id': str(uuid.uuid4())}, {'last_seen_at': 1}):
            with self.assertRaisesRegex(AppError, 'release_conflict'):
                self.box.release_stale(successor, payload | changes)
        with self.assertRaisesRegex(AppError, 'release_conflict'):
            self.box.release_stale(str(uuid.uuid4()), payload)

    def test_explicit_confirmation_and_strict_fields(self):
        payload = self.stale()
        for changes in ({'confirm_release': False}, {'confirm_release': 1}, {'reason': ''},
                        {'last_seen_at': float('nan')}, {'last_seen_at': True},
                        {'binding_id': '../binding'}, {'extra': 'field'}):
            with self.assertRaises(AppError):
                self.box.release_stale(str(uuid.uuid4()), payload | changes)

    def test_crash_after_each_write_recovers_once_and_reserves_successor(self):
        for failed_write in ('release-pending.json', 'binding.json', 'receipt'):
            with self.subTest(failed_write=failed_write):
                payload = self.stale()
                successor = str(uuid.uuid4())
                original = self.box._write
                def crash(name, value):
                    original(name, value)
                    if name == failed_write or (failed_write == 'receipt' and name.startswith('releases/')):
                        raise OSError('simulated process loss after durable write')
                with patch.object(self.box, '_write', side_effect=crash):
                    with self.assertRaises(OSError):
                        self.box.release_stale(successor, payload)
                recovered = AppMailbox(self.config)
                with self.assertRaisesRegex(AppError, 'handoff_reserved'):
                    recovered.attach(self.thread)
                self.assertTrue(recovered.release_stale(successor, payload)['deduped'])
                recovered.attach(successor)
                self.assertEqual(recovered.status()['thread_id'], successor)
                self.assertFalse((recovered.root / 'release-pending.json').exists())
                recovered.detach(successor)

    def test_two_competing_releases_only_one_commits(self):
        payload = self.stale()
        gate = threading.Barrier(2)
        results = []
        def run():
            owner = str(uuid.uuid4())
            gate.wait()
            try:
                results.append(self.box.release_stale(owner, payload | {'request_id': str(uuid.uuid4())}))
            except AppError as exc:
                results.append(str(exc))
        threads = [threading.Thread(target=run) for _ in range(2)]
        for t in threads: t.start()
        for t in threads: t.join(10)
        self.assertTrue(all(not t.is_alive() for t in threads))
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        self.assertIn('binding_changed', results)

    def test_api_send_requires_owner_and_preserves_review_uuid(self):
        payload = self.stale()
        backend = Backend(self.config.console_root, self.config.output_root, FakeObserver(), FakeWorker(),
                          execute=False, config=self.config)
        request = {'client_request_id': str(uuid.uuid4()), 'recipient': 'claude',
                   'title': 'fixture', 'prompt': 'fixture only', 'contexts': []}
        body = {'command': 'send', 'actor': 'codex', 'thread_id': self.thread, 'payload': request}
        first = backend.app_command(body)
        # Sending refreshes the owner heartbeat, so recovery needs a fresh stale snapshot.
        payload = self.stale()
        successor = str(uuid.uuid4())
        backend.app_command({'command': 'release-stale', 'actor': 'codex', 'thread_id': successor, 'payload': payload})
        with self.assertRaises(ConsoleError): backend.app_command(body)
        self.box.attach(successor)
        with self.assertRaises(ConsoleError): backend.app_command(body)
        body['thread_id'] = successor
        second = backend.app_command(body)
        self.assertTrue(second['deduped'])
        self.assertEqual(first['request_id'], second['request_id'])
        body['payload'] = request | {'prompt': 'changed'}
        with self.assertRaises(ConsoleError): backend.app_command(body)
        self.assertEqual(len(backend.reviews.jobs), 1)

    def test_cli_uses_current_identity_and_app_endpoint_for_saved_operations(self):
        payload = self.stale()
        path = self.root / 'release.json'
        path.write_text(json.dumps(payload), encoding='utf-8')
        session = ConsoleSession(self.config)
        with patch.dict(os.environ, {'CODEX_THREAD_ID': self.thread}), \
                patch.object(ConsoleSession, 'bootstrap', return_value=session), \
                patch.object(ConsoleSession, 'post', return_value={'ok': True}) as post, \
                patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO):
            self.assertEqual(main(['--config', str(self.config.path), 'app', 'release-stale', '--file', str(path)]), 0)
            route, raw = post.call_args.args
            self.assertEqual(route, '/api/app')
            self.assertEqual(json.loads(raw)['thread_id'], self.thread)
            self.assertEqual(json.loads(raw)['payload'], payload)
            post.reset_mock()
            self.assertEqual(main(['--config', str(self.config.path), 'app', '--thread-id', str(uuid.uuid4()),
                                   'release-stale', '--file', str(path)]), 2)
            post.assert_not_called()

    def test_cli_error_is_allowlisted_and_never_reflects_server_body(self):
        session = ConsoleSession(self.config)
        for body, expected in [
            ({'error': {'code': 'different_app_task', 'message': 'PRIVATE_SECRET'}}, 'different_app_task'),
            ({'error': {'code': 'app_command_rejected', 'message': 'different_app_task: PRIVATE_SECRET'}}, 'different_app_task'),
            ({'error': {'code': 'unknown', 'message': 'PRIVATE_SECRET'}}, 'inspect app status'),
            ({'error': []}, 'inspect app status'),
        ]:
            error = HTTPError(session.origin, 409, 'Conflict', {}, io.BytesIO(json.dumps(body).encode()))
            with patch.object(session.opener, 'open', side_effect=error):
                with self.assertRaises(CLIError) as caught: session._request('/api/app', b'{}')
            self.assertIn(expected, str(caught.exception))
            self.assertNotIn('PRIVATE_SECRET', str(caught.exception))
            self.assertNotIn('retain the same UUID and payload', str(caught.exception))
        with patch.object(session.opener, 'open', side_effect=URLError('PRIVATE_SECRET')):
            with self.assertRaisesRegex(CLIError, 'original operation'):
                session._request('/api/app', b'{}')


if __name__ == '__main__':
    unittest.main()
