"""Quick-start isolation, resumability and official-login orchestration."""
import argparse
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest
from unittest.mock import Mock, patch
import uuid

from relay_collaboration import quickstart as quick
from relay_collaboration.config import ConfigError, load_config, require_runtime


class QuickStartTests(unittest.TestCase):
    def setUp(self):
        work = Path(__file__).resolve().parents[1] / 'work'
        self.root = work / ('快速 啟動 ' + uuid.uuid4().hex)
        self.root.mkdir()
        self.assertTrue(self.root.resolve().is_relative_to(work.resolve()))
        self.addCleanup(shutil.rmtree, self.root)
        self.data = self.root / 'data'

    def prepare(self, name='default'):
        return quick.prepare(self.data, name, detect=False)

    def test_two_instances_and_repeat_preserve_config_identity_and_auth(self):
        one, created = self.prepare('one')
        self.assertTrue(created)
        marker = require_runtime(one)
        raw = one.path.read_bytes()
        two, _ = self.prepare('two')
        with patch.object(quick, 'initialize', side_effect=AssertionError('must not reinitialize')):
            again, created = self.prepare('one')
        self.assertFalse(created)
        self.assertEqual(again.path.read_bytes(), raw)
        self.assertEqual(require_runtime(again), marker)
        self.assertNotEqual(marker['runtime_id'], require_runtime(two)['runtime_id'])
        self.assertTrue({one.broker_url, one.console_url}.isdisjoint({two.broker_url, two.console_url}))
        self.assertEqual(list(one.auth_root.iterdir()), [])
        self.assertEqual(list(two.auth_root.iterdir()), [])

    def test_invalid_name_git_ancestor_and_incomplete_instance_never_fall_back(self):
        with self.assertRaises(ConfigError): quick.prepare(self.data, '../bad', detect=False)
        self.assertFalse(self.data.exists())
        (self.root / '.git').mkdir()
        with self.assertRaises(ConfigError): self.prepare()
        self.assertFalse(self.data.exists())
        (self.root / '.git').rmdir()
        one, _ = self.prepare()
        broken = self.data / 'instances/broken'
        broken.mkdir()
        with self.assertRaises(ConfigError): self.prepare('broken')
        self.assertFalse((broken / 'relay.local.json').exists())
        self.assertEqual(quick.service.status(one)['running'], False)

    def test_unknown_base_catalog_is_preserved(self):
        (self.data / 'instances').mkdir(parents=True)
        with self.assertRaises(ConfigError): self.prepare()
        self.assertFalse((self.data / 'relay.local.json').exists())

    def test_configuration_change_preserves_identity_other_settings_and_backup(self):
        config, _ = self.prepare()
        raw = config.path.read_bytes()
        marker = require_runtime(config)
        exe = self.root / 'claude.exe'
        with patch.object(quick, 'official_claude', return_value=exe):
            updated = quick.configure_claude(config, exe)
        self.assertEqual(require_runtime(updated), marker)
        self.assertEqual(updated.provider['command'], [str(exe)])
        old = json.loads(raw)
        new = json.loads(updated.path.read_bytes())
        new['provider'] = old['provider']
        self.assertEqual(new, old)
        backup = next(updated.path.parent.glob('.before-quickstart-*.json'))
        self.assertEqual(backup.read_bytes(), raw)
        self.assertEqual(list(updated.auth_root.iterdir()), [])

    def test_running_or_unknown_service_blocks_configuration_and_login(self):
        config, _ = self.prepare()
        raw = config.path.read_bytes()
        exe = self.root / 'claude.exe'
        with patch.object(quick, 'official_claude', return_value=exe):
            for running in (True, None):
                with patch.object(quick.service, 'status', return_value={'running': running}):
                    with self.assertRaises(ConfigError): quick.configure_claude(config, exe)
                    self.assertEqual(config.path.read_bytes(), raw)
            configured = quick.configure_claude(config, exe)
            with patch.object(quick.service, 'status', return_value={'running': True}), patch.object(quick.subprocess, 'run') as run:
                with self.assertRaises(ConfigError): quick.login(configured)
                run.assert_not_called()

    def test_explicit_model_is_preserved_and_existing_cli_not_auto_replaced(self):
        config, _ = self.prepare()
        value = json.loads(config.path.read_bytes())
        value['provider'] = {'kind': 'claude_cli', 'command': [str(self.root / 'old/claude.exe')],
                             'model': 'requested-model', 'effort': 'high'}
        config.path.write_text(json.dumps(value), encoding='utf-8')
        config = load_config(config.path)
        with patch.object(quick, 'discover_claude') as discover:
            selected, _ = quick.prepare(self.data)
            discover.assert_not_called()
        self.assertEqual(selected.provider['model'], 'requested-model')
        exe = self.root / 'new/claude.exe'
        with patch.object(quick, 'official_claude', return_value=exe):
            changed = quick.configure_claude(config, exe)
        self.assertEqual(changed.provider['model'], 'requested-model')
        self.assertEqual(changed.provider['effort'], 'high')

    def test_changed_config_is_not_overwritten(self):
        config, _ = self.prepare()
        config.path.write_bytes(config.path.read_bytes() + b'\n')
        raw = config.path.read_bytes()
        with patch.object(quick, 'official_claude', return_value=self.root / 'claude.exe'):
            with self.assertRaises(ConfigError): quick.configure_claude(config, self.root / 'claude.exe')
        self.assertEqual(config.path.read_bytes(), raw)

    def test_login_calls_auth_only_in_own_environment_without_changing_parent(self):
        config, _ = self.prepare()
        exe = self.root / 'claude.exe'
        with patch.object(quick, 'official_claude', return_value=exe):
            config = quick.configure_claude(config, exe)
            adapter = Mock()
            adapter.environment.return_value = {'CLAUDE_CONFIG_DIR': str(config.auth_root)}
            adapter.preflight.return_value = {'ok': True, 'auth_ready': True}
            with patch.object(type(config), 'adapter', return_value=adapter), \
                 patch.object(quick.subprocess, 'run', return_value=Mock(returncode=0)) as run, \
                 patch.dict(os.environ, {'CLAUDE_CONFIG_DIR': 'parent-unchanged'}):
                self.assertTrue(quick.login(config)['auth_ready'])
                self.assertEqual(os.environ['CLAUDE_CONFIG_DIR'], 'parent-unchanged')
            args, kwargs = run.call_args
            self.assertEqual(args[0], [str(exe), 'auth', 'login'])
            self.assertEqual(kwargs['cwd'], config.run_root)
            self.assertEqual(kwargs['env'], {'CLAUDE_CONFIG_DIR': str(config.auth_root)})
            self.assertFalse(kwargs['shell'])

    def test_notes_report_incomplete_setup_and_preserve_user_edits(self):
        config, created = self.prepare()
        for language in ('en', 'zh-TW'):
            result = quick.snapshot(config, created, language)
            self.assertFalse(result['ready_for_both_directions'])
            self.assertFalse(result['claude']['ok'])
            self.assertFalse(result['codex']['connected'])
            self.assertIn(str(config.path), result['connection_instructions'])
            self.assertIn('relay-codex-app/SKILL.md'.replace('/', os.sep), result['connection_instructions'])
            note = Path(result['connection_file'])
            note.write_text('user-edited note', encoding='utf-8')
            self.assertIsNone(quick.snapshot(config, False, language)['connection_file'])
            self.assertEqual(note.read_text(encoding='utf-8'), 'user-edited note')

    @unittest.skipUnless(os.name == 'nt', 'Authenticode is Windows-specific')
    def test_signature_check_uses_literal_environment_path_and_rejects_unsigned(self):
        exe = self.root / 'claude.exe'
        exe.write_bytes(b'fixture, never executed')
        with patch.object(quick.subprocess, 'run', return_value=Mock(returncode=0)) as run:
            self.assertEqual(quick.official_claude(exe), exe)
        args, kwargs = run.call_args
        self.assertNotIn(str(exe), ' '.join(args[0]))
        self.assertEqual(kwargs['env']['RELAY_CLAUDE_CANDIDATE'], str(exe))
        self.assertEqual(kwargs['env']['PSModulePath'], str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/Modules'))
        self.assertFalse(kwargs['shell'])
        with patch.object(quick.subprocess, 'run', return_value=Mock(returncode=1)):
            with self.assertRaises(ConfigError): quick.official_claude(exe)

    def test_wizard_opens_selected_console_without_attaching_or_auth_copy(self):
        args = argparse.Namespace(language='zh-TW', data_dir=str(self.data), name='default',
                                  claude=None, no_detect=True, no_browser=False)
        messages = []
        replies = iter(['', '', ''])  # data folder, name, missing CLI: configure later
        with patch.object(quick.service, 'start', return_value={'ok': True, 'running': True}) as start, \
             patch.object(quick.webbrowser, 'open') as browser, patch.object(quick, 'login') as login:
            result = quick.wizard(args, ask=lambda _: next(replies), emit=messages.append)
        start.assert_called_once()
        login.assert_not_called()
        browser.assert_called_once_with(result['console_url'])
        self.assertFalse(result['ready_for_both_directions'])
        self.assertIn('Codex 已連接：False', messages)

    def test_noninteractive_failed_start_is_not_reported_as_success(self):
        with patch.object(quick.service, 'start', return_value={'ok': False, 'running': False}), \
             patch('sys.stderr', new_callable=io.StringIO) as error:
            code = quick.main(['--data-dir', str(self.data), '--no-detect', '--start'])
        self.assertEqual(code, 2)
        self.assertFalse(json.loads(error.getvalue())['ok'])


if __name__ == '__main__':
    unittest.main()
