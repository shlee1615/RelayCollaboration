"""Guided source setup using existing instance, runtime and service safeguards.

No model task, App attachment, global skill installation or credential copying.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import locale
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid
import webbrowser

from .config import ConfigError, initialize, load_config, require_runtime, safe_path, strict_json
from .context_dispatch import JournalLock
from .execution import package_root
from . import instances, service
from . import model_selection


def data_root(value):
    root = safe_path(value)
    if any((p / '.git').exists() for p in (root, *root.parents)):
        raise ConfigError('Choose a data folder outside Git / 請選擇 Git 儲存庫外的資料目錄')
    if root.exists() and not root.is_dir():
        raise ConfigError('Data folder is not a directory / 資料路徑不是目錄')
    return root


def official_claude(path):
    """Verify Windows publisher before executing an automatically found binary."""
    path = safe_path(path)
    if os.name != 'nt' or path.name.lower() != 'claude.exe' or not path.is_file():
        raise ConfigError('Select the official native claude.exe / 請選擇官方原生 claude.exe')
    powershell = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
    # PowerShell 7 can export its module search path into a Windows PowerShell
    # child. Pin this verifier to the system modules to avoid loading incompatible
    # (or user-provided) implementations of the signature cmdlet.
    environment = {k: v for k, v in os.environ.items() if k.upper() != 'PSMODULEPATH'}
    environment.update(PSModulePath=str(powershell.parent / 'Modules'),
                       RELAY_CLAUDE_CANDIDATE=str(path))
    # The candidate is data in an environment variable, never interpolated code.
    script = "$s = Get-AuthenticodeSignature -LiteralPath $env:RELAY_CLAUDE_CANDIDATE; " \
             "if ($s.Status -eq 'Valid' -and $s.SignerCertificate.Subject -match '(?:^|,\\s*)O=(?:\"Anthropic, PBC\"|Anthropic)(?:,|$)') { exit 0 }; exit 1"
    checked = subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-Command', script],
                             env=environment,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             shell=False, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
    if checked.returncode != 0:
        raise ConfigError('Claude publisher signature is not verified / 無法驗證 Claude 官方發行者簽章')
    return path


def discover_claude():
    """Only known executable locations; never inspect auth or conversation stores."""
    candidates = []
    found = shutil.which('claude.exe')
    if found:
        candidates.append(Path(found))
    candidates.append(Path.home() / '.local/bin/claude.exe')
    roaming = os.environ.get('APPDATA')
    local = os.environ.get('LOCALAPPDATA')
    if roaming:
        candidates.extend((Path(roaming) / 'Claude/claude-code').glob('*/claude.exe'))
    if local:
        candidates.extend((Path(local) / 'Packages').glob('Claude_*/LocalCache/Roaming/Claude/claude-code/*/claude.exe'))
    def version(path):
        return tuple(int(n) for n in re.findall(r'\d+', path.parent.name))
    for candidate in sorted(set(candidates), key=version, reverse=True):
        if not candidate.is_file():
            continue
        try:
            return official_claude(candidate)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            continue
    return None


def _base(root):
    path = root / 'relay.local.json'
    if path.exists():
        return load_config(path)
    # Do not reinterpret an existing differently named catalog as a new one.
    if (root / 'instances').exists():
        raise ConfigError('Existing catalog has no selected base; preserve it / 既有目錄缺少所選基底設定，請保留檢查')
    ports = next(((p, p + 1) for p in range(9341, 65535, 2) if instances._probe_pair((p, p + 1))), None)
    if ports is None:
        raise ConfigError('No available ports / 找不到可用連接埠')
    value = {'schema_version': 1, 'project': {'id': 'relay-quickstart', 'root': './workspace'},
             'runtime': {'root': './private'}, 'endpoints': {
                 'broker': f'http://127.0.0.1:{ports[0]}', 'console': f'http://127.0.0.1:{ports[1]}'},
             'transport': 'http', 'provider': {'kind': 'unavailable'},
             'codex_provider': {'kind': 'codex_app'},
             'worker': {'allowed_paths': [], 'allowed_modes': ['read_only', 'proposal']}}
    with path.open('x', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
    return load_config(path)


def configure_claude(config, executable):
    """Change only a stopped selected profile, under the same lock as start/stop."""
    executable = official_claude(executable)
    require_runtime(config)
    with JournalLock(service._lock_path(config, 'launch')):
        current = load_config(config.path)
        if current.digest != config.digest:
            raise ConfigError('Configuration changed; inspect before retry / 設定已變更，請先檢查')
        if current.provider['kind'] == 'claude_cli' and current.provider['command'] == [str(executable)]:
            return current
        if current.provider['kind'] not in ('unavailable', 'claude_cli'):
            raise ConfigError('Existing provider mode is preserved / 保留既有 provider 模式')
        if service.status(current)['running'] is not False:
            raise ConfigError('Stop this instance before changing Claude / 請先停止這個 instance，再修改 Claude 設定')
        raw = current.path.read_bytes()
        value = strict_json(raw.decode('utf-8-sig'))
        value['provider'] = {**value['provider'], 'kind': 'claude_cli', 'command': [str(executable)]}
        temporary = safe_path(current.path.with_name('.quickstart-' + uuid.uuid4().hex + '.json'))
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        load_config(temporary)  # Validate relative paths and all settings before replacement.
        if current.path.read_bytes() != raw:
            raise ConfigError('Configuration changed; prepared file preserved / 設定已變更，已保留準備檔')
        backup = current.path.with_name('.before-quickstart-' + hashlib.sha256(raw).hexdigest() + '.json')
        if not backup.exists():
            with backup.open('xb') as stream:
                stream.write(raw)
        elif safe_path(backup).read_bytes() != raw:
            raise ConfigError('Configuration backup mismatch / 設定備份不符')
        os.replace(temporary, current.path)
        return load_config(current.path)


def prepare(folder, name='default', claude=None, detect=True):
    name = instances.instance_name(name)
    root = data_root(folder)
    explicit = official_claude(claude) if claude else None
    root.mkdir(parents=True, exist_ok=True)
    with JournalLock(root / '.quickstart'):
        base = _base(root)
        target = safe_path(root / 'instances' / name)
        created = not target.exists()
        if created:
            selected = instances.create(base.path, name)['config']
        else:
            selected = instances.select(base.path, name)
        config = load_config(selected)
        if config.codex_provider['kind'] != 'codex_app':
            raise ConfigError('Selected instance is not in Codex App mode / 所選 instance 不是 Codex App 模式')
        if not config.runtime_root.exists():
            initialize(config)
        else:
            require_runtime(config)  # Do not initialize or adopt an existing runtime.
        candidate = explicit
        if candidate is None and detect and config.provider['kind'] == 'unavailable':
            candidate = discover_claude()
        if candidate:
            config = configure_claude(config, candidate)
        return config, created


def login(config):
    """Official interactive login only, without starting a model conversation."""
    require_runtime(config)
    if config.provider['kind'] != 'claude_cli':
        raise ConfigError('Configure Claude before login / 請先設定 Claude CLI')
    executable = official_claude(config.provider['command'][0])
    with JournalLock(service._lock_path(config, 'launch')):
        if load_config(config.path).digest != config.digest or service.status(config)['running'] is not False:
            raise ConfigError('Stop this instance before login / 請先停止這個 instance 再登入')
        adapter = config.adapter('claude')
        adapter._validate_runtime()
        result = subprocess.run([str(executable), 'auth', 'login'], cwd=config.run_root,
                                env=adapter.environment(), shell=False, stdout=sys.stderr, stderr=sys.stderr)
        if result.returncode != 0:
            raise ConfigError('Official login did not complete; rerun with the same instance / 官方登入未完成，請沿用同一個 instance 重試')
    return config.adapter('claude').preflight()


def connection_instructions(config, language):
    root = package_root()
    if language == 'zh-TW':
        return (f'Codex App：\n請讀取 {root / "skills/relay-codex-app/SKILL.md"} 與 '
                f'{root / "docs/AGENT_GUIDE.zh-TW.md"}。使用完整設定檔 {config.path}，'
                '直接呼叫此套件的 relay.py，不加 --instance。必要時啟動 Relay，連接目前這個真實任務並檢查收件匣。'
                '若已有其他擁有者，先回報衝突，不要自動解除。若有 App heartbeat 工具，設定每五分鐘收件，僅在有重要進展或需要處理時通知。\n\n'
                f'Claude Code：\n請讀取 {root / "skills/relay-claude-code/SKILL.md"} 與 '
                f'{root / "docs/AGENT_GUIDE.zh-TW.md"}。使用相同完整設定檔 {config.path}，'
                '直接呼叫此套件的 relay.py，不加 --instance。檢查 Relay；收到授權問題後派給 Codex，並查原始 receipt。'
                '不要冒用 Codex 任務身分。\n')
    return (f'Codex App:\nRead {root / "skills/relay-codex-app/SKILL.md"} and '
            f'{root / "docs/AGENT_GUIDE.md"}. Use the full config {config.path} with this package\'s relay.py directly, '
            'without --instance. Start Relay if needed, attach this actual task and check its inbox. '
            'Report another owner instead of releasing automatically. If the App heartbeat tool is available, '
            'check every five minutes and notify only on meaningful progress or required action.\n\n'
            f'Claude Code:\nRead {root / "skills/relay-claude-code/SKILL.md"} and '
            f'{root / "docs/AGENT_GUIDE.md"}. Use the same full config {config.path} with this package\'s relay.py directly, '
            'without --instance. Inspect Relay; submit an authorized question to Codex when provided and read '
            'the original receipt. Never impersonate the Codex task.\n')


def snapshot(config, created=False, language='en'):
    from .cli import doctor
    state = service.status(config)
    checked = doctor(config)
    from .app_mailbox import AppMailbox
    app = AppMailbox(config).status()
    instructions = connection_instructions(config, language)
    note = safe_path(config.path.parent / ('CONNECT.' + language + '.txt'))
    try:
        with note.open('x', encoding='utf-8', newline='\n') as stream:
            stream.write(instructions)
    except FileExistsError:
        pass  # A user's existing note is never overwritten.
    note_matches = note.stat().st_size <= 65536 and note.read_text(encoding='utf-8') == instructions
    return {'ok': state.get('ok') is True and checked['runtime_ready'], 'created': created,
            'config': str(config.path), 'console_url': config.console_url,
            'runtime_id': require_runtime(config)['runtime_id'], 'service': state,
            'claude': checked['provider_statuses']['claude'], 'codex': app,
            'models': model_selection.describe(config)['providers'],
            'ready_for_both_directions': bool(state.get('running') and
                 checked['provider_statuses']['claude'].get('ok') and app.get('connected')),
            'connection_file': str(note) if note_matches else None,
            'connection_instructions': instructions, 'model_acceptance': 'not_run'}


def choose_model(config, actor, language, ask=input, emit=print):
    """Keep existing settings on Enter; never stop a service implicitly."""
    info = model_selection.describe(config, actor)['providers'][actor]
    if not info['editable']:
        emit(info['guidance'])
        return config
    zh = language == 'zh-TW'
    current = info['model'] or ('CLI 預設' if zh else 'CLI default')
    emit(f'{actor}: {current}; effort: {info["effort"] or "default"}')
    if service.status(config)['running'] is not False:
        emit('此 instance 尚未確認停止；保留模型。正常停止後可重新選擇。' if zh else
             'This instance is not confirmed stopped; preserving its model. Stop it normally to change models.')
        return config
    presets = model_selection.PRESETS[actor]
    for index, (value, label) in enumerate(presets, 1):
        emit(f'  {index}. {label} ({value})')
    answer = ask('選擇模型編號或輸入完整 ID／alias；Enter 保留目前設定：' if zh else
                 'Choose a model number or enter an exact ID/alias; Enter keeps the current setting: ').strip()
    if not answer:
        return config
    if answer.isdecimal():
        if not 1 <= int(answer) <= len(presets):
            raise ConfigError('Invalid model selection / 模型選項無效')
        answer = presets[int(answer) - 1][0]
    effort = ask('推理強度（default 使用預設；Enter 保留目前設定）：' if zh else
                 'Reasoning effort (default resets; Enter preserves the current setting): ').strip() or None
    model_selection.configure(config, actor, model=answer, effort=effort)
    return load_config(config.path)


def wizard(args, ask=input, emit=print):
    def message(zh, en):
        return zh if args.language == 'zh-TW' else en
    if not args.language:
        system_language = (locale.getlocale()[0] or '').lower()
        default = '1' if system_language.startswith(('zh', 'chinese')) else '2'
        answer = ask(f'1 繁體中文 / 2 English [{default}]: ').strip() or default
        if answer not in ('1', '2'):
            raise ConfigError('Choose 1 or 2 / 請選擇 1 或 2')
        args.language = 'zh-TW' if answer == '1' else 'en'
    emit(message('Relay 快速啟動：同一名稱會沿用原 instance，新名稱建立獨立環境。',
                 'Relay quick start: reuse a name to resume its instance; a new name creates an independent one.'))
    folder = args.data_dir or str(package_root() / 'local-data')
    folder = ask(message(f'資料目錄 [{folder}]: ', f'Data folder [{folder}]: ')).strip().strip('"') or folder
    data_root(folder)
    base_path = safe_path(Path(folder) / 'relay.local.json')
    if base_path.exists():
        items = instances.list_instances(base_path)['instances']
        for item in items:
            emit(f'  {item["name"]}: {item["state"]}  {item.get("console_url", "")}')
    name = ask(message(f'Instance 名稱 [{args.name}]: ', f'Instance name [{args.name}]: ')).strip() or args.name
    emit(message('正在準備環境並尋找官方 Claude CLI…', 'Preparing the instance and locating the official Claude CLI…'))
    config, created = prepare(folder, name, args.claude, not args.no_detect)
    if config.provider['kind'] == 'unavailable':
        chosen = ask(message('找不到已驗證的 Claude。輸入官方 claude.exe 完整路徑，或 Enter 稍後設定：',
                             'No verified Claude found. Enter the full official claude.exe path, or Enter to configure later: ')).strip()
        if chosen:
            config = configure_claude(config, chosen.strip('"'))
    if getattr(args, 'model', None) is not None or getattr(args, 'effort', None) is not None:
        model_selection.configure(config, 'claude', model=args.model, effort=args.effort)
        config = load_config(config.path)
    else:
        config = choose_model(config, 'claude', args.language, ask, emit)
    config = choose_model(config, 'codex', args.language, ask, emit)
    current = snapshot(config, created, args.language)
    if config.provider['kind'] == 'claude_cli' and not current['claude'].get('auth_ready'):
        if current['service']['running'] is False:
            reply = ask(message('現在開啟這個 instance 的官方 Claude 登入？[Y/n]: ',
                                'Open official Claude login for this instance now? [Y/n]: ')).strip().lower()
            if reply in ('', 'y', 'yes'):
                login(config)
        else:
            emit(message('此 instance 正在執行；請正常停止後再登入。',
                         'This instance is running; stop it normally before signing in.'))
    state = service.start(config)
    if state.get('running') is not True or state.get('ok') is False:
        raise ConfigError('Service did not start; inspect this instance / 服務未啟動，請檢查這個 instance')
    current = snapshot(config, created, args.language)
    emit(message('控制台已啟動。請把以下兩段指示分別貼到目標 LLM：',
                 'Console started. Paste each instruction into its intended LLM:'))
    emit(current['connection_instructions'])
    if current['connection_file']:
        emit(message('連線說明檔：', 'Connection note: ') + current['connection_file'])
    else:
        emit(message('原說明檔已被修改，已保留；請使用上方目前的指示。',
                     'Existing note was edited and preserved; use the current instructions above.'))
    emit(message('Claude 就緒：', 'Claude ready: ') + str(current['claude'].get('ok') is True))
    emit(message('Codex 已連接：', 'Codex connected: ') + str(current['codex'].get('connected') is True))
    emit(current['console_url'])
    if not args.no_browser:
        webbrowser.open(current['console_url'])
    return current


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', help='Dedicated data folder outside Git; defaults to <package>/local-data')
    parser.add_argument('--name', default='default', help='Same name resumes; a new name creates an independent instance')
    parser.add_argument('--claude', help='Explicit official native claude.exe path; changes require a stopped instance')
    parser.add_argument('--model', help='Claude model ID/alias; default clears the override; omit to preserve')
    parser.add_argument('--effort', help='Claude effort; default clears the override; omit to preserve')
    parser.add_argument('--no-detect', action='store_true', help='Skip automatic executable discovery')
    parser.add_argument('--language', choices=('en', 'zh-TW'))
    parser.add_argument('--wizard', action='store_true')
    parser.add_argument('--login', action='store_true', help='Run official interactive login for the selected stopped instance')
    parser.add_argument('--start', action='store_true', help='Start the selected hidden service')
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args(argv)
    try:
        if args.wizard:
            result = wizard(args)
        else:
            config, created = prepare(args.data_dir or package_root() / 'local-data', args.name,
                                      args.claude, not args.no_detect)
            if args.model is not None or args.effort is not None:
                model_selection.configure(config, 'claude', model=args.model, effort=args.effort)
                config = load_config(config.path)
            if args.login:
                login(config)
            if args.start:
                state = service.start(config)
                if state.get('running') is not True or state.get('ok') is False:
                    raise ConfigError('Service did not start / 服務未啟動')
            result = snapshot(config, created, args.language or 'en')
        if not args.wizard:
            print(json.dumps(result, ensure_ascii=False))
        return 0 if result['ok'] else 2
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
        # Do not reflect third-party command output or auth data into diagnostics.
        error = str(exc) if isinstance(exc, ConfigError) else type(exc).__name__ + ': inspect the selected instance; existing data preserved'
        print(json.dumps({'ok': False, 'error': error}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (KeyboardInterrupt, EOFError):
        print('Cancelled; existing data retained / 已取消，保留既有資料', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())
