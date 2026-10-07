"""Relocatable skill bindings, conflict preservation and real PowerShell forwarding."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
from relay_collaboration import skill_install as installer


class SkillInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'work', prefix='共用 技能 ')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.package('套件 A')
        self.config = self.root / 'relay.local.json'
        self.targets = {'codex': self.base / '使用者/.codex/skills', 'claude': self.base / '使用者/.claude/skills'}

    def package(self, name):
        root = self.base / name
        for folder in ('skills/relay-codex-app', 'skills/relay-claude-code', 'scripts'):
            (root / folder).mkdir(parents=True)
        for name in ('relay.py', 'scripts/skill-command.ps1', 'skills/relay-codex-app/SKILL.md',
                     'skills/relay-claude-code/SKILL.md'):
            shutil.copyfile(ROOT / name, root / name)
        shutil.copytree(ROOT / 'relay_collaboration', root / 'relay_collaboration', ignore=shutil.ignore_patterns('__pycache__'))
        shutil.copyfile(ROOT / 'config/codex-app.example.json', root / 'relay.local.json')
        return root

    def install(self, **kwargs):
        return installer.install(self.root, self.config, Path(sys.executable), self.targets, **kwargs)

    def skill(self, agent):
        return self.targets[agent] / installer.NAMES[agent]

    def test_bindings_relocate_and_repeat_does_not_rewrite(self):
        self.assertEqual([r['state'] for r in self.install()], ['installed', 'installed'])
        before = {str(f): (f.read_bytes(), f.stat().st_mtime_ns) for t in self.targets for f in self.skill(t).iterdir()}
        self.assertEqual([r['state'] for r in self.install()], ['unchanged', 'unchanged'])
        after = {str(f): (f.read_bytes(), f.stat().st_mtime_ns) for t in self.targets for f in self.skill(t).iterdir()}
        self.assertEqual(before, after)
        for agent in self.targets:
            binding = json.loads((self.skill(agent) / 'installation.json').read_text(encoding='utf-8'))
            self.assertEqual(binding['package_root'], str(self.root))
            self.assertEqual(binding['config'], str(self.config))
            self.assertEqual(binding['agent'], agent)
        self.assertFalse((self.root / '.relay-private').exists())

    def test_conflicting_second_destination_prevents_first_write(self):
        self.skill('claude').mkdir(parents=True)
        foreign = self.skill('claude') / 'SKILL.md'
        foreign.write_text('user owned', encoding='utf-8')
        with self.assertRaisesRegex(installer.InstallError, 'unmanaged'):
            self.install()
        self.assertFalse(self.skill('codex').exists())
        self.assertEqual(foreign.read_text(encoding='utf-8'), 'user owned')

    def test_modified_managed_skill_is_preserved(self):
        self.install()
        path = self.skill('codex') / 'SKILL.md'
        path.write_text('local edits', encoding='utf-8')
        with self.assertRaisesRegex(installer.InstallError, 'modified'):
            self.install(rebind=True)
        self.assertEqual(path.read_text(encoding='utf-8'), 'local edits')

    def test_rebinding_requires_explicit_switch_and_creates_no_runtime(self):
        self.install()
        self.root = self.package('套件 B')
        self.config = self.root / 'relay.local.json'
        with self.assertRaisesRegex(installer.InstallError, 'rebind'):
            self.install()
        self.install(rebind=True)
        for agent in self.targets:
            body = json.loads((self.skill(agent) / 'installation.json').read_text(encoding='utf-8'))
            self.assertEqual(body['package_root'], str(self.root))
        self.assertFalse((self.root / '.relay-private').exists())

    def test_updated_template_installs_for_same_binding(self):
        self.install()
        source = self.root / 'skills/relay-codex-app/SKILL.md'
        changed = source.read_bytes() + b'\nAdditional release instructions.\n'
        source.write_bytes(changed)
        states = self.install()
        self.assertEqual(states[0]['state'], 'installed')
        self.assertEqual(states[1]['state'], 'unchanged')
        self.assertEqual((self.skill('codex') / 'SKILL.md').read_bytes(), changed)

    def test_does_not_overwrite_source_skill_or_external_config(self):
        with self.assertRaisesRegex(installer.InstallError, 'separate'):
            installer.install(self.root, self.config, Path(sys.executable), {'codex': self.root / 'skills'})
        outside = self.base / 'external.json'
        shutil.copyfile(self.config, outside)
        with self.assertRaisesRegex(installer.InstallError, 'inside'):
            installer.install(self.root, outside, Path(sys.executable), self.targets)

    def test_hardlink_is_rejected_without_mutation(self):
        self.install()
        extra = self.base / 'linked-skill.md'
        os.link(self.skill('codex') / 'SKILL.md', extra)
        before = extra.read_bytes()
        with self.assertRaisesRegex(installer.InstallError, 'Hardlinked'):
            self.install()
        self.assertEqual(extra.read_bytes(), before)

    def test_partial_write_failure_restores_prior_files(self):
        self.install()
        before = {agent: {p.name: p.read_bytes() for p in self.skill(agent).iterdir()} for agent in self.targets}
        for agent in self.targets:
            source = self.root / 'skills' / installer.NAMES[agent] / 'SKILL.md'
            source.write_bytes(source.read_bytes() + b'\nNew version.\n')
        original = installer.atomic_write
        failed = False
        def fail_once(path, raw):
            nonlocal failed
            if path == self.skill('claude') / 'run-relay.ps1' and not failed:
                failed = True
                raise OSError('simulated interrupted write')
            return original(path, raw)
        with patch.object(installer, 'atomic_write', side_effect=fail_once), self.assertRaises(OSError):
            self.install()
        self.assertEqual(before, {agent: {p.name: p.read_bytes() for p in self.skill(agent).iterdir()} for agent in self.targets})

    @unittest.skipUnless(os.name == 'nt', 'Windows launcher')
    def test_launcher_uses_bound_config_from_unrelated_working_directory(self):
        self.install()
        for agent in self.targets:
            launcher = self.skill(agent) / 'run-relay.ps1'
            command = [shutil.which('powershell'), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(launcher)]
            def run(*args):
                return subprocess.run([*command, *args], cwd=self.base, capture_output=True, text=True,
                                      encoding='utf-8', timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
            # Runtime initialization is separate from global skill installation.
            if agent == 'codex':
                fresh = run('status')
                self.assertEqual(fresh.returncode, 2)
                self.assertIn('not initialized', fresh.stderr)
                initialized = run('init')
                self.assertEqual(initialized.returncode, 0, initialized.stderr)
            status = run('status')
            self.assertEqual(status.returncode, 0, status.stderr)
            self.assertFalse(json.loads(status.stdout)['running'])
            help_result = run('app', 'reply', '--help')
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn('--delivery-id', help_result.stdout)
            invalid = run('no-such-operation')
            self.assertEqual(invalid.returncode, 2)
        self.assertFalse((self.base / 'relay.local.json').exists())


if __name__ == '__main__':
    unittest.main()
