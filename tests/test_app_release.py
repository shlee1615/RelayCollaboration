"""Operator release uses isolated fixtures, including real CLI/HTTP and crash recovery."""
import io
import json
import os
import threading
import unittest
import uuid
from unittest.mock import patch

from relay_collaboration.app_mailbox import AppMailbox, AppError
from relay_collaboration.cli import ConsoleSession, CLIError, main
from relay_collaboration.console_backend import Backend, ConsoleError
from tests import test_app_rebind
from tests.test_console import FakeObserver, FakeWorker


class ReleaseTests(unittest.TestCase):
    setUp = test_app_rebind.RebindTests.setUp
    stale = test_app_rebind.RebindTests.stale
    delivery = test_app_rebind.RebindTests.delivery

    def preview(self, **kwargs):
        return self.box.prepare_release(self.operator, 'User requested fixture handoff', **kwargs)

    def bound(self, stale=False):
        self.operator = str(uuid.uuid4())
        if stale:
            self.stale()
        else:
            self.box.attach(self.thread)

    def test_preview_does_not_change_binding_and_fresh_requires_explicit_override(self):
        self.bound()
        before = (self.box.root / 'binding.json').read_bytes()
        value = self.preview()
        self.assertFalse(value['can_release'])
        self.assertTrue(value['requires_allow_active'])
        self.assertEqual(before, (self.box.root / 'binding.json').read_bytes())
        with self.assertRaisesRegex(AppError, 'binding_not_stale'):
            self.box.release(self.operator, value['payload'])
        confirmed = self.preview(allow_active=True)
        self.assertTrue(confirmed['can_release'])
        self.assertIsNone(self.box.release(self.operator, confirmed['payload'])['successor_thread_id'])
        successor = str(uuid.uuid4())
        self.box.attach(successor)
        with self.assertRaisesRegex(AppError, 'different_app_task'):
            self.box.inbox(self.thread, include_replies=False)

    def test_stale_open_release_replay_does_not_release_next_owner(self):
        self.bound(stale=True)
        payload = self.preview()['payload']
        first = self.box.release(self.operator, payload)
        self.assertFalse(first['deduped'])
        self.box.attach(str(uuid.uuid4()))
        binding = (self.box.root / 'binding.json').read_bytes()
        self.assertTrue(AppMailbox(self.config).release(self.operator, payload)['deduped'])
        self.assertEqual(binding, (self.box.root / 'binding.json').read_bytes())
        with self.assertRaisesRegex(AppError, 'release_conflict'):
            self.box.release(self.thread, payload)

    def test_reserved_handoff_can_be_cleared_or_transferred_without_impersonation(self):
        self.bound(stale=True)
        first_target, final_target = str(uuid.uuid4()), str(uuid.uuid4())
        self.box.release(self.operator, self.preview(successor=first_target)['payload'])
        with self.assertRaisesRegex(AppError, 'handoff_reserved'):
            self.box.attach(final_target)
        transfer = self.preview(successor=final_target)['payload']
        self.box.release(self.operator, transfer)
        with self.assertRaisesRegex(AppError, 'handoff_reserved'):
            self.box.attach(first_target)
        clear = self.preview()['payload']
        self.box.release(self.operator, clear)
        self.box.attach(final_target)
        current = (self.box.root / 'binding.json').read_bytes()
        self.assertTrue(self.box.release(self.operator, transfer)['deduped'])
        self.assertEqual(current, (self.box.root / 'binding.json').read_bytes())

    def test_refreshed_generation_reservation_and_wrong_runtime_reject_snapshot(self):
        self.bound(stale=True)
        payload = self.preview()['payload']
        with self.assertRaisesRegex(AppError, 'release_profile_mismatch'):
            self.box.release(self.operator, payload | {'runtime_id': str(uuid.uuid4())})
        with self.assertRaisesRegex(AppError, 'release_profile_mismatch'):
            self.box.release(self.operator, payload | {'config_sha256': '0' * 64})
        self.box.inbox(self.thread, include_replies=False)
        with self.assertRaisesRegex(AppError, 'binding_changed'):
            self.box.release(self.operator, payload)
        payload = self.preview(allow_active=True)['payload']
        self.box.detach(self.thread)
        self.box.attach(self.thread)
        with self.assertRaisesRegex(AppError, 'binding_changed'):
            self.box.release(self.operator, payload)

    def test_deliveries_block_owner_detach_and_operator_release_even_with_override(self):
        self.bound()
        payload = self.preview(allow_active=True)['payload']
        for state in ('waiting', 'delivered', 'answered'):
            self.delivery(state)
        before = {p: p.read_bytes() for p in self.box.root.rglob('*.json')}
        for action in (lambda: self.box.release(self.operator, payload), lambda: self.box.detach(self.thread)):
            with self.assertRaisesRegex(AppError, 'release_blocked'):
                action()
        self.assertEqual(before, {p: p.read_bytes() for p in before})
        self.assertFalse(self.preview(allow_active=True)['can_release'])

    def test_all_release_writes_recover_before_attach_or_replay(self):
        self.bound()
        for failed_write in ('release-pending.json', 'binding.json', 'receipt'):
            with self.subTest(write=failed_write):
                payload = self.preview(allow_active=True)['payload']
                original = self.box._write
                def crash(name, value):
                    original(name, value)
                    if name == failed_write or (failed_write == 'receipt' and name.startswith('releases/')):
                        raise OSError('fixture crash after durable write')
                with patch.object(self.box, '_write', side_effect=crash):
                    with self.assertRaises(OSError):
                        self.box.release(self.operator, payload)
                recovered = AppMailbox(self.config)
                recovered.attach(self.thread)
                current = (self.box.root / 'binding.json').read_bytes()
                self.assertTrue(recovered.release(self.operator, payload)['deduped'])
                self.assertEqual(current, (self.box.root / 'binding.json').read_bytes())
                self.assertFalse((self.box.root / 'release-pending.json').exists())

    def test_strict_new_payload_and_old_api_do_not_accept_cross_version(self):
        self.bound()
        payload = self.preview(allow_active=True)['payload']
        for changes in ({'confirm_release': 1}, {'allow_active': 1}, {'successor_thread_id': '../bad'},
                        {'expected_binding_sha256': 'BAD'}, {'schema': 'unknown'}, {'extra': True}):
            with self.assertRaises(AppError):
                self.box.release(self.operator, payload | changes)
        with self.assertRaises(AppError):
            self.box.release_stale(self.operator, payload)

    def test_competing_operator_releases_cannot_both_change_reservation(self):
        self.bound(stale=True)
        payloads = [self.preview(successor=str(uuid.uuid4()))['payload'] for _ in range(2)]
        gate = threading.Barrier(2)
        results = []
        def run(payload):
            gate.wait()
            try:
                results.append(self.box.release(self.operator, payload))
            except AppError as exc:
                results.append(str(exc))
        threads = [threading.Thread(target=run, args=(p,)) for p in payloads]
        for thread in threads: thread.start()
        for thread in threads: thread.join(10)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
        self.assertIn('binding_changed', results)

    def test_cli_old_service_has_actionable_diagnostic_without_post_or_file(self):
        self.bound()
        path = self.root / 'release.json'
        session = ConsoleSession(self.config)
        with patch.dict(os.environ, {'CODEX_THREAD_ID': self.operator}), \
                patch.object(ConsoleSession, 'bootstrap', return_value=session), \
                patch.object(ConsoleSession, 'post') as post, \
                patch('sys.stdout', new_callable=io.StringIO), patch('sys.stderr', new_callable=io.StringIO) as err:
            self.assertEqual(main(['--config', str(self.config.path), 'app', 'release', '--file', str(path),
                                   '--reason', 'fixture release']), 2)
            self.assertIn('service_upgrade_required', err.getvalue())
            post.assert_not_called()
            self.assertFalse(path.exists())

    def test_real_service_cli_preview_confirm_handoff_and_idempotent_retry(self):
        from relay_collaboration import service
        self.bound()
        self.addCleanup(service.stop, self.config, 15)
        self.assertTrue(service.start(self.config)['running'])
        path = self.root / 'release.json'
        target = str(uuid.uuid4())
        def command(*args, expected=0, owner=None):
            out, err = io.StringIO(), io.StringIO()
            with patch.dict(os.environ, {'CODEX_THREAD_ID': owner or self.operator}), \
                    patch('sys.stdout', out), patch('sys.stderr', err):
                code = main(['--config', str(self.config.path), 'app', *args])
            self.assertEqual(code, expected, err.getvalue())
            return json.loads(out.getvalue() if code == 0 else err.getvalue())
        before = (self.box.root / 'binding.json').read_bytes()
        preview = command('release', '--file', str(path), '--reason', 'fixture handoff',
                          '--allow-active', '--successor-thread-id', target)
        self.assertTrue(preview['requires_confirmation'])
        self.assertEqual(before, (self.box.root / 'binding.json').read_bytes())
        saved = path.read_bytes()
        command('release', '--file', str(path), '--reason', 'must not overwrite', expected=2)
        self.assertEqual(saved, path.read_bytes())
        command('release', '--file', str(path), '--confirm', '--allow-active', expected=2)
        first = command('release', '--file', str(path), '--confirm')
        self.assertFalse(first['deduped'])
        self.assertEqual(first['successor_thread_id'], target)
        self.assertIn('handoff_reserved', command('attach', expected=2)['error'])
        command('attach', owner=target)
        self.assertTrue(command('release', '--file', str(path), '--confirm')['deduped'])
        self.assertEqual(command('status')['binding_relation'], 'another_task')
        self.assertEqual(command('status', owner=target)['binding_relation'], 'owner')
        command('detach', owner=target)


if __name__ == '__main__':
    unittest.main()
