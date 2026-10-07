"""Source archive integrity fixtures: no live runtime or model execution."""
import importlib.util
import json
import re
import posixpath
from pathlib import Path
import stat
import tempfile
import unittest
import warnings
import zipfile

_SPEC = importlib.util.spec_from_file_location('relay_package_tool', Path(__file__).resolve().parents[1] / 'scripts/package.py')
pack = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pack)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'work', prefix='package-fixture-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for name in pack.REQUIRED_FILES:
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture-only\n', encoding='utf-8')

    def put(self, name, raw=b'PRIVATE-FIXTURE-NOT-FOR-PACKAGE'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def build(self):
        return pack.build(self.root, self.root / 'dist')

    def rewrite(self, archive, transform):
        with zipfile.ZipFile(archive) as source:
            entries = [(i.filename, source.read(i.filename)) for i in source.infolist()]
        target = self.root / 'altered.zip'
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            with zipfile.ZipFile(target, 'w') as dest:
                for name, raw in transform(entries):
                    dest.writestr(name, raw)
        return target

    def test_allowlist_excludes_private_history_runtime_and_machine_config(self):
        for name in ('migration_input/worker/worker_core.py', 'docs/bootstrap-manifest.json', 'docs/agent-log.md',
                     'docs/START_HERE.md', 'AGENTS.md', 'relay.local.json', 'config/private.json',
                     'local-data/instances/default/CONNECT.zh-TW.txt', 'local-data/relay.local.json', 'runtime/database.sqlite', 'work/provider-auth/auth.json', 'tests/fixture/runtime.json',
                     'relay_collaboration/private.sqlite', 'relay_collaboration/private_notes.py', 'relay_collaboration/web/private.json',
                     'scripts/private.ps1', '.git/config', '.env', 'credentials/claude.token'):
            self.put(name)
        self.put('config/relay.example.json', b'{"fixture":true}')
        self.put('docs/PROTOCOL.md', b'Public fixture protocol')
        archive, manifest = self.build()
        with zipfile.ZipFile(archive) as bundle:
            self.assertNotIn(b'PRIVATE-FIXTURE-NOT-FOR-PACKAGE', b''.join(bundle.read(n) for n in bundle.namelist()))
            self.assertIn('config/relay.example.json', bundle.namelist())
            self.assertIn('docs/PROTOCOL.md', bundle.namelist())
        result = pack.verify(archive, manifest)
        self.assertTrue(result['ok'])
        self.assertTrue(result['external_manifest_verified'])
        self.assertNotIn(str(self.root), manifest.read_text(encoding='utf-8'))

    def test_package_deterministic_and_existing_output_is_preserved(self):
        first, manifest = self.build()
        before = first.read_bytes()
        second, _ = pack.build(self.root, self.root / 'other-dist')
        self.assertEqual(before, second.read_bytes())
        with self.assertRaises(pack.PackageError):
            self.build()
        self.assertEqual(before, first.read_bytes())

    def test_current_source_contains_both_languages_and_agent_references(self):
        source=pack.collect(Path(__file__).resolve().parents[1])
        for name in ('README.md','README.zh-TW.md','relay_collaboration/web/i18n.js','QuickStart.cmd','scripts/quickstart.ps1','relay_collaboration/quickstart.py','docs/QUICKSTART.md','docs/QUICKSTART.zh-TW.md','docs/AGENT_GUIDE.md','docs/AGENT_GUIDE.zh-TW.md','docs/SHARING.md'):
            self.assertIn(name,source)
        for name in ('README.md','README.zh-TW.md','docs/QUICKSTART.md','docs/QUICKSTART.zh-TW.md','docs/AGENT_GUIDE.md','docs/AGENT_GUIDE.zh-TW.md','docs/SHARING.md'):
            for target in re.findall(r'\]\(([^)]+)\)',source[name].decode('utf-8')):
                if '://' in target or target.startswith('#'):continue
                path=posixpath.normpath(posixpath.join(posixpath.dirname(name),target.split('#')[0]))
                self.assertIn(path,source,(name,target))
        self.assertNotIn('AGENTS.md',source)
        self.assertNotIn('docs/agent-log.md',source)

    def test_rejects_changed_payload_missing_file_and_extra_file(self):
        archive, _ = self.build()
        for transform in (lambda e: [(n, b'tampered' if n == 'README.md' else b) for n, b in e],
                          lambda e: [(n, b) for n, b in e if n != 'README.md'],
                          lambda e: [*e, ('credentials/secret.json', b'bad')]):
            altered = self.rewrite(archive, transform)
            with self.assertRaises(pack.PackageError):
                pack.verify(altered)

    def test_rejects_duplicate_case_collision_and_traversal(self):
        archive, _ = self.build()
        for name in ('README.md', 'Readme.md', '../outside.py', '/absolute.py', 'a\\evil.py', 'x:stream', 'tests/con.py'):
            with self.subTest(name=name):
                altered = self.rewrite(archive, lambda entries: [*entries, (name, b'bad')])
                with self.assertRaises(pack.PackageError):
                    pack.verify(altered)

    def test_rejects_link_archive_entry(self):
        archive, _ = self.build()
        altered = self.rewrite(archive, lambda e: e)
        with zipfile.ZipFile(altered, 'a') as bundle:
            entry = zipfile.ZipInfo('tests/test_link.py')
            entry.create_system = 3
            entry.external_attr = (stat.S_IFLNK | 0o777) << 16
            bundle.writestr(entry, '../private')
        with self.assertRaises(pack.PackageError):
            pack.verify(altered)

    def test_rejects_external_archive_hash_and_embedded_manifest_changes(self):
        archive, sidecar = self.build()
        external = json.loads(sidecar.read_text(encoding='utf-8'))
        external['archive']['sha256'] = '0' * 64
        sidecar.write_text(json.dumps(external), encoding='utf-8')
        with self.assertRaises(pack.PackageError):
            pack.verify(archive, sidecar)
        altered = self.rewrite(archive, lambda entries: [(n, b'{"schema_version":1,"schema_version":1}' if n == 'MANIFEST.json' else raw) for n, raw in entries])
        with self.assertRaises(pack.PackageError):
            pack.verify(altered)

    def test_outside_output_rejected(self):
        with self.assertRaises(pack.PackageError):
            pack.build(self.root, self.root.parent / 'outside-output')


if __name__ == '__main__':
    unittest.main()
