"""Explicit per-provider model selection; never dispatch or change App settings."""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid

from .config import ConfigError, load_config, require_runtime, safe_path, strict_json
from .context_dispatch import JournalLock
from . import service


# Convenience presets, not a query of account entitlements. Custom IDs remain supported.
PRESETS = {
    'claude': (('default', 'CLI default / CLI 預設'),
               ('claude-opus-5-5', 'Claude Opus 5.5'),
               ('fable', 'Claude Fable (alias)'), ('opus', 'Claude Opus (alias)'),
               ('sonnet', 'Claude Sonnet (alias)'), ('haiku', 'Claude Haiku (alias)')),
    'codex': (('default', 'CLI default / CLI 預設'),
              ('gpt-6-astra', 'GPT-6 Astra'), ('gpt-6-sol', 'GPT-6 Sol'),
              ('gpt-6-luna', 'GPT-6 Luna')),
}
EFFORTS = {'claude': ('low', 'medium', 'high', 'xhigh', 'max'),
           'codex': ('minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra')}


def describe(config, actor=None):
    actors = (actor,) if actor else ('claude', 'codex')
    providers = {}
    for name in actors:
        provider = config.provider_for(name)
        kind = provider['kind']
        editable = kind == name + '_cli'
        owner = 'relay_config' if editable else 'codex_app' if kind == 'codex_app' else 'claude_web' if kind == 'claude_web' else 'unavailable'
        providers[name] = {
            'kind': kind, 'model': provider['model'], 'effort': provider['effort'],
            'selection_owner': owner, 'editable': editable,
            'selection': ('explicit' if provider['model'] else 'cli_default') if editable else 'external' if kind != 'unavailable' else 'unavailable',
            'presets': [{'value': value, 'label': label} for value, label in PRESETS[name]] if editable else [],
            'effort_options': ['default', *EFFORTS[name]] if editable else [],
            'custom_model_supported': editable,
            'guidance': ('Stop this instance, then use model set / 先停止此 instance，再用 model set' if editable else
                         'Choose the model in the bound Codex App task / 請在已綁定的 Codex App 任務中選擇模型' if kind == 'codex_app' else
                         'Choose the model in Claude web / 請在 Claude 網頁選擇模型' if kind == 'claude_web' else
                         'Configure a CLI provider first / 請先配置 CLI provider'),
        }
    return {'ok': True, 'config': str(config.path), 'providers': providers,
            'catalog_checked_on': '2026-09-23', 'availability_verified': False,
            'model_acceptance': 'not_run'}


def configure(config, actor, *, model=None, effort=None):
    """None preserves a field; the literal 'default' resets it to JSON null."""
    provider = config.provider_for(actor)
    if provider['kind'] != actor + '_cli':
        raise ConfigError(describe(config, actor)['providers'][actor]['guidance'])
    if model is None and effort is None:
        raise ConfigError('Specify --model or --effort / 請指定 --model 或 --effort')
    if model is not None:
        pattern = r'[A-Za-z0-9_][A-Za-z0-9_.:\[\]-]{0,159}' if actor == 'claude' else r'[A-Za-z0-9_][A-Za-z0-9_.:-]{0,159}'
        if not isinstance(model, str) or not re.fullmatch(pattern, model):
            raise ConfigError('Invalid model ID / 模型名稱格式無效')
    if effort is not None and effort not in ('default', *EFFORTS[actor]):
        raise ConfigError('Unsupported effort for this provider / 此 provider 不支援此 effort')
    require_runtime(config)
    with JournalLock(service._lock_path(config, 'launch')):
        current = load_config(config.path)
        if current.digest != config.digest:
            raise ConfigError('Configuration changed; inspect before retry / 設定已變更，請先檢查')
        changes = {key: None if value == 'default' else value
                   for key, value in (('model', model), ('effort', effort)) if value is not None}
        if all(current.provider_for(actor)[key] == value for key, value in changes.items()):
            return {**describe(current, actor), 'changed': False, 'backup': None}
        if service.status(current)['running'] is not False:
            raise ConfigError('Stop this instance before changing its model / 請先正常停止此 instance，再切換模型')
        raw = current.path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != current.digest:
            raise ConfigError('Configuration changed; inspect before retry / 設定已變更，請先檢查')
        value = strict_json(raw.decode('utf-8-sig'))
        key = 'provider' if actor == 'claude' else 'codex_provider'
        value[key].update(changes)
        # Keep the source encoding/BOM and newline convention.
        newline = '\r\n' if b'\r\n' in raw else '\n'
        encoded = (json.dumps(value, ensure_ascii=False, indent=2) + '\n').replace('\n', newline).encode('utf-8')
        if raw.startswith(b'\xef\xbb\xbf'):
            encoded = b'\xef\xbb\xbf' + encoded
        temporary = safe_path(current.path.with_name('.model-' + uuid.uuid4().hex + '.json'))
        with temporary.open('xb') as stream:
            stream.write(encoded); stream.flush(); os.fsync(stream.fileno())
        load_config(temporary)
        if current.path.read_bytes() != raw:
            raise ConfigError('Configuration changed; prepared file preserved / 設定已變更，已保留準備檔')
        backup = safe_path(current.path.with_name('.before-model-' + current.digest + '.json'))
        if not backup.exists():
            with backup.open('xb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        elif backup.read_bytes() != raw:
            raise ConfigError('Configuration backup mismatch / 設定備份不符')
        if current.path.read_bytes() != raw:
            raise ConfigError('Configuration changed; prepared file preserved / 設定已變更，已保留準備檔')
        os.replace(temporary, current.path)
        return {**describe(load_config(current.path), actor), 'changed': True,
                'backup': str(backup), 'applies_on': 'next_start',
                'pending_tasks': 'Unclaimed tasks use the selected model after start'}
