"""Model selection changes only the selected stopped profile; no real models."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from relay_collaboration import cli, model_selection as models, quickstart
from relay_collaboration.config import ConfigError, initialize, load_config, require_runtime


class ModelSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'work', prefix='model-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        body = {'schema_version': 1, 'project': {'id': 'models', 'root': './workspace'},
                'runtime': {'root': './private'}, 'endpoints': {
                    'broker': 'http://127.0.0.1:19541', 'console': 'http://127.0.0.1:19542'},
                'transport': 'http',
                'provider': {'kind': 'claude_cli', 'command': [str(self.root / 'claude.exe')], 'model': 'fable', 'effort': 'high'},
                'codex_provider': {'kind': 'codex_cli', 'command': [str(self.root / 'codex.exe')], 'model': 'gpt-6-sol'}}
        self.path = self.root / 'relay.json'
        # BOM/CRLF exercises preservation.
        body['worker'] = {'allowed_paths': []}
        self.path.write_bytes(b'\xef\xbb\xbf' + (json.dumps(body, indent=2) + '\n').replace('\n', '\r\n').encode('utf-8'))
        self.config = load_config(self.path)
        initialize(self.config)

    def invoke(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = cli.main(['--config', str(self.path), *args])
        return code, json.loads(output.getvalue())

    def test_show_distinguishes_presets_from_account_availability_without_dispatch(self):
        with patch.object(type(self.config), 'adapter', side_effect=AssertionError('no provider call')):
            code, result = self.invoke('model', 'show')
        self.assertEqual(code, 0)
        self.assertFalse(result['availability_verified'])
        self.assertEqual(result['providers']['claude']['model'], 'fable')
        self.assertIn('claude-opus-5-5', [p['value'] for p in result['providers']['claude']['presets']])
        self.assertIn('gpt-6-astra', [p['value'] for p in result['providers']['codex']['presets']])

    def test_select_opus_preserves_peer_effort_runtime_and_original_bytes(self):
        raw = self.path.read_bytes()
        marker = require_runtime(self.config)
        result = models.configure(self.config, 'claude', model='claude-opus-5-5')
        updated = load_config(self.path)
        self.assertTrue(result['changed'])
        self.assertEqual(updated.provider['model'], 'claude-opus-5-5')
        self.assertEqual(updated.provider['effort'], 'high')
        self.assertEqual(updated.codex_provider, self.config.codex_provider)
        self.assertEqual(require_runtime(updated), marker)
        self.assertEqual(Path(result['backup']).read_bytes(), raw)
        self.assertTrue(self.path.read_bytes().startswith(b'\xef\xbb\xbf'))
        self.assertNotIn(b'\n', self.path.read_bytes().replace(b'\r\n', b''))
        before, after = json.loads(raw), json.loads(self.path.read_bytes())
        before['provider']['model'] = 'claude-opus-5-5'
        self.assertEqual(before, after)
        command = updated.adapter('claude').build_command()
        self.assertEqual(command[command.index('--model') + 1], 'claude-opus-5-5')

    def test_codex_cli_selection_and_default_reset(self):
        code, result = self.invoke('model', 'set', '--actor', 'codex', '--model', 'gpt-6-astra', '--effort', 'high')
        self.assertEqual(code, 0)
        updated = load_config(self.path)
        self.assertEqual(updated.provider, self.config.provider)
        command = updated.adapter('codex').build_command()
        self.assertEqual(command[command.index('--model') + 1], 'gpt-6-astra')
        models.configure(updated, 'codex', model='default', effort='default')
        updated = load_config(self.path)
        self.assertIsNone(updated.codex_provider['model'])
        self.assertIsNone(updated.codex_provider['effort'])
        self.assertNotIn('--model', updated.adapter('codex').build_command())

    def test_running_unknown_and_stale_config_refuse_without_writing(self):
        raw = self.path.read_bytes()
        for running in (True, None):
            with patch.object(models.service, 'status', return_value={'running': running}):
                with self.assertRaises(ConfigError):
                    models.configure(self.config, 'claude', model='opus')
            self.assertEqual(self.path.read_bytes(), raw)
        self.path.write_bytes(raw + b'\r\n')
        with self.assertRaises(ConfigError):
            models.configure(self.config, 'claude', model='opus')
        self.assertEqual(self.path.read_bytes(), raw + b'\r\n')

    def test_app_and_unavailable_are_external_or_unconfigured_and_cannot_be_changed(self):
        for actor, kind in (('codex', 'codex_app'), ('claude', 'unavailable')):
            body = json.loads(self.path.read_bytes())
            body['codex_provider' if actor == 'codex' else 'provider'] = {'kind': kind}
            self.path.write_text(json.dumps(body), encoding='utf-8')
            config = load_config(self.path)
            info = models.describe(config, actor)['providers'][actor]
            self.assertFalse(info['editable'])
            self.assertEqual(info['presets'], [])
            raw = self.path.read_bytes()
            with self.assertRaises(ConfigError):
                models.configure(config, actor, model='default')
            self.assertEqual(raw, self.path.read_bytes())

    def test_invalid_values_and_empty_update_do_not_mutate_config(self):
        raw = self.path.read_bytes()
        for options in ({}, {'model': ''}, {'model': '--help'}, {'model': 'x;whoami'},
                        {'model': 'a b'}, {'model': 'a' * 161}, {'effort': 'ultra'}):
            with self.subTest(options=options), self.assertRaises(ConfigError):
                models.configure(self.config, 'claude', **options)
            self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(self.invoke('model', 'set', '--actor', 'claude')[0], 2)

    def test_same_value_is_idempotent_and_custom_id_is_retained(self):
        raw = self.path.read_bytes()
        with patch.object(models.service, 'status', return_value={'running': True}):
            result = models.configure(self.config, 'claude', model='fable', effort='high')
        self.assertFalse(result['changed'])
        self.assertEqual(raw, self.path.read_bytes())
        models.configure(self.config, 'claude', model='claude-future-version[1m]', effort='default')
        self.assertEqual(load_config(self.path).provider['model'], 'claude-future-version[1m]')

    def test_peer_edit_during_validation_is_preserved(self):
        original_loader = models.load_config
        peer = self.path.read_bytes() + b'\r\n'
        def load(path):
            config = original_loader(path)
            if path != self.path:
                self.path.write_bytes(peer)
            return config
        with patch.object(models, 'load_config', side_effect=load):
            with self.assertRaises(ConfigError):
                models.configure(self.config, 'claude', model='opus')
        self.assertEqual(self.path.read_bytes(), peer)

    def test_named_instance_selection_does_not_change_base(self):
        from relay_collaboration import instances
        selected = load_config(instances.create(self.path, 'second')['config'])
        initialize(selected)
        raw = self.path.read_bytes()
        code, result = self.invoke('--instance', 'second', 'model', 'set', '--actor', 'claude', '--model', 'claude-opus-5-5')
        self.assertEqual(code, 0)
        self.assertEqual(result['selection']['config'], str(selected.path))
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertEqual(load_config(selected.path).provider['model'], 'claude-opus-5-5')

    def test_quickstart_explicit_model_option_is_applied_before_start(self):
        output = io.StringIO()
        def snapshot(config, *args):
            self.assertEqual(config.provider['model'], 'claude-opus-5-5')
            self.assertIsNone(config.provider['effort'])
            return {'ok': True}
        with patch.object(quickstart, 'prepare', return_value=(self.config, False)), \
             patch.object(quickstart, 'snapshot', side_effect=snapshot), \
             patch.object(quickstart.service, 'start', return_value={'ok': True, 'running': True}) as start, \
             contextlib.redirect_stdout(output):
            code = quickstart.main(['--no-detect', '--model', 'claude-opus-5-5', '--effort', 'default', '--start'])
        self.assertEqual(code, 0)
        self.assertEqual(start.call_args.args[0].provider['model'], 'claude-opus-5-5')

    def test_wizard_keep_select_and_running_paths(self):
        raw = self.path.read_bytes()
        config = quickstart.choose_model(self.config, 'claude', 'en', lambda _: '', lambda _: None)
        self.assertEqual(config.digest, self.config.digest)
        self.assertEqual(raw, self.path.read_bytes())
        answers = iter(['2', 'default'])
        config = quickstart.choose_model(config, 'claude', 'zh-TW', lambda _: next(answers), lambda _: None)
        self.assertEqual(config.provider['model'], 'claude-opus-5-5')
        self.assertIsNone(config.provider['effort'])
        with patch.object(models.service, 'status', return_value={'running': True}):
            same = quickstart.choose_model(config, 'claude', 'en', lambda _: self.fail('must not prompt'), lambda _: None)
        self.assertEqual(config.digest, same.digest)


if __name__ == '__main__':
    unittest.main()
