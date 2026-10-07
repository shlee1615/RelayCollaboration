"""Windows installer integration with duplicate Python application candidates."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
import uuid


@unittest.skipUnless(os.name == 'nt', 'PowerShell installer is Windows-specific')
class InstallTests(unittest.TestCase):
    def test_multiple_python_candidates_choose_first_and_initialize_unicode_path(self):
        source = Path(__file__).resolve().parents[1]
        root = source / 'work' / ('安裝 多個 Python ' + uuid.uuid4().hex)
        root.mkdir(); self.addCleanup(shutil.rmtree, root)
        (root/'scripts').mkdir(); (root/'config').mkdir(); (root/'decoy').mkdir()
        # Not executed: this merely creates a second Get-Command application match.
        (root/'decoy/python.exe').write_bytes(b'not-an-executable')
        shutil.copyfile(source/'scripts/install.ps1',root/'scripts/install.ps1')
        shutil.copyfile(source/'config/relay.example.json',root/'config/relay.example.json')
        shutil.copyfile(source/'relay.py',root/'relay.py')
        shutil.copytree(source/'relay_collaboration',root/'relay_collaboration',ignore=shutil.ignore_patterns('__pycache__'))
        env = {**os.environ, 'PATH':os.pathsep.join([str(Path(sys.executable).parent),str(root/'decoy'),os.environ['PATH']])}
        env.pop('PYTHONPATH',None)
        result = subprocess.run([shutil.which('powershell'),'-NoProfile','-ExecutionPolicy','Bypass',
                                 '-File',str(root/'scripts/install.ps1'),'-Initialize'],cwd=root,env=env,
                                capture_output=True,timeout=45,creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(result.returncode,0,result.stderr.decode('utf-8',errors='replace'))
        self.assertTrue((root/'.venv/Scripts/python.exe').is_file())
        self.assertTrue((root/'.relay-private/codex-auth').is_dir())
        metadata=json.loads((root/'.relay-private/relay-runtime.json').read_text(encoding='utf-8'))
        self.assertEqual(metadata['identity']['runtime_root'],str(root/'.relay-private'))
