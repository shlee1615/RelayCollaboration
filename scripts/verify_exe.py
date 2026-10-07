"""Verify the Windows executable distribution without executing or extracting it."""
import argparse
import hashlib
import json
from pathlib import Path
import stat
import struct
import sys
import zipfile
from package import PACKAGE, VERSION, REQUIRED_FILES, allowed, safe_name, strict_json, PackageError, digest

LIMIT = 128 * 1024 * 1024
EXTRAS = {'Relay.exe', 'BUILD_INFO.json', 'THIRD_PARTY_NOTICES.txt'}


def pe_x64(raw):
    if len(raw) < 256 or raw[:2] != b'MZ':
        return False
    offset = struct.unpack_from('<I', raw, 60)[0]
    return (offset + 26 <= len(raw) and raw[offset:offset + 4] == b'PE\0\0'
            and struct.unpack_from('<H', raw, offset + 4)[0] == 0x8664
            and struct.unpack_from('<H', raw, offset + 24)[0] == 0x20b)


def verify(archive, sidecar):
    archive, sidecar = Path(archive), Path(sidecar)
    if archive.stat().st_size > LIMIT or sidecar.stat().st_size > 1024 * 1024:
        raise PackageError('Windows release exceeds size bound')
    external = strict_json(sidecar.read_bytes())
    if external.get('archive') != {'filename': archive.name, 'size': archive.stat().st_size,
                                   'sha256': digest(archive.read_bytes())}:
        raise PackageError('Windows archive hash or size mismatch')
    with zipfile.ZipFile(archive) as bundle:
        infos = bundle.infolist()
        names = [safe_name(i.filename) for i in infos]
        if len(names) > 1000 or len(names) != len(set(n.lower() for n in names)):
            raise PackageError('Duplicate Windows archive entries')
        if sum(i.file_size for i in infos) > LIMIT:
            raise PackageError('Expanded Windows archive exceeds size bound')
        for item in infos:
            if item.flag_bits & 1 or stat.S_IFMT(item.external_attr >> 16) not in (0, stat.S_IFREG):
                raise PackageError('Encrypted or special Windows archive entry')
        manifest = strict_json(bundle.read('MANIFEST.json'))
        if (set(manifest) != {'schema', 'package', 'version', 'files'} or
                manifest['schema'] != 'relay-windows-release/v1' or manifest['package'] != PACKAGE or
                manifest['version'] != VERSION or not isinstance(manifest['files'], list)):
            raise PackageError('Unsupported Windows release manifest')
        expected = {}
        for entry in manifest['files']:
            if not isinstance(entry, dict) or set(entry) != {'path', 'size', 'sha256'}:
                raise PackageError('Invalid Windows manifest entry')
            name = safe_name(entry['path'])
            if name.lower() in {n.lower() for n in expected} or not (name in EXTRAS or allowed(name)):
                raise PackageError('Nonpublic or duplicate Windows manifest entry')
            expected[name] = entry
        if set(names) != set(expected) | {'MANIFEST.json'} or not (REQUIRED_FILES | EXTRAS).issubset(expected):
            raise PackageError('Missing or extra Windows release file')
        for name, entry in expected.items():
            raw = bundle.read(name)
            if type(entry['size']) is not int or len(raw) != entry['size'] or digest(raw) != entry['sha256']:
                raise PackageError('Windows release file hash or size mismatch: ' + name)
        if not pe_x64(bundle.read('Relay.exe')):
            raise PackageError('Expected a Windows x64 executable')
        if set(external) != set(manifest) | {'archive'} or any(external.get(k) != v for k, v in manifest.items()):
            raise PackageError('Windows embedded and external manifests differ')
    return {'ok': True, 'version': VERSION, 'target': 'windows-x64', 'files': len(expected),
            'archive_sha256': external['archive']['sha256'], 'exe_sha256': expected['Relay.exe']['sha256']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('archive', type=Path)
    parser.add_argument('--manifest', type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.archive, args.manifest or args.archive.with_suffix('.manifest.json')), indent=2))
        return 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError, zipfile.BadZipFile) as exc:
        print('Windows release verification failed: ' + str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
