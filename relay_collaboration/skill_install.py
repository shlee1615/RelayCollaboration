"""Install local Relay skills with per-machine bindings; no credentials or downloads."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import uuid
from . import __version__
from .config import load_config
from .execution import frozen, package_root, resource_root

PACKAGE = "relay-collaboration"
NAMES = {"codex": "relay-codex-app", "claude": "relay-claude-code"}
MANIFEST = ".relay-managed.json"
FILES = {"SKILL.md", "run-relay.ps1", "installation.json"}


class InstallError(ValueError):
    pass


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def plain_path(path):
    """Do not traverse links/junctions or replace hardlinked files."""
    path = Path(os.path.abspath(path))
    for item in (path, *path.parents):
        if not os.path.lexists(item):
            continue
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise InstallError("Linked/reparse installation path is not supported: " + str(item))
        if item.is_file() and info.st_nlink != 1:
            raise InstallError("Hardlinked installation file is not supported: " + str(item))
    return path


def defaults():
    codex_root = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
    return {"codex": codex_root / "skills", "claude": Path.home() / ".claude" / "skills"}


def prepare(root, config, python, agent, skills_dir, rebind=False, executable=False, resources=None):
    root = plain_path(root)
    config = plain_path(config)
    python = plain_path(python)
    if (not executable and not (root / "relay.py").is_file()) or not config.is_file() or not python.is_file():
        raise InstallError("Package, configuration and command executable must exist before installing skills")
    if executable and python.parent != root:
        raise InstallError("Relay executable must be inside the selected installation root")
    if not config.is_relative_to(root):
        raise InstallError("Select a configuration inside this Relay installation")
    # Validate the selected profile without initializing a runtime or accessing auth.
    load_config(config)
    name = NAMES[agent]
    resources = plain_path(resources) if resources is not None else root
    source = plain_path(resources / "skills" / name / "SKILL.md")
    launcher = plain_path(resources / "scripts" / "skill-command.ps1")
    target = plain_path(Path(skills_dir) / name)
    # Never install over the release templates themselves.
    if target == source.parent or source.is_relative_to(target):
        raise InstallError("The installed skill must be separate from release templates")
    binding = {"schema_version": 1, "package": PACKAGE, "version": __version__,
               "agent": agent, "package_root": str(root), "config": str(config),
               ("executable" if executable else "python"): str(python)}
    new = {"SKILL.md": source.read_bytes(), "run-relay.ps1": launcher.read_bytes(),
           "installation.json": encoded(binding)}
    old = {}
    if target.exists():
        if not target.is_dir():
            raise InstallError("Skill destination is not a directory: " + str(target))
        for path in target.iterdir():
            plain_path(path)
        if {p.name for p in target.iterdir()} != FILES | {MANIFEST}:
            raise InstallError("Existing skill is unmanaged or has extra files; preserved: " + str(target))
        old = {name: plain_path(target / name).read_bytes() for name in FILES | {MANIFEST}}
        try:
            receipt = json.loads(old[MANIFEST])
            prior = json.loads(old["installation.json"])
            if (receipt.get("package") != PACKAGE or receipt.get("schema_version") != 1
                    or receipt.get("files") != {name: sha(old[name]) for name in FILES}):
                raise ValueError("modified skill")
            if not rebind and (prior.get("package_root") != str(root) or prior.get("config") != str(config)):
                raise InstallError("Skill is bound to another installation/config; use --rebind to switch explicitly")
        except (ValueError, TypeError, AttributeError) as exc:
            if isinstance(exc, InstallError):
                raise
            raise InstallError("Existing skill was modified or has an invalid receipt; preserved: " + str(target)) from exc
    new[MANIFEST] = encoded({"schema_version": 1, "package": PACKAGE,
                             "files": {name: sha(raw) for name, raw in new.items()}})
    return {"agent": agent, "target": target, "new": new, "old": old}


def atomic_write(path, raw):
    temporary = path.with_name("." + path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def install(root, config, python, targets, rebind=False, executable=False, resources=None):
    # Preflight every destination before writing either agent's skill.
    plans = [prepare(root, config, python, agent, folder, rebind, executable, resources) for agent, folder in targets.items()]
    changed = []
    try:
        for plan in plans:
            if plan["new"] == plan["old"]:
                continue
            target = plan["target"]
            target.mkdir(parents=True, exist_ok=True)
            changed.append(plan)
            for name, raw in plan["new"].items():
                atomic_write(target / name, raw)
    except OSError:
        # Only our fixed file names are restored; never recursively remove a tree.
        for plan in reversed(changed):
            for name in plan["new"]:
                path = plan["target"] / name
                if name in plan["old"]:
                    atomic_write(path, plan["old"][name])
                elif path.exists():
                    path.unlink()
            if not plan["old"]:
                plan["target"].rmdir()
        raise
    return [{"agent": plan["agent"], "path": str(plan["target"]),
             "state": "unchanged" if plan["new"] == plan["old"] else "installed"} for plan in plans]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--agents", choices=["both", "codex", "claude"], default="both")
    parser.add_argument("--codex-skills-dir", type=Path, default=defaults()["codex"])
    parser.add_argument("--claude-skills-dir", type=Path, default=defaults()["claude"])
    parser.add_argument("--rebind", action="store_true", help="Switch a managed unmodified skill to this installation/config")
    args = parser.parse_args(argv)
    targets = {agent: getattr(args, agent + "_skills_dir") for agent in NAMES
               if args.agents in ("both", agent)}
    try:
        result = install(package_root(), args.config, Path(sys.executable), targets, args.rebind,
                         executable=frozen(), resources=resource_root())
        print(json.dumps({"ok": True, "skills": result}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError) as exc:
        print("Skill installation failed: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
