"""Frozen command construction and first-run behavior; real EXE acceptance is separate."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from relay_collaboration import execution, desktop, cli, service, skill_install
from relay_collaboration.config import load_config, ConfigError

ROOT = Path(__file__).resolve().parents[1]


class ExecutableTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / 'work', prefix='exe-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config_path = self.root / 'relay.local.json'

    def test_frozen_supervisor_invokes_exe_without_python_flags(self):
        desktop.ensure_profile(self.config_path)
        config = load_config(self.config_path)
        executable = str(self.root / 'Relay.exe')
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', executable):
            command = service._command(config, 'fixture-instance')
            self.assertEqual(command, [executable, '--config', str(config.path), 'serve', '--instance-id',
                                      'fixture-instance', '--expected-config-digest', config.digest])
            self.assertEqual(execution.package_root(), self.root)
            self.assertEqual(execution.supervisor_environment()['PYINSTALLER_RESET_ENVIRONMENT'], '1')
        self.assertNotEqual(os.environ.get('PYINSTALLER_RESET_ENVIRONMENT'), '1')

    def test_source_command_keeps_python_entry(self):
        with patch.object(sys, 'frozen', False, create=True):
            prefix = execution.command_prefix()
            self.assertEqual(prefix[0], sys.executable)
            self.assertIn(str(ROOT / 'relay.py'), prefix)
            self.assertEqual(execution.supervisor_environment(), dict(os.environ))

    def test_profile_creation_is_relative_and_preserves_existing_bytes(self):
        desktop.ensure_profile(self.config_path)
        config = load_config(self.config_path)
        self.assertEqual(config.project_root, self.root / 'workspace')
        self.assertEqual(config.runtime_root, self.root / '.relay-private')
        self.assertEqual(config.provider['kind'], 'unavailable')
        self.config_path.write_bytes(b'not valid; user owned')
        desktop.ensure_profile(self.config_path)
        self.assertEqual(self.config_path.read_bytes(), b'not valid; user owned')
        self.assertFalse(config.runtime_root.exists())

    def test_failed_start_does_not_open_browser(self):
        desktop.ensure_profile(self.config_path)
        config = load_config(self.config_path)
        with patch.object(desktop, 'initialize'), patch.object(service, 'start', return_value={'ok': False}), \
                patch.object(desktop.webbrowser, 'open') as browser:
            self.assertFalse(desktop.launch(config)['ok'])
            browser.assert_not_called()

    def test_no_browser_flag_and_successful_open(self):
        desktop.ensure_profile(self.config_path)
        config = load_config(self.config_path)
        with patch.object(desktop, 'initialize'), patch.object(service, 'start', return_value={'ok': True, 'running': True}), \
                patch.object(desktop.webbrowser, 'open', return_value=True) as browser:
            self.assertFalse(desktop.launch(config, open_browser=False)['browser_opened'])
            browser.assert_not_called()
            self.assertTrue(desktop.launch(config)['browser_opened'])
            browser.assert_called_once_with(config.console_url)

    def test_frozen_binding_points_to_exe_and_uses_embedded_templates(self):
        desktop.ensure_profile(self.config_path)
        exe = self.root / 'Relay.exe'
        exe.write_bytes(b'MZ-fixture-not-executed')
        targets = {'codex': self.root / 'user/.codex/skills', 'claude': self.root / 'user/.claude/skills'}
        skill_install.install(self.root, self.config_path, exe, targets, executable=True, resources=ROOT)
        for agent, folder in targets.items():
            path = folder / skill_install.NAMES[agent]
            body = json.loads((path / 'installation.json').read_text(encoding='utf-8'))
            self.assertEqual(body['executable'], str(exe))
            self.assertNotIn('python', body)
            self.assertEqual(body['package_root'], str(self.root))
            self.assertEqual((path / 'SKILL.md').read_bytes(), (ROOT / 'skills' / skill_install.NAMES[agent] / 'SKILL.md').read_bytes())
        self.assertFalse((self.root / 'relay.py').exists())

    def test_frozen_default_config_uses_exe_folder_not_cwd(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(self.root / 'Relay.exe')):
            args = cli.parser().parse_args(['status'])
            self.assertEqual(args.config, str(self.config_path))

    def test_invalid_existing_profile_does_not_start(self):
        self.config_path.write_bytes(b'{broken user config')
        with patch.object(service, 'start') as start, contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(['--config', str(self.config_path), 'launch', '--no-browser'])
        self.assertEqual(code, 2)
        start.assert_not_called()
        self.assertEqual(self.config_path.read_bytes(), b'{broken user config')


if __name__ == '__main__':
    unittest.main()
