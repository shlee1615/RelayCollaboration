"""Build a source-only local release from an explicit public file allowlist.

This intentionally never packages an entire project tree. No runtime state,
credentials, imported provenance, or machine-specific local configuration is read.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import zipfile

PACKAGE = "relay-collaboration"
VERSION = "0.5.0"
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 64 * 1024 * 1024
ROOT_FILES = {"relay.py", "pyproject.toml", "requirements.txt", "README.md", "README.zh-TW.md", "NOTICE.md", ".gitignore", "Install.cmd"}
REQUIRED_FILES = {"relay.py", "pyproject.toml", "requirements.txt", "README.md", "relay_collaboration/__init__.py", "Install.cmd"}
ROOT_FILES |= {"QuickStart.cmd"}
REQUIRED_FILES |= {"QuickStart.cmd"}
WEB_FILES = {"app.js", "flow_model.js", "index.html", "style.css", "package.json"}
WEB_FILES |= {"bindings.js", "i18n.js"}
SCRIPT_FILES = {"install.ps1", "install_skills.py", "skill-command.ps1", "start.ps1", "stop.ps1", "status.ps1", "test.ps1", "package.py", "verify-package.py"}
SCRIPT_FILES |= {"build_exe.py", "verify_exe.py", "quickstart.ps1"}
PUBLIC_DOCS = {"CONFIGURATION.md", "PROTOCOL.md", "GUI_CONTRACT.md", "OPERATIONS.md", "MIGRATION.md", "VALIDATION.md", "RELEASE_NOTES.md", "CODEX_APP.md", "SERVICE_TERMS.md", "INSTALLATION.md"}
PUBLIC_DOCS |= {"INSTANCES.md", "AGENT_GUIDE.md", "AGENT_GUIDE.zh-TW.md", "SHARING.md", "QUICKSTART.md", "QUICKSTART.zh-TW.md"}
PUBLIC_DOCS |= {"USER_MANUAL.zh-TW.md", "RELEASE_STATUS.md"}
TEMPLATE_FILES = {"relay.example.json", "relay.file.example.json", "claude-default.example.json", "claude-fable.example.json", "bidirectional.example.json", "codex-app.example.json", "app-cli.example.json"}
REQUIRED_MODULES = {"__init__", "__main__", "bridge_core", "bridge_client", "bridge_server", "worker_core", "cli_executor", "context_dispatch", "console_backend", "console_server", "config", "cli", "service", "privacy", "process_identity", "codex_executor", "app_mailbox"}
REQUIRED_MODULES |= {"execution", "desktop", "skill_install"}
REQUIRED_MODULES |= {"instances", "vision", "quickstart"}
REQUIRED_MODULES |= {"model_selection"}
SKILL_FILES = {"skills/relay-codex-app/SKILL.md", "skills/relay-claude-code/SKILL.md"}
REQUIRED_FILES |= SKILL_FILES | {"README.zh-TW.md", ".gitignore"}
REQUIRED_FILES |= {"relay_collaboration/" + name + ".py" for name in REQUIRED_MODULES}
REQUIRED_FILES |= {"relay_collaboration/web/" + name for name in WEB_FILES}
REQUIRED_FILES |= {"scripts/" + name for name in SCRIPT_FILES}
REQUIRED_FILES |= {"docs/" + name for name in PUBLIC_DOCS}
REQUIRED_FILES |= {"config/" + name for name in TEMPLATE_FILES}


class PackageError(ValueError):
    pass


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def safe_name(name):
    if (not isinstance(name, str) or not name or "\\" in name or ":" in name
            or "\x00" in name or name.startswith("/") or name.endswith("/")):
        raise PackageError("Archive path is invalid")
    parts = name.split("/")
    if any(part in {"", ".", ".."} or part.endswith((" ", ".")) for part in parts):
        raise PackageError("Archive path is unsafe")
    reserved = {"con", "prn", "aux", "nul", *("com" + str(i) for i in range(1, 10)), *("lpt" + str(i) for i in range(1, 10))}
    if any(part.split(".")[0].lower() in reserved or any(ord(char) < 32 for char in part) for part in parts):
        raise PackageError("Archive path contains a reserved name")
    if PurePosixPath(name).as_posix() != name:
        raise PackageError("Archive path is not canonical")
    return name


def allowed(name):
    safe_name(name)
    if name in SKILL_FILES:
        return True
    parts = name.split("/")
    if len(parts) == 1:
        return name in ROOT_FILES
    if parts[0] == "relay_collaboration":
        if len(parts) == 2:
            return parts[1] in {module + ".py" for module in REQUIRED_MODULES}
        return len(parts) == 3 and parts[1] == "web" and parts[2] in WEB_FILES
    if len(parts) == 2 and parts[0] == "scripts":
        return parts[1] in SCRIPT_FILES
    if len(parts) == 2 and parts[0] == "tests":
        return parts[1] == "__init__.py" or bool(re.fullmatch(r"test_[a-z0-9_]+\.(?:py|mjs)", parts[1]))
    if len(parts) == 2 and parts[0] == "docs":
        return parts[1] in PUBLIC_DOCS
    return len(parts) == 2 and parts[0] in {"config", "examples"} and parts[1] in TEMPLATE_FILES


def checked_source(root, path):
    if not path.is_relative_to(root):
        raise PackageError("Package source escapes its root")
    for current in (path, *path.parents):
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise PackageError("Linked or reparse source is not shareable")
        if current == path and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
            raise PackageError("Package source must be a regular unlinked file")
        if current == root:
            break
    return path


def collect(root):
    root = Path(root).absolute()
    candidates = [root / name for name in ROOT_FILES | SKILL_FILES]
    for folder, patterns in (("relay_collaboration", ("*.py",)), ("relay_collaboration/web", ("*",)),
                             ("scripts", ("*",)), ("tests", ("*.py", "*.mjs")),
                             ("docs", ("*.md",)), ("config", ("*.json",)), ("examples", ("*.json",))):
        directory = root / folder
        if directory.exists():
            candidates.extend(path for pattern in patterns for path in directory.glob(pattern))
    selected = {}
    total = 0
    for path in sorted(set(candidates)):
        name = path.relative_to(root).as_posix()
        if not allowed(name) or not path.exists():
            continue
        checked_source(root, path)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise PackageError("Source file exceeds package size bound")
        raw = path.read_bytes()
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise PackageError("Package exceeds size bound")
        if name.lower() in {n.lower() for n in selected}:
            raise PackageError("Case-insensitive path collision")
        selected[name] = raw
    if not REQUIRED_FILES.issubset(selected):
        raise PackageError("Required public release files are missing: " + ", ".join(sorted(REQUIRED_FILES - selected.keys())))
    return selected


def build(root, output_dir):
    source = collect(root)
    output_dir = Path(output_dir).absolute()
    root = Path(root).absolute()
    if not output_dir.is_relative_to(root) or output_dir == root:
        raise PackageError("Release output must be a child of the source root")
    for parent in (output_dir, *output_dir.parents):
        if parent.exists() and (parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction())):
            raise PackageError("Linked release output is not permitted")
        if parent == root:
            break
    output_dir.mkdir(parents=True, exist_ok=True)
    entries = [{"path": name, "size": len(raw), "sha256": digest(raw)} for name, raw in sorted(source.items())]
    manifest = {"schema_version": 1, "package": PACKAGE, "version": VERSION, "files": entries}
    manifest_raw = canonical(manifest)
    archive = output_dir / f"{PACKAGE}-{VERSION}.zip"
    sidecar = archive.with_suffix(".manifest.json")
    if archive.exists() or sidecar.exists():
        raise PackageError("Release output already exists; preserve or explicitly remove the prior build first")
    created = []
    try:
        archive_stream = archive.open("xb")
        created.append(archive)
        with archive_stream, zipfile.ZipFile(archive_stream, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as bundle:
            for name, raw in sorted({**source, "MANIFEST.json": manifest_raw}.items()):
                item = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                item.compress_type = zipfile.ZIP_DEFLATED
                item.create_system = 3
                item.external_attr = (stat.S_IFREG | 0o644) << 16
                bundle.writestr(item, raw, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
        external = {**manifest, "archive": {"filename": archive.name, "size": archive.stat().st_size, "sha256": digest(archive.read_bytes())}, "manifest_sha256": digest(manifest_raw)}
        with sidecar.open("xb") as stream:
            created.append(sidecar)
            stream.write(canonical(external))
    except Exception:
        # Only the two new files owned by this operation are candidates for cleanup.
        for owned_path in created:
            if owned_path.exists():
                owned_path.unlink()
        raise
    return archive, sidecar


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise PackageError("Duplicate manifest key")
            result[key] = value
        return result
    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (ValueError, UnicodeError) as exc:
        raise PackageError("Invalid manifest JSON") from exc


def verify(archive, sidecar=None):
    archive = Path(archive)
    external = None
    if archive.stat().st_size > MAX_TOTAL_BYTES:
        raise PackageError("Archive exceeds size bound")
    if sidecar is not None:
        if Path(sidecar).stat().st_size > MAX_FILE_BYTES:
            raise PackageError("External manifest exceeds size bound")
        external = strict_json(Path(sidecar).read_bytes())
        if not isinstance(external, dict):
            raise PackageError("Invalid external manifest")
        actual = {"filename": archive.name, "size": archive.stat().st_size, "sha256": digest(archive.read_bytes())}
        if external.get("archive") != actual:
            raise PackageError("Archive sidecar digest or size mismatch")
    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            names = [safe_name(item.filename) for item in entries]
            if len(names) > 1000 or len(names) != len(set(name.lower() for name in names)):
                raise PackageError("Duplicate or excessive archive entries")
            total = 0
            for item in entries:
                if item.file_size > MAX_FILE_BYTES or item.flag_bits & 1:
                    raise PackageError("Oversized or encrypted archive entry")
                total += item.file_size
                mode = item.external_attr >> 16
                if stat.S_IFMT(mode) not in (0, stat.S_IFREG):
                    raise PackageError("Archive links and special files are forbidden")
            if total > MAX_TOTAL_BYTES or "MANIFEST.json" not in names:
                raise PackageError("Missing manifest or oversized archive")
            raw = bundle.read("MANIFEST.json")
            manifest = strict_json(raw)
            if set(manifest) != {"schema_version", "package", "version", "files"} or manifest["schema_version"] != 1 or manifest["package"] != PACKAGE or manifest["version"] != VERSION:
                raise PackageError("Unsupported manifest")
            if not isinstance(manifest["files"], list):
                raise PackageError("Invalid manifest file list")
            expected = {}
            for item in manifest["files"]:
                if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
                    raise PackageError("Invalid manifest entry")
                name = safe_name(item["path"])
                if not allowed(name) or name.lower() in {n.lower() for n in expected}:
                    raise PackageError("Nonpublic or duplicate manifest file")
                if type(item["size"]) is not int or item["size"] < 0 or not isinstance(item["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
                    raise PackageError("Invalid manifest hash or size")
                expected[name] = item
            if set(names) != set(expected) | {"MANIFEST.json"}:
                raise PackageError("Missing or extra archive file")
            if not REQUIRED_FILES.issubset(expected):
                raise PackageError("Missing required release file")
            for name, item in expected.items():
                data = bundle.read(name)
                if len(data) != item["size"] or digest(data) != item["sha256"]:
                    raise PackageError("File digest or size mismatch: " + name)
            if external is not None:
                if set(external) != set(manifest) | {"archive", "manifest_sha256"} or any(external.get(key) != value for key, value in manifest.items()) or external.get("manifest_sha256") != digest(raw):
                    raise PackageError("External and embedded manifests differ")
    except (zipfile.BadZipFile, KeyError, TypeError, AttributeError) as exc:
        raise PackageError("Invalid release archive or manifest") from exc
    return {"ok": True, "package": PACKAGE, "version": VERSION, "files": len(expected), "archive_sha256": digest(archive.read_bytes()), "external_manifest_verified": external is not None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        archive, manifest = build(args.root, args.output_dir or args.root / "dist")
        result = verify(archive, manifest)
        print(json.dumps({**result, "archive": str(archive), "manifest": str(manifest)}, indent=2))
        return 0
    except (OSError, PackageError) as exc:
        print("Package failed: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
