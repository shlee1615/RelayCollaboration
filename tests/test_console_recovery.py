"""Recovery fault fixtures: preserve evidence, reserve UUIDs, and never invoke models."""
import json
import contextlib
import io
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import uuid

from relay_collaboration import context_dispatch, cli
from relay_collaboration.console_backend import Backend, Reviews, ConsoleError, atomic, canonical

WORK = Path(__file__).resolve().parents[1] / 'work'


class RecoveryObserver:
    dispatcher = context_dispatch
    client = SimpleNamespace(request=lambda *args, **kwargs: None)

    def get(self, kind, **kwargs):
        return {'server_time': 1, 'leader': {'actor': None, 'active': False},
                'counts': dict.fromkeys(('pending', 'running', 'completed', 'failed', 'attention', 'total'), 0)}


class ConsoleRecoveryTests(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=WORK, prefix='console-recovery-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.root.resolve().relative_to(WORK.resolve())
        self.observer = RecoveryObserver()
        self.backend = Backend(self.root / 'runtime', self.root / 'output', self.observer,
                               SimpleNamespace(snapshot=lambda: {'state': 'unavailable'}), execute=False)
        self.reviews = self.backend.reviews
        self.run_config = SimpleNamespace(controller_root=self.reviews.state_root, broker_root=self.root / 'broker',
                                          project='recovery-fixture', transport='file', broker_url='http://127.0.0.1:19091')

    def body(self):
        return {'client_request_id': str(uuid.uuid4()), 'title': 'Recovery fixture',
                'prompt': 'Only controlled test data; no provider execution.', 'contexts': []}

    def submit(self):
        body = self.body()
        self.reviews.submit(body)
        return body

    def folder(self, body):
        return self.reviews.runtime / 'submissions' / body['client_request_id']

    def disk_job(self, body):
        return json.loads((self.folder(body) / 'job.json').read_text(encoding='utf-8'))

    def restart(self):
        self.reviews = Reviews(self.reviews.runtime, self.reviews.output_root, self.observer, execute=False)
        self.backend.reviews = self.reviews
        return self.reviews

    def assert_blocked(self, reviews, body):
        for action in (lambda: reviews.submit(body), lambda: reviews.retry(body['client_request_id'])):
            with self.assertRaises(ConsoleError) as caught:
                action()
            self.assertEqual(caught.exception.status, 409)
            self.assertEqual(caught.exception.code, 'submission_recovery_required')

    def run_failure(self, body, exception, constructor=False):
        self.reviews.config = self.run_config
        self.reviews.active.add(body['client_request_id'])
        controller = Mock()
        controller.run.side_effect = exception
        options = {'side_effect': exception} if constructor else {'return_value': controller}
        with patch.object(context_dispatch, 'Controller', **options):
            self.reviews.run(body['client_request_id'])
        self.assertNotIn(body['client_request_id'], self.reviews.active)
        return self.reviews.jobs[body['client_request_id']]

    def test_corrupt_records_are_preserved_and_do_not_prevent_healthy_recovery(self):
        healthy = self.submit()
        malformed = []
        for raw in (b'{bad json', b'{}', b'x' * 300001):
            body = self.submit()
            path = self.folder(body) / 'job.json'
            path.write_bytes(raw)
            malformed.append((body, path, raw))
        restored = self.restart()
        self.assertEqual(set(restored.jobs), {healthy['client_request_id']})
        self.assertEqual(restored.jobs[healthy['client_request_id']]['state'], 'uncertain')
        self.assertEqual(restored.recovery_issues, 3)
        for body, path, raw in malformed:
            self.assertEqual(path.read_bytes(), raw)
            self.assert_blocked(restored, body)
        summary = self.backend.summary()
        self.assertTrue(summary['broker']['ok'])
        self.assertEqual(summary['submission_recovery'], {'state': 'attention', 'count': 3})
        self.assertEqual(restored.submit(self.body())['state'], 'dispatching')

    def test_folder_mismatch_reserves_both_record_and_folder_uuid(self):
        body = self.submit()
        other = self.body()
        job = self.disk_job(body)
        job['request_id'] = other['client_request_id']
        path = self.folder(body) / 'job.json'
        raw = canonical(job)
        path.write_bytes(raw)
        restored = self.restart()
        self.assertFalse(restored.jobs)
        self.assertEqual(restored.recovery_issues, 1)
        self.assert_blocked(restored, body)
        self.assert_blocked(restored, other)
        self.assertEqual(path.read_bytes(), raw)

    def test_orphan_submission_folder_is_preserved_and_reserved(self):
        body = self.body()
        path = self.folder(body) / 'payload.json'
        atomic(path, canonical(body))
        restored = self.restart()
        self.assert_blocked(restored, body)
        self.assertEqual(path.read_bytes(), canonical(body))
        self.assertFalse((path.parent / 'job.json').exists())
        self.assertEqual(restored.recovery()['count'], 1)

    def test_unhashable_metadata_and_payload_mismatch_are_isolated(self):
        broken = []
        for kind in ('state', 'payload', 'prompt', 'created_at'):
            body = self.submit()
            if kind in ('state', 'created_at'):
                path = self.folder(body) / 'job.json'
                job = self.disk_job(body)
                job[kind] = [] if kind == 'state' else float('nan')
                path.write_text(json.dumps(job), encoding='utf-8')
            elif kind == 'payload':
                path = self.folder(body) / 'payload.json'
                path.write_bytes(canonical({**body, 'prompt': 'Changed after acceptance'}))
            else:
                path = self.folder(body) / 'prompt.md'
                path.write_bytes(b'Changed after acceptance')
            broken.append((body, path, path.read_bytes()))
        restored = self.restart()
        self.assertEqual(restored.recovery()['count'], 4)
        for body, path, raw in broken:
            self.assert_blocked(restored, body)
            self.assertEqual(path.read_bytes(), raw)

    def test_restart_uncertain_transition_and_original_task_id_are_durable(self):
        body = self.submit()
        job = self.reviews.jobs[body['client_request_id']]
        job.update(state='queued', task_id=str(uuid.uuid4()))
        self.reviews.save(job)
        restored = self.restart()
        value = restored.jobs[body['client_request_id']]
        self.assertEqual(value['state'], 'uncertain')
        self.assertTrue(value['retryable'])
        self.assertEqual(value['task_id'], job['task_id'])
        self.assertEqual(self.disk_job(body), value)

    def test_resume_later_is_resumable_and_preserves_identity(self):
        body = self.submit()
        job = self.run_failure(body, context_dispatch.ResumeLater('SENTINEL_RAW_SECRET'))
        self.assertEqual(job['state'], 'uncertain')
        self.assertTrue(job['retryable'])
        self.assertNotIn('SENTINEL_RAW_SECRET', canonical(job).decode())
        self.assertEqual(self.disk_job(body), job)
        with patch.object(self.reviews, 'launch') as launch:
            returned = self.reviews.retry(body['client_request_id'])
            launch.assert_called_once_with(body['client_request_id'])
        self.assertEqual(returned['request_id'], body['client_request_id'])
        self.assertEqual(len(self.reviews.jobs), 1)
        original_task_id = str(uuid.uuid4())
        job = self.reviews.jobs[body['client_request_id']]
        self.reviews._transition(job, 'uncertain', 'Fixture interrupted wait', True, task_id=original_task_id)
        with patch.object(self.reviews, 'launch'):
            continued = self.reviews.retry(body['client_request_id'])
        self.assertEqual(continued['state'], 'queued')
        self.assertEqual(continued['task_id'], original_task_id)
        self.assertEqual(self.disk_job(body)['state'], 'queued')

    def test_broker_rejected_is_terminal_and_cannot_be_relaunched(self):
        body = self.submit()
        job = self.run_failure(body, context_dispatch.BrokerRejected('SENTINEL_RAW_SECRET'))
        self.assertEqual(job['state'], 'failed')
        self.assertFalse(job['retryable'])
        self.assertNotIn('SENTINEL_RAW_SECRET', job['error'])
        with patch.object(self.reviews, 'launch') as launch:
            for value in (self.reviews.retry(body['client_request_id']), self.reviews.submit(body)):
                self.assertEqual(value['state'], 'failed')
                self.assertFalse(value['retryable'])
            launch.assert_not_called()

    def test_dispatch_error_requires_manual_review_across_restart(self):
        body = self.submit()
        job = self.run_failure(body, context_dispatch.DispatchError('SENTINEL_RAW_SECRET'), constructor=True)
        self.assertEqual(job['state'], 'manual_review')
        self.assertFalse(job['retryable'])
        self.assertNotIn('SENTINEL_RAW_SECRET', job['error'])
        restored = self.restart()
        self.assertEqual(restored.jobs[body['client_request_id']]['state'], 'manual_review')
        self.assertEqual(restored.recovery(), {'state': 'attention', 'count': 1})
        with patch.object(restored, 'launch') as launch:
            for value in (restored.retry(body['client_request_id']), restored.submit(body)):
                self.assertEqual(value['state'], 'manual_review')
                self.assertFalse(value['retryable'])
            launch.assert_not_called()

    def test_unexpected_controller_failure_is_not_blindly_retried(self):
        body = self.submit()
        job = self.run_failure(body, RuntimeError('SENTINEL_RAW_SECRET'))
        self.assertEqual(job['state'], 'manual_review')
        self.assertFalse(job['retryable'])
        self.assertNotIn('SENTINEL_RAW_SECRET', job['error'])

    def test_terminal_receipt_preserves_task_identity_and_result_status(self):
        self.reviews.config = self.run_config
        for model_ok, expected in ((True, 'completed'), (False, 'failed')):
            body = self.submit()
            task_id = str(uuid.uuid4())
            controller = Mock()
            controller.run.return_value = {'task_id': task_id, 'status': 'completed', 'result': {'ok': model_ok}}
            with patch.object(context_dispatch, 'Controller', return_value=controller):
                self.reviews.run(body['client_request_id'])
            job = self.disk_job(body)
            self.assertEqual(job['state'], expected)
            self.assertEqual(job['task_id'], task_id)
            self.assertFalse(job['retryable'])

    def test_corrupt_controller_snapshot_is_preserved_and_transition_saved(self):
        body = self.submit()
        journal = self.reviews.state_root / ('controller-gui-' + body['client_request_id'] + '.json')
        journal.write_bytes(b'{bad controller journal')
        value = self.reviews.snapshots()[0]
        self.assertEqual(value['state'], 'manual_review')
        self.assertFalse(value['retryable'])
        self.assertEqual(self.disk_job(body)['state'], 'manual_review')
        self.assertEqual(journal.read_bytes(), b'{bad controller journal')
        self.assertEqual(self.reviews.recovery()['count'], 1)
        self.assertEqual(self.reviews.snapshots()[0]['state'], 'manual_review')
        with patch.object(self.reviews, 'launch') as launch:
            self.reviews.retry(body['client_request_id'])
            launch.assert_not_called()

    def test_snapshot_save_failure_is_reported_and_prevents_retry(self):
        body = self.submit()
        old = (self.folder(body) / 'job.json').read_bytes()
        journal = self.reviews.state_root / ('controller-gui-' + body['client_request_id'] + '.json')
        journal.write_bytes(b'[]')
        with patch.object(self.reviews, 'save', side_effect=OSError('SENTINEL_RAW_SECRET')):
            with self.assertRaises(ConsoleError) as caught:
                self.reviews.snapshots()
        self.assertEqual(caught.exception.status, 503)
        self.assertNotIn('SENTINEL_RAW_SECRET', caught.exception.message)
        self.assertEqual((self.folder(body) / 'job.json').read_bytes(), old)
        self.assert_blocked(self.reviews, body)

    def test_restart_save_failure_preserves_original_and_reserves_uuid(self):
        body = self.submit()
        old = (self.folder(body) / 'job.json').read_bytes()
        with patch.object(Reviews, 'save', side_effect=OSError('read-only fixture')):
            restored = self.restart()
        self.assert_blocked(restored, body)
        self.assertEqual((self.folder(body) / 'job.json').read_bytes(), old)
        self.assertEqual(restored.recovery()['count'], 1)

    def test_activity_escape_expansion_cannot_poison_readback(self):
        original = self.backend.report({'title': 'Ready', 'phase': 'fixture', 'detail': '', 'ttl_s': 30})
        path = self.reviews.runtime / 'codex-activity.json'
        old = path.read_bytes()
        for text in ('\x01' * 2000, '"' * 2000, '\\' * 2000):
            with self.assertRaises(ConsoleError) as caught:
                self.backend.report({'title': text, 'phase': text, 'detail': text, 'ttl_s': 30})
            self.assertEqual(caught.exception.status, 413)
            self.assertEqual(path.read_bytes(), old)
        self.assertEqual(self.backend.activity()['reported_at'], original['reported_at'])
        self.backend.report({'title': '\x01' * 400, 'phase': '\x02' * 400, 'detail': '\x03' * 400, 'ttl_s': 30})
        self.assertLessEqual(path.stat().st_size, 8192)
        self.assertEqual(self.backend.activity()['state'], 'reported')

    def test_initial_save_failure_reserves_payload_before_restart(self):
        body = self.body()
        with patch.object(self.reviews, 'save', side_effect=OSError('SENTINEL_RAW_SECRET')), \
                patch.object(self.reviews, 'launch') as launch:
            with self.assertRaises(ConsoleError) as caught:
                self.reviews.submit(body)
            launch.assert_not_called()
        self.assertEqual(caught.exception.status, 503)
        payload = self.folder(body) / 'payload.json'
        self.assertEqual(payload.read_bytes(), canonical(body))
        self.assert_blocked(self.reviews, body)
        self.assert_blocked(self.restart(), body)
        self.assertEqual(payload.read_bytes(), canonical(body))

    def test_invalid_folder_name_reserves_identifiable_saved_uuid(self):
        body = self.submit()
        folder = self.reviews.runtime / 'submissions' / 'invalid-folder-name'
        folder.mkdir()
        for name in ('job.json', 'payload.json', 'prompt.md'):
            (folder / name).write_bytes((self.folder(body) / name).read_bytes())
        original = (folder / 'job.json').read_bytes()
        restored = self.restart()
        self.assert_blocked(restored, body)
        self.assertEqual((folder / 'job.json').read_bytes(), original)

    def test_manual_review_cli_is_failure_exit_and_does_not_retry_post(self):
        body = self.submit()
        config = SimpleNamespace(runtime_root=self.reviews.runtime)
        folder = self.folder(body)
        for result in ({'state': 'manual_review', 'retryable': False},
                       {'state': 'uncertain', 'retryable': False}):
            session = Mock()
            session.post.return_value = result
            with patch.object(cli, 'require_runtime'), \
                    patch.object(cli, '_record_payload', return_value=folder), \
                    patch.object(cli, 'ConsoleSession', return_value=session):
                self.assertEqual(cli.submit(config, canonical(body), resume=True), result)
                session.post.assert_called_once()
        with patch.object(cli, 'load_config', return_value=config), \
                patch.object(cli, 'submit', return_value={'state': 'manual_review', 'retryable': False}), \
                patch.object(cli, '_selection', return_value={'instance': 'fixture'}), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cli.main(['submit', '--payload', canonical(body).decode('utf-8')]), 2)

    def test_atomic_creates_exclusive_owner_private_temp(self):
        path = self.reviews.runtime / 'private-atomic.json'
        real_open = os.open
        with patch('relay_collaboration.console_backend.os.open', wraps=real_open) as opened:
            atomic(path, b'private fixture')
        self.assertEqual(opened.call_args.args[2], 0o600)
        self.assertTrue(opened.call_args.args[1] & os.O_EXCL)
        self.assertTrue(opened.call_args.args[1] & os.O_CREAT)
        self.assertEqual(path.read_bytes(), b'private fixture')
        if os.name != 'nt':
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)


if __name__ == '__main__':
    unittest.main()
