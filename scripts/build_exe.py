"""Build a Windows x64 onefile executable from public sources in neutral staging."""
from __future__ import annotations
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import stat
import subprocess
import sys
import uuid
import zipfile
from package import PACKAGE, VERSION, collect, canonical, digest, checked_source, PackageError
from verify_exe import verify, pe_x64


def build(root):
    if os.name != 'nt' or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise PackageError('Build this release with native Windows x64 Python')
    pyinstaller = importlib.metadata.distribution('pyinstaller')
    version_parts = tuple(int(p) for p in pyinstaller.version.split('.')[:2])
    if version_parts < (6, 9):
        raise PackageError('PyInstaller 6.9+ is required for independent onefile supervisors')
    sources = collect(root)
    source_files = [{'path': n, 'size': len(raw), 'sha256': digest(raw)} for n, raw in sorted(sources.items())]
    stage = root / 'work' / ('exe-build-' + VERSION + '-' + uuid.uuid4().hex[:8])
    stage.mkdir(parents=True)
    source = stage / 'source'
    for name, raw in sources.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    numeric = tuple(int(p) for p in VERSION.split('.')) + (0,)
    version_file = stage / 'version-info.txt'
    version_file.write_text(
        'VSVersionInfo(ffi=FixedFileInfo(filevers=' + repr(numeric) + ', prodvers=' + repr(numeric) +
        ", mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0,0)), kids=[" +
        "StringFileInfo([StringTable('040904B0', [StringStruct('FileDescription','Relay Collaboration')," +
        "StringStruct('ProductName','Relay Collaboration'), StringStruct('FileVersion','" + VERSION +
        "'), StringStruct('ProductVersion','" + VERSION + "'), StringStruct('OriginalFilename','Relay.exe')])])," +
        "VarFileInfo([VarStruct('Translation',[1033,1200])])])", encoding='utf-8')
    command = [sys.executable, '-B', '-X', 'utf8', '-m', 'PyInstaller', '--onefile', '--console',
               '--name', 'Relay', '--noupx', '--distpath', str(stage / 'output'),
               '--workpath', str(stage / 'build'), '--specpath', str(stage),
               '--version-file', str(version_file)]
    # Only already-allowlisted resources are embedded. Never collect the workspace tree.
    for name in sorted(sources):
        if (name.startswith(('skills/', 'config/', 'docs/', 'relay_collaboration/web/'))
                or name == 'scripts/skill-command.ps1'):
            command.extend(['--add-data', str(source / name) + os.pathsep + str(Path(name).parent)])
    command.append(str(source / 'relay.py'))
    env = {**os.environ, 'PYTHONUTF8': '1', 'PYINSTALLER_CONFIG_DIR': str(stage / 'cache')}
    env.pop('PYTHONPATH', None)
    log = stage / 'build.log'
    with log.open('w', encoding='utf-8') as stream:
        result = subprocess.run(command, cwd=source, env=env, stdout=stream, stderr=subprocess.STDOUT,
                                creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise PackageError('Executable build failed; inspect ' + str(log))
    exe = checked_source(stage, stage / 'output/Relay.exe').read_bytes()
    if not pe_x64(exe):
        raise PackageError('Build did not produce Windows x64 PE executable')
    python_license = (Path(sys.base_prefix) / 'LICENSE.txt').read_text(encoding='utf-8')
    license_files = [p for p in pyinstaller.files if str(p).endswith('.dist-info/COPYING.txt')]
    if len(license_files) != 1:
        raise PackageError('Unable to locate the installed PyInstaller license')
    bootloader_license = pyinstaller.locate_file(license_files[0]).read_text(encoding='utf-8')
    notices = ('Relay executable runtime notices\n\nPython ' + platform.python_version() + '\n' + python_license
               + '\n\nPyInstaller ' + pyinstaller.version + ' bootloader\n' + bootloader_license).encode('utf-8')
    build_info = {'schema': 'relay-exe-build/v1', 'version': VERSION, 'target': 'windows-x64',
                  'python': platform.python_version(), 'pyinstaller': pyinstaller.version, 'mode': 'onefile-console',
                  'source_manifest_sha256': digest(canonical(source_files)), 'exe_sha256': digest(exe),
                  'code_signed': False}
    public = {**sources, 'Relay.exe': exe, 'BUILD_INFO.json': canonical(build_info), 'THIRD_PARTY_NOTICES.txt': notices}
    manifest = {'schema': 'relay-windows-release/v1', 'package': PACKAGE, 'version': VERSION,
                'files': [{'path': n, 'size': len(b), 'sha256': digest(b)} for n, b in sorted(public.items())]}
    output = root / 'dist'
    output.mkdir(exist_ok=True)
    archive = output / (PACKAGE + '-' + VERSION + '-windows-x64.zip')
    sidecar = archive.with_suffix('.manifest.json')
    folder = output / (PACKAGE + '-' + VERSION + '-windows-x64')
    if archive.exists() or sidecar.exists() or folder.exists():
        raise PackageError('Windows release destination exists; preserve it and choose a new version')
    folder.mkdir()
    for name, raw in {**public, 'MANIFEST.json': canonical(manifest)}.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
        for name, raw in sorted({**public, 'MANIFEST.json': canonical(manifest)}.items()):
            item = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            item.create_system = 3
            item.external_attr = (stat.S_IFREG | 0o644) << 16
            bundle.writestr(item, raw, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    sidecar.write_bytes(canonical({**manifest, 'archive': {'filename': archive.name,
                                'size': archive.stat().st_size, 'sha256': digest(archive.read_bytes())}}))
    proof = verify(archive, sidecar)
    (stage / 'build-evidence.json').write_bytes(canonical({**proof, 'build_info': build_info}))
    return {**proof, 'archive': str(archive), 'executable': str(folder / 'Relay.exe'),
            'build_log': str(log), 'bytes': len(exe)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    try:
        print(json.dumps(build(args.root.absolute()), ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, importlib.metadata.PackageNotFoundError) as exc:
        print('Windows build failed: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
