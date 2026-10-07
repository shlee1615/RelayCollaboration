"""Native lock semantics plus explicit POSIX-path injection on Windows.

Injected locker tests validate file opening/identity only; they do not qualify
Linux/macOS flock. Native POSIX permission/inode checks are skipped on Windows.
All processes and fixtures stay within the project's new work directory.
"""
from pathlib import Path
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from relay_collaboration import context_dispatch as dispatch
from tests.test_context_dispatch import FixtureBroker, Clock

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / 'work'


class LockTests(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='controller-lock-', dir=WORK)
        self.folder = Path(self.tmp.name).resolve()
        self.folder.relative_to(WORK.resolve())
        self.addCleanup(self.tmp.cleanup)
        self.first, self.second = self.folder / 'first.json', self.folder / 'second.json'

    def child_lock(self, path):
        source = ('from pathlib import Path; import sys\n'
                  'from relay_collaboration.context_dispatch import JournalLock, DispatchError\n'
                  'try:\n'
                  ' with JournalLock(Path(sys.argv[1])): print("acquired")\n'
                  'except DispatchError: print("blocked")\n')
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', source, str(path)],
                                cwd=ROOT, capture_output=True, text=True, timeout=10, check=True, **options)
        return result.stdout.strip()

    def test_native_distinct_journals_can_lock_concurrently(self):
        with dispatch.JournalLock(self.first):
            self.assertEqual(self.child_lock(self.second), 'acquired')

    def test_native_same_journal_blocks_other_process_then_releases(self):
        with dispatch.JournalLock(self.first):
            self.assertEqual(self.child_lock(self.first), 'blocked')
        self.assertEqual(self.child_lock(self.first), 'acquired')

    @unittest.skipUnless(os.name == 'posix', 'POSIX permissions and flock require a native POSIX host')
    def test_native_posix_lockfile_keeps_inode_and_private_mode(self):
        path = self.first.with_name(self.first.name + '.lock')
        with dispatch.JournalLock(self.first):
            info = path.stat()
            self.assertEqual(stat.S_IMODE(info.st_mode), 0o600)
        self.assertTrue(path.exists())
        with dispatch.JournalLock(self.first):
            self.assertEqual(path.stat().st_ino, info.st_ino)
            self.assertEqual(self.child_lock(self.first), 'blocked')

    @staticmethod
    def recorder():
        class RecordingLocker:
            LOCK_EX, LOCK_NB = 2, 4
            def __init__(self): self.calls = []
            def flock(self, fd, operation): self.calls.append((fd, operation))
        return RecordingLocker()

    def test_injected_posix_uses_distinct_stable_regular_lockfiles(self):
        locker = self.recorder()
        locks = [dispatch.JournalLock(path) for path in (self.first, self.second)]
        try:
            with patch.object(dispatch.os, 'open', wraps=os.open) as opening:
                for lock in locks:
                    lock._acquire_posix(locker)
            calls = opening.call_args_list
            self.assertEqual([call.args[0] for call in calls],
                             [path.with_name(path.name + '.lock') for path in (self.first, self.second)])
            self.assertTrue(all(call.args[2] == 0o600 for call in calls))
            for call in calls:
                self.assertTrue(call.args[1] & os.O_CREAT)
                if hasattr(os, 'O_NOFOLLOW'):
                    self.assertTrue(call.args[1] & os.O_NOFOLLOW)
            self.assertEqual(len(locker.calls), 2)
            identities = [os.fstat(lock.handle).st_ino for lock in locks]
            self.assertNotEqual(*identities)
        finally:
            for lock in locks: lock.__exit__()
        for path, inode in zip((self.first, self.second), identities):
            file = path.with_name(path.name + '.lock')
            self.assertEqual(file.stat().st_ino, inode)
            self.assertEqual(file.read_bytes(), b'')

    def test_injected_posix_rejects_hardlink_and_directory(self):
        source = self.folder / 'source'
        source.write_text('fixture', encoding='utf-8')
        lock_path = self.first.with_name(self.first.name + '.lock')
        os.link(source, lock_path)
        with self.assertRaises(dispatch.DispatchError):
            dispatch.JournalLock(self.first)._acquire_posix(self.recorder())
        # Only this fixture link is removed; the stable production lock is never removed.
        lock_path.unlink()
        lock_path.mkdir()
        with self.assertRaises(dispatch.DispatchError):
            dispatch.JournalLock(self.first)._acquire_posix(self.recorder())

    def test_injected_posix_rejects_symlink(self):
        source = self.folder / 'source'
        source.write_text('fixture', encoding='utf-8')
        lock_path = self.first.with_name(self.first.name + '.lock')
        try:
            lock_path.symlink_to(source)
        except OSError:
            self.skipTest('Host does not grant creation of fixture symlinks')
        with self.assertRaises(dispatch.DispatchError):
            dispatch.JournalLock(self.first)._acquire_posix(self.recorder())

    def test_injected_posix_closes_descriptor_on_flock_failure(self):
        locker = self.recorder()
        captured = []
        def fail(fd, _):
            captured.append(fd)
            raise BlockingIOError('fixture contention')
        locker.flock = fail
        lock = dispatch.JournalLock(self.first)
        with self.assertRaises(dispatch.DispatchError):
            lock._acquire_posix(locker)
        self.assertIsNone(lock.handle)
        with self.assertRaises(OSError):
            os.fstat(captured[0])
        self.assertTrue(self.first.with_name(self.first.name + '.lock').is_file())


class RetryBudgetTests(unittest.TestCase):
    def setUp(self):
        WORK.mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='controller-retries-', dir=WORK)
        self.folder = Path(self.tmp.name).resolve()
        self.folder.relative_to(WORK.resolve())
        self.addCleanup(self.tmp.cleanup)
        for name in ('private', 'broker', 'public'):
            (self.folder / name).mkdir()
        self.prompt = self.folder / 'prompt.md'
        self.prompt.write_text('Explicit test context; no model call.', encoding='utf-8')
        self.broker, self.clock = FixtureBroker(), Clock()

    def controller(self, request=None):
        return dispatch.Controller(prompt_file=self.prompt, name='retry-fixture', title='Retry fixture',
            output_dir=self.folder / 'public' / 'result', state_root=self.folder / 'private',
            broker_root=self.folder / 'broker', output_roots=(self.folder / 'public',),
            project='schematic', request_fn=request or self.broker.request,
            clock=self.clock, sleep=self.clock.sleep)

    def test_long_lived_round_above_old_limit_can_resume(self):
        controller = self.controller()
        with dispatch.JournalLock(controller.journal):
            controller._load()
            controller.data['round'] = 10001
            controller._save()
        self.broker.lease_held_once = True
        receipt = controller.run(timeout=10)
        self.assertEqual(receipt['status'], 'completed')
        saved = json.loads(controller.journal.read_text(encoding='utf-8'))
        self.assertEqual(saved['round'], 10002)
        self.assertEqual(len(self.broker.tasks), 1)
        self.assertEqual(self.controller().run()['task_id'], receipt['task_id'])

    def test_retry_budget_is_per_run_and_resumable_without_duplicate_submit(self):
        rejected = []
        def held(root, actor, op, args, **kwargs):
            self.assertEqual(op, 'lead.acquire')
            rejected.append(kwargs['request_id'])
            return self.broker.error('LEASE_HELD')
        controller = self.controller(held)
        with patch.object(dispatch, 'MAX_ACQUISITION_ROTATIONS', 2):
            for _ in range(2):
                with self.assertRaises(dispatch.ResumeLater):
                    controller.run(timeout=100)
        saved = json.loads(controller.journal.read_text(encoding='utf-8'))
        self.assertEqual(saved['round'], 5)
        self.assertEqual(len(rejected), 5)
        self.assertEqual(len(set(rejected)), len(rejected))
        self.assertFalse(self.broker.tasks)
        # Same name/journal safely rotates only the definitive failed acquire.
        receipt = self.controller().run(timeout=10)
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(len(self.broker.tasks), 1)


if __name__ == '__main__':
    unittest.main()
