"""Named independent profiles. Never clone runtime, auth, queues or histories."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import socket
from urllib.parse import urlsplit
import uuid

from .config import ConfigError, load_config, safe_path, strict_json
from .context_dispatch import JournalLock


def instance_name(value):
    if (not isinstance(value, str) or not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,47}', value) or
            value in {'con', 'prn', 'aux', 'nul', *(f'com{i}' for i in range(1, 10)), *(f'lpt{i}' for i in range(1, 10))}):
        raise ConfigError('Instance name must be 1..48 lowercase ASCII letters, digits, underscores or hyphens; no reserved names')
    return value


def catalog_path(base_config):
    # Resolve relative to the explicit base configuration, never process cwd.
    base = safe_path(base_config)
    if base.name == 'relay.local.json' and base.parent.parent.name == 'instances':
        raise ConfigError('Use the original base configuration for instances create/list/selection; nested catalogs are not supported')
    return safe_path(base.parent / 'instances')


def select(base_config, name):
    path = safe_path(catalog_path(base_config) / instance_name(name) / 'relay.local.json')
    if not path.is_file():
        raise ConfigError('Named instance does not exist; use instances create first')
    _validate_paths(load_config(path), path.parent)
    return path


def _validate_paths(config, folder):
    if (config.runtime_root != safe_path(folder / 'private') or
            config.project_root != safe_path(folder / 'workspace')):
        raise ConfigError('Named instance paths changed; restore the original instance paths before selecting it')


def _configs(base_config):
    root = catalog_path(base_config)
    if root.exists():
        for folder in sorted(root.iterdir()):
            if folder.name.startswith('.'):
                continue
            instance_name(folder.name)
            path = safe_path(folder / 'relay.local.json')
            if not path.is_file():
                raise ConfigError('Incomplete instance directory; preserve it and inspect before creating another instance')
            config = load_config(path)
            # Named instances cannot be edited into aliases for another runtime.
            _validate_paths(config, folder)
            yield folder.name, config


def _probe_pair(ports):
    listeners = []
    try:
        for port in ports:
            listener = socket.socket()
            listeners.append(listener)
            if os.name == 'nt':
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(('127.0.0.1', port))
        return True
    except OSError:
        return False
    finally:
        for listener in listeners:
            listener.close()


def create(base_config, name, broker_port=None, console_port=None):
    name = instance_name(name)
    base = load_config(base_config)
    root = catalog_path(base.path)
    root.mkdir(exist_ok=True)
    with JournalLock(root / '.catalog'):
        _register_base(base, root)
        folder = safe_path(root / name)
        if folder.exists():
            raise ConfigError('Instance already exists; select it with --instance. Creation never overwrites an instance')
        reserved = {urlsplit(url).port for url in (base.broker_url, base.console_url)}
        for _, config in _configs(base.path):
            reserved.update(urlsplit(url).port for url in (config.broker_url, config.console_url))
        if (broker_port is None) != (console_port is None):
            raise ConfigError('Specify both broker-port and console-port, or neither')
        if broker_port is not None:
            ports = (broker_port, console_port)
            if any(type(p) is not int or not 1024 <= p <= 65535 for p in ports) or ports[0] == ports[1]:
                raise ConfigError('Instance ports must be distinct integers in 1024..65535')
            if reserved.intersection(ports) or not _probe_pair(ports):
                raise ConfigError('Instance port is reserved by a profile or occupied; existing services were not changed')
        else:
            ports = next(((p, p + 1) for p in range(9241, 65535, 2)
                          if not reserved.intersection((p, p + 1)) and _probe_pair((p, p + 1))), None)
            if ports is None:
                raise ConfigError('No free instance port pair was found')
        # Only nonsecret CLI configuration is selected from the base profile.
        # The official CLI executable may be shared, never its authentication.
        provider = ({k: base.provider[k] for k in ('kind', 'command', 'model', 'effort', 'auth_profile')}
                    if base.provider['kind'] == 'claude_cli' else {'kind': 'unavailable'})
        value = {'schema_version': 1, 'project': {'id': 'relay-' + name, 'root': './workspace'},
                 'runtime': {'root': './private'}, 'endpoints': {
                     'broker': f'http://127.0.0.1:{ports[0]}', 'console': f'http://127.0.0.1:{ports[1]}'},
                 'transport': 'http', 'provider': provider, 'codex_provider': {'kind': 'codex_app'},
                 'worker': {'allowed_paths': [], 'allowed_modes': ['read_only', 'proposal']}}
        # A crash leaves only a hidden staging directory; it cannot publish a
        # partial named instance or overwrite an existing configuration.
        stage = safe_path(root / ('.new-' + name + '-' + uuid.uuid4().hex))
        stage.mkdir()
        temporary = stage / 'relay.local.json'
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        load_config(temporary)
        destination = safe_path(folder / 'relay.local.json')
        if folder.exists():
            raise ConfigError('Instance appeared during creation; preserve the staged configuration for inspection')
        os.rename(stage, folder)
        config = load_config(destination)
        return {'ok': True, 'name': name, 'config': str(destination), 'initialized': False,
                'runtime_root': str(config.runtime_root), 'workspace': str(config.project_root),
                'console_url': config.console_url, 'broker_url': config.broker_url,
                'claude_login': 'fresh_official_login_required' if provider['kind'] == 'claude_cli' else 'provider_unavailable',
                'next': ['--instance ' + name + ' init', '--instance ' + name + ' start'],
                'codex_app_step': '--instance ' + name + ' app attach'}


def list_instances(base_config):
    from .service import status
    items = []
    root = catalog_path(base_config)
    for folder in sorted(root.iterdir()) if root.exists() else ():
        if folder.name.startswith('.'):
            continue
        try:
            instance_name(folder.name)
            config = load_config(safe_path(folder / 'relay.local.json'))
            _validate_paths(config, folder)
            initialized = config.runtime_root.exists()
            state = status(config) if initialized else {'state': 'not_initialized', 'running': False}
            items.append({'name': folder.name, 'config': str(config.path), 'runtime_root': str(config.runtime_root),
                          'console_url': config.console_url, 'broker_url': config.broker_url,
                          'initialized': initialized, **{k: state.get(k) for k in ('state', 'running', 'pid')}})
        except (OSError, ValueError):
            items.append({'name': folder.name, 'state': 'invalid_or_incomplete', 'running': None,
                          'error': 'Preserve this entry for inspection; other instances remain selectable'})
    return {'ok': True, 'base_config': str(safe_path(base_config)), 'instances': items}


def _read_base(root):
    path = safe_path(root / '.base-config.json')
    if not path.exists():
        return None
    if path.stat().st_size > 4096:
        raise ConfigError('Invalid instance catalog registration')
    value = strict_json(path.read_text(encoding='utf-8'))
    name = value.get('base_config') if isinstance(value, dict) else None
    if (not isinstance(value, dict) or set(value) != {'schema', 'base_config'} or value['schema'] != 'relay-instance-catalog/v1' or
            not isinstance(name, str) or name in ('', '.', '..') or any(c in name for c in '/\\:') or
            not name.endswith('.json') or name.endswith((' ', '.'))):
        raise ConfigError('Invalid instance catalog registration')
    return load_config(safe_path(root.parent / name))


def _register_base(base, root):
    prior = _read_base(root)
    if prior is not None:
        if prior.path != base.path:
            raise ConfigError('Instance catalog is registered to another base configuration; preserve it for inspection')
        return
    target = safe_path(root / '.base-config.json')
    temporary = root / ('.base-config-' + uuid.uuid4().hex + '.tmp')
    with temporary.open('x', encoding='utf-8') as stream:
        json.dump({'schema': 'relay-instance-catalog/v1', 'base_config': base.path.name}, stream)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temporary, target)


def register(base_config):
    base = load_config(base_config)
    root = catalog_path(base.path)
    root.mkdir(exist_ok=True)
    with JournalLock(root / '.catalog'):
        _register_base(base, root)
    return {'ok': True, 'base_config': str(base.path), 'catalog': str(root), 'runtime_changed': False}


def overview(config):
    """Bounded metadata inventory of the configured catalog, including its base.

    No port scanning, provider login, model execution or App-history reads.
    Mutations are performed only after opening a profile's own console.
    """
    from .service import status
    from .app_mailbox import AppMailbox
    named = config.path.name == 'relay.local.json' and config.path.parent.parent.name == 'instances'
    root = safe_path(config.path.parent.parent if named else catalog_path(config.path))
    items, catalog_error = [], None
    configs = []
    try:
        base = _read_base(root) if named else config
        if base is not None:
            configs.append(('base', base))
    except (ValueError, OSError):
        catalog_error = 'base_registration_invalid'
    if named and not configs and catalog_error is None:
        catalog_error = 'base_not_registered'
    folders = sorted(root.iterdir()) if root.exists() else []
    for folder in [p for p in folders if not p.name.startswith('.')][:100]:
        try:
            instance_name(folder.name)
            value = load_config(safe_path(folder / 'relay.local.json'))
            _validate_paths(value, folder)
            configs.append((folder.name, value))
        except (ValueError, OSError):
            items.append({'name': folder.name, 'state': 'invalid_or_incomplete', 'running': None})
    if not any(c.path == config.path for _, c in configs):
        configs.insert(0, (config.path.parent.name if named else 'base', config))
    for name, value in configs:
        item = {'name': name, 'project': value.project, 'config': str(value.path),
                'console_url': value.console_url, 'current': value.path == config.path,
                'app': None, 'state': 'unknown', 'running': None}
        try:
            if value.runtime_root.exists():
                snapshot = status(value)
                item.update({k: snapshot.get(k) for k in ('running', 'state')})
                if value.codex_provider['kind'] == 'codex_app':
                    item['app'] = AppMailbox(value).status()
            else:
                item.update(state='not_initialized', running=False)
        except (ValueError, OSError, RuntimeError):
            item['inspection_error'] = 'state_unavailable'
        items.append(item)
    return {'ok': True, 'instances': items, 'catalog_error': catalog_error,
            'truncated': len([p for p in folders if not p.name.startswith('.')]) > 100}
