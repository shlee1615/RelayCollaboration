"""First-run setup and browser launch; models remain explicitly configured."""
from __future__ import annotations
import os
from pathlib import Path
import sys
import webbrowser
from .config import ConfigError, initialize, safe_path
from .execution import package_root, resource_root, frozen


def ensure_profile(path):
    path = safe_path(path)
    if path.exists():
        return path
    if not path.parent.is_dir():
        raise ConfigError('Choose an existing writable folder for the configuration')
    raw = (resource_root() / 'config/codex-app.example.json').read_bytes()
    try:
        with path.open('xb') as stream:
            stream.write(raw)
    except FileExistsError:
        pass  # Another launch created it; strict config loading still follows.
    return path


def launch(config, open_browser=True):
    from . import service
    initialize(config)
    result = service.start(config)
    if result.get('running') is not True or result.get('ok') is False:
        return result
    opened = False
    if open_browser:
        try:
            opened = bool(webbrowser.open(config.console_url))
        except OSError:
            pass
    return {**result, 'browser_opened': opened}


def install(config, args):
    from . import skill_install
    if not config.path.is_relative_to(package_root()):
        raise ConfigError('Install selects a configuration inside this Relay installation')
    targets = {agent: getattr(args, agent + '_skills_dir') for agent in skill_install.NAMES
               if args.agents in ('both', agent)}
    # Validate both destinations before initializing anything or writing a skill.
    for agent, folder in targets.items():
        skill_install.prepare(package_root(), config.path, Path(sys.executable), agent, folder,
                              args.rebind, frozen(), resource_root())
    runtime = initialize(config)
    installed = skill_install.install(package_root(), config.path, Path(sys.executable), targets,
                                     args.rebind, frozen(), resource_root())
    return {'ok': True, 'runtime': runtime, 'skills': installed, 'service_started': False,
            'config': str(config.path), 'console_url': config.console_url}
