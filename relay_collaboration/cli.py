"""Local Relay CLI. JSON results; durable IDs precede all task submissions."""
from __future__ import annotations
import argparse
import hashlib
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import sys
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler, HTTPCookieProcessor

from . import __version__
from .config import ConfigError, load_config, initialize, require_runtime, safe_path, strict_json
from .bridge_server import atomic_json, private_permissions
from .console_backend import Reviews, ConsoleError, canonical, atomic
from .context_dispatch import JournalLock
from .execution import frozen, package_root, configure_process


class CLIError(ValueError):
    pass


class JSONArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        # Do not reflect raw arguments: they could contain private payloads.
        raise CLIError('Invalid command arguments; use --help. Place --instance NAME before the subcommand')


def valid_uuid(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
    except (ValueError, AttributeError) as exc:
        raise CLIError('request_id must be a canonical UUID; retain it for every retry') from exc
    return value


def payload_bytes(value):
    if value.lstrip().startswith('{'):
        raw = value.encode('utf-8')
    else:
        path = safe_path(value[1:] if value.startswith('@') else value)
        if path.stat().st_size > 262144:
            raise CLIError('Payload exceeds 256 KiB')
        raw = path.read_bytes()
    if len(raw) > 262144:
        raise CLIError('Payload exceeds 256 KiB')
    return raw


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class ConsoleSession:
    def __init__(self, config, timeout=15):
        self.origin, self.timeout = config.console_url, timeout
        self.profile_id = require_runtime(config)['runtime_id']
        self.config_digest = config.digest
        self.opener = build_opener(ProxyHandler({}), NoRedirect(), HTTPCookieProcessor(CookieJar()))
        self.csrf = None
        self.capabilities = []

    def _request(self, path, raw=None, headers=None):
        request = Request(self.origin + path, data=raw, method='POST' if raw is not None else 'GET',
                          headers=headers or {})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                body = response.read(4 * 1024 * 1024 + 1)
        except HTTPError as exc:
            if raw is None and path.startswith('/api/review/'):
                raise CLIError(f'Console receipt read rejected: HTTP {exc.code}; check --instance and the original request ID. No retry was submitted') from None
            if path == '/api/app':
                from .app_mailbox import APP_DIAGNOSTICS
                code = None
                try:
                    error_body = exc.read(8193)
                    if len(error_body) <= 8192:
                        error = strict_json(error_body.decode('utf-8')).get('error', {})
                        code = error.get('code')
                        # Compatibility with 0.5.0's generic App error wrapper.
                        if code == 'app_command_rejected':
                            code = error.get('message', '').split(':', 1)[0]
                except (ValueError, AttributeError, UnicodeError, OSError):
                    pass
                if isinstance(code, str) and code in APP_DIAGNOSTICS:
                    raise CLIError(f'Console rejected App request: HTTP {exc.code}; {code}: {APP_DIAGNOSTICS[code]}') from None
                raise CLIError(f'Console rejected App request: HTTP {exc.code}; inspect app status. '
                               'For an uncertain mutation, retry the original operation and saved file.') from None
            # Do not reflect arbitrary server response text into the CLI.
            raise CLIError(f'Console rejected request: HTTP {exc.code}; retain the same UUID and payload') from None
        except (URLError, TimeoutError, ConnectionError, OSError):
            if raw is None and path.startswith('/api/review/'):
                raise CLIError('Receipt could not be read; check the selected instance and service, then repeat this read') from None
            if path == '/api/app':
                raise CLIError('App response is uncertain; retry the original operation with the same IDs and saved file') from None
            raise CLIError('Console response is uncertain; retain the same UUID and payload and use retry') from None
        if len(body) > 4 * 1024 * 1024:
            raise CLIError('Console response exceeds limit')
        result = strict_json(body.decode('utf-8'))
        if not isinstance(result, dict):
            raise CLIError('Console returned an invalid response')
        return result

    def bootstrap(self):
        if self.csrf is None:
            session = self._request('/api/session')
            if session.get('profile_id') != self.profile_id:
                raise CLIError('Console profile identity does not match this private runtime; refusing to submit')
            if session.get('config_sha256') != self.config_digest:
                raise CLIError('Console is running a different configuration; stop it before changing the configuration')
            self.csrf = session.get('csrf')
            self.capabilities = session.get('capabilities', [])
            if not isinstance(self.csrf, str) or not self.csrf:
                raise CLIError('Console session bootstrap failed')
        return self

    def post(self, path, payload):
        self.bootstrap()
        return self._request(path, payload, {'Content-Type': 'application/json', 'Origin': self.origin,
                             'X-CSRF-Token': self.csrf})

    def require_capability(self, capability):
        if not isinstance(self.capabilities, list) or capability not in self.capabilities:
            raise CLIError('service_upgrade_required: this running instance has not loaded the release API. '
                           'After its work is idle, stop/start this selected instance using the updated package; '
                           'keep its configuration and private runtime. No release was sent')


def _record_payload(config, request_id, raw, kind='review'):
    """Persist original UTF-8 bytes before transport; reject identity conflicts."""
    request_id = valid_uuid(request_id)
    folder = safe_path(config.runtime_root / 'cli-submissions' / request_id)
    folder.mkdir(exist_ok=True)
    private_permissions(folder, directory=True)
    parsed = strict_json(raw.decode('utf-8-sig'))
    digest = hashlib.sha256(canonical(parsed)).hexdigest()
    payload_path = folder / 'payload.json'
    metadata_path = folder / 'request.json'
    with JournalLock(folder / 'request.lock'):
        if payload_path.exists():
            prior = strict_json(payload_path.read_text(encoding='utf-8-sig'))
            if hashlib.sha256(canonical(prior)).hexdigest() != digest:
                raise CLIError('request_conflict: UUID already belongs to a different payload')
        else:
            atomic(payload_path, raw)
        if metadata_path.exists():
            prior = strict_json(metadata_path.read_text(encoding='utf-8'))
            if prior.get('kind') != kind or prior.get('payload_sha256') != digest:
                raise CLIError('request_conflict: UUID already belongs to a different operation')
        else:
            atomic_json(config.runtime_root, metadata_path,
                        {'request_id': request_id, 'kind': kind, 'payload_sha256': digest, 'created_at': time.time()})
    return folder


def submit(config, raw, resume=False):
    require_runtime(config)
    body = strict_json(raw.decode('utf-8-sig'))
    request_id, *_ = Reviews.prepare(body)
    folder = _record_payload(config, request_id, raw)
    session = ConsoleSession(config)
    try:
        # Sending the canonical JSON avoids a UTF-8 BOM while retaining exact source bytes on disk.
        result = session.post('/api/review', canonical(body))
        if (resume and result.get('state') not in ('completed', 'failed', 'manual_review') and
                result.get('retryable') is not False):
            result = session.post('/api/review/retry', canonical({'client_request_id': request_id}))
        atomic_json(config.runtime_root, folder / 'receipt.json', result)
        return result
    except Exception:
        atomic_json(config.runtime_root, folder / 'attempt.json',
                    {'request_id': request_id, 'state': 'uncertain', 'attempted_at': time.time()})
        raise


def retry(config, request_id):
    require_runtime(config)
    folder = safe_path(config.runtime_root / 'cli-submissions' / valid_uuid(request_id))
    if not folder.is_dir():
        raise CLIError('No saved CLI submission in this instance; check --instance. App send retries use the original app send file')
    metadata = strict_json((folder / 'request.json').read_text(encoding='utf-8'))
    if metadata.get('kind') != 'review':
        raise CLIError('retry accepts an existing review UUID only')
    return submit(config, (folder / 'payload.json').read_bytes(), resume=True)


def doctor(config):
    runtime_ready, runtime_error = False, None
    try:
        require_runtime(config)
        runtime_ready = True
    except (ValueError, OSError):
        runtime_error = 'runtime_not_initialized_or_identity_or_permissions_invalid'
    providers = {actor: _provider_doctor(config, actor, runtime_ready) for actor in ('claude', 'codex')}
    return {'ok': runtime_ready, 'config_valid': True, 'runtime_ready': runtime_ready,
            'runtime_error': runtime_error, **config.public(), 'provider_status': providers['claude'],
            'provider_statuses': providers, 'model_acceptance': 'not_run'}


def _provider_doctor(config, actor, runtime_ready):
    provider = {'ok': False, 'available': False, 'auth_ready': False,
                'execution_ready': False, 'state': 'unavailable', 'reason': 'provider_not_configured'}
    if config.provider_for(actor)['kind'] != 'unavailable':
        if not runtime_ready:
            provider['reason'] = 'runtime_not_ready'
        else:
            try:
                provider = config.adapter(actor).preflight()
                provider['execution_ready'] = provider.get('ok') is True
                provider['state'] = 'ready' if provider['execution_ready'] else 'unavailable'
            except (OSError, ValueError, RuntimeError):
                provider['reason'] = 'provider_preflight_failed'
    return provider


def call(config, actor, op, args, request_id):
    require_runtime(config)
    request_id = valid_uuid(request_id)
    body = {'actor': actor, 'op': op, 'args': args, 'request_id': request_id}
    folder = _record_payload(config, request_id, canonical(body), kind='rpc')
    ConsoleSession(config).bootstrap()
    from .bridge_client import request
    result = request(config.broker_root, actor, op, args, transport=config.transport,
                     url=config.broker_url, timeout=config.worker['request_timeout_seconds'], request_id=request_id)
    atomic_json(config.runtime_root, folder / 'receipt.json', result)
    return result


def activity(config, request_id, title, phase, detail, ttl):
    require_runtime(config)
    body = {'title': title, 'phase': phase, 'detail': detail, 'ttl_s': ttl}
    if not 15 <= ttl <= 900 or any(len(body[k].encode('utf-8')) > 2000 for k in ('title', 'phase', 'detail')):
        raise CLIError('Activity TTL must be 15..900 and each text field at most 2000 UTF-8 bytes')
    folder = _record_payload(config, request_id, canonical(body), kind='activity')
    receipt = safe_path(folder / 'receipt.json')
    if receipt.exists():
        return strict_json(receipt.read_text(encoding='utf-8'))
    session = ConsoleSession(config).bootstrap()
    token_path = safe_path(config.console_root / 'activity.token')
    if token_path.stat().st_size > 512:
        raise CLIError('Activity token is invalid')
    token = token_path.read_text(encoding='ascii').strip()
    if not token or any(char.isspace() for char in token):
        raise CLIError('Activity token is invalid')
    result = session._request('/api/codex/activity', canonical(body),
                  {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + token})
    atomic_json(config.runtime_root, receipt, result)
    return result


def parser():
    result = JSONArgumentParser(description=__doc__)
    result.add_argument('--version', action='version', version='Relay Collaboration ' + __version__)
    default_config = str(package_root() / 'relay.local.json') if frozen() else 'relay.local.json'
    result.add_argument('--config', default=default_config, help='UTF-8 schema v1 configuration')
    result.add_argument('--instance', help='Select a named independent instance beside the base --config')
    commands = result.add_subparsers(dest='command', required=True)
    instances = commands.add_parser('instances', help='Create or list independent Relay profiles; no runtime/auth copying')
    instance_commands = instances.add_subparsers(dest='instance_command', required=True)
    instance_commands.add_parser('list')
    instance_commands.add_parser('inspect', help='Show base and named instances with binding metadata')
    instance_commands.add_parser('register', help='Register this base for discovery from named consoles; no runtime changes')
    create = instance_commands.add_parser('create')
    create.add_argument('--name', required=True)
    create.add_argument('--broker-port', type=int)
    create.add_argument('--console-port', type=int)
    for name in ('init', 'doctor', 'start', 'status'):
        commands.add_parser(name)
    models = commands.add_parser('model', help='Inspect or select Claude/Codex models for this instance')
    model_commands = models.add_subparsers(dest='model_command', required=True)
    show_model = model_commands.add_parser('show', help='Show configuration and presets; no model call')
    show_model.add_argument('--actor', choices=('claude', 'codex'))
    set_model = model_commands.add_parser('set', help='Update a stopped CLI provider; App models remain App-owned')
    set_model.add_argument('--actor', choices=('claude', 'codex'), required=True)
    set_model.add_argument('--model', help='Exact model ID or alias; default clears the override')
    set_model.add_argument('--effort', help='Explicit effort; default clears the override; omit to preserve')
    launch = commands.add_parser('launch', help='Initialize a fresh profile, start hidden and open the console')
    launch.add_argument('--no-browser', action='store_true')
    install = commands.add_parser('install', help='Initialize a profile and install common agent skills')
    from .skill_install import defaults
    install.add_argument('--agents', choices=('both', 'codex', 'claude'), default='both')
    install.add_argument('--codex-skills-dir', type=Path, default=defaults()['codex'])
    install.add_argument('--claude-skills-dir', type=Path, default=defaults()['claude'])
    install.add_argument('--rebind', action='store_true')
    stop = commands.add_parser('stop')
    stop.add_argument('--wait-seconds', type=float, default=20)
    serve = commands.add_parser('serve', help='Internal supervisor; normally launched by start')
    serve.add_argument('--instance-id', required=True, type=valid_uuid)
    serve.add_argument('--expected-config-digest', help='Internal launch configuration binding')
    send = commands.add_parser('submit')
    send.add_argument('--payload', required=True, help='JSON text, a UTF-8 JSON file path, or @file')
    send.add_argument('--require-recipient', choices=('codex', 'claude'), help='Require this explicit recipient in the saved payload')
    receipt = commands.add_parser('receipt', help='Read a saved request and its result without retrying or dispatching')
    receipt.add_argument('--request-id', required=True, type=valid_uuid)
    again = commands.add_parser('retry')
    again.add_argument('--request-id', required=True, type=valid_uuid)
    rpc = commands.add_parser('call')
    rpc.add_argument('--actor', choices=('codex', 'claude'), default='codex')
    rpc.add_argument('--op', required=True)
    rpc.add_argument('--args', default='{}', help='JSON object')
    rpc.add_argument('--request-id', required=True, type=valid_uuid)
    report = commands.add_parser('activity')
    report.add_argument('--request-id', required=True, type=valid_uuid)
    report.add_argument('--title', required=True)
    report.add_argument('--phase', default='working')
    report.add_argument('--detail', default='')
    report.add_argument('--ttl', type=int, default=300)
    app = commands.add_parser('app', help='Connect the existing Codex App task; no additional Codex login')
    _app_parser(app, web=False)
    web = commands.add_parser('web', help='Disabled experimental web mailbox; not a supported Claude connection')
    _app_parser(web, web=True)
    return result


def _app_parser(app, web=False):
    app.add_argument('--thread-id', help='Defaults to the current App CODEX_THREAD_ID')
    app_commands = app.add_subparsers(dest='app_command', required=True)
    for name in ('attach', 'detach', 'status'):
        app_commands.add_parser(name)
    inbox = app_commands.add_parser('inbox')
    inbox.add_argument('--cursor', help='Continue scanning Claude reply notifications')
    reply = app_commands.add_parser('reply')
    reply.add_argument('--delivery-id', required=True, type=valid_uuid)
    reply.add_argument('--file', required=True, help='UTF-8 plain text reply, at most 48 KiB')
    reply.add_argument('--failed', action='store_true')
    if web:
        reply.add_argument('--source-url', help='Observed Claude conversation URL, required for successful replies')
        reply.add_argument('--model-label', help='Visible web model label; not independent model identity verification')
        checkpoint = app_commands.add_parser('checkpoint', help='Persist immediately before sending once in the browser')
        for name in ('delivery-id','browser-id','tab-id','url'):
            checkpoint.add_argument('--'+name, required=True)
    else:
        release2 = app_commands.add_parser('release', help='Preview and save a binding release, then execute the same file with --confirm')
        release2.add_argument('--file', required=True, help='New preview file, or original saved file when confirming/retrying')
        release2.add_argument('--reason', help='Required when preparing; recorded in the release audit')
        release2.add_argument('--successor-thread-id', type=valid_uuid, help='Reserve for this task; omit to leave open for a new owner')
        release2.add_argument('--allow-active', action='store_true', help='Explicitly permit operator revocation even with a recent heartbeat')
        release2.add_argument('--confirm', action='store_true', help='Execute the previously saved file without changing its UUID or snapshot')
        release = app_commands.add_parser('release-stale', help='Explicitly release a stale generation for this task; see docs/CODEX_APP.md')
        release.add_argument('--file', required=True, help='Saved UTF-8 JSON release request; reuse unchanged for retries')
        send = app_commands.add_parser('send', help='Submit a saved UTF-8 JSON request to Claude through the local console')
        send.add_argument('--file', required=True)
    ack = app_commands.add_parser('ack')
    ack.add_argument('--task-id', required=True, type=valid_uuid)


def main(argv=None):
    configure_process()
    if argv is None and frozen() and len(sys.argv) == 1:
        argv = ['launch']
    config = None
    args = None
    try:
        args = parser().parse_args(argv)
        if args.instance is not None:
            if args.command == 'instances':
                raise CLIError('Use the base --config for instances create/list; do not nest instance catalogs')
            from .instances import select
            args.config = str(select(args.config, args.instance))
        if args.command in ('launch', 'install'):
            from .desktop import ensure_profile
            ensure_profile(args.config)
        config = load_config(args.config)
        if (args.command == 'serve' and args.expected_config_digest is not None and
                args.expected_config_digest != config.digest):
            raise CLIError('Launch configuration changed; start again with the current configuration')
        if args.command == 'instances':
            from .instances import create, list_instances, overview, register
            if args.instance_command == 'inspect':
                result = overview(config)
            elif args.instance_command == 'register':
                result = register(config.path)
            elif args.instance_command == 'list':
                result = list_instances(config.path)
            else:
                result = create(config.path, args.name, args.broker_port, args.console_port)
        elif args.command == 'launch':
            from .desktop import launch
            result = launch(config, open_browser=not args.no_browser)
        elif args.command == 'install':
            from .desktop import install
            result = install(config, args)
        elif args.command == 'init':
            result = initialize(config)
        elif args.command == 'doctor':
            result = doctor(config)
        elif args.command == 'model':
            from .model_selection import describe, configure
            result = (describe(config, args.actor) if args.model_command == 'show' else
                      configure(config, args.actor, model=args.model, effort=args.effort))
        elif args.command in ('start', 'status', 'stop', 'serve'):
            from . import service
            if args.command == 'serve':
                return service.serve(config, args.instance_id)
            if args.command == 'stop':
                if not 0 <= args.wait_seconds <= 60:
                    raise CLIError('wait-seconds must be 0..60')
                result = service.stop(config, args.wait_seconds)
            else:
                result = getattr(service, args.command)(config)
        elif args.command == 'submit':
            raw = payload_bytes(args.payload)
            if args.require_recipient:
                payload = strict_json(raw.decode('utf-8-sig'))
                if not isinstance(payload, dict) or payload.get('recipient') != args.require_recipient:
                    raise CLIError('Saved payload must explicitly contain the required recipient; inspect it before submitting')
            result = submit(config, raw)
        elif args.command == 'retry':
            result = retry(config, args.request_id)
        elif args.command == 'receipt':
            result = ConsoleSession(config).bootstrap()._request('/api/review/' + args.request_id)
        elif args.command == 'call':
            rpc_args = strict_json(args.args)
            if not isinstance(rpc_args, dict):
                raise CLIError('call --args must be a JSON object')
            result = call(config, args.actor, args.op, rpc_args, args.request_id)
        elif args.command in ('app', 'web'):
            from .app_mailbox import AppMailbox, current_thread, AppError, MAX_TEXT_BYTES
            actor = 'codex' if args.command == 'app' else 'claude'
            if args.app_command == 'status':
                result = {'ok': True, **AppMailbox(config,actor).status()}
                caller = os.environ.get('CODEX_THREAD_ID')
                result['binding_relation'] = ('unknown' if not caller else
                    'owner' if result['thread_id'] == caller else
                    'reserved_for_current_task' if result['successor_thread_id'] == caller else
                    'reserved_for_another_task' if result['successor_thread_id'] else
                    'another_task' if result['thread_id'] else 'available')
            elif args.app_command == 'release':
                result = app_release(config, args)
            else:
                thread_id = current_thread(args.thread_id)
                body = {'command':args.app_command, 'actor':actor, 'thread_id':thread_id}
                session = ConsoleSession(config).bootstrap()
                if args.app_command in ('send', 'release-stale'):
                    # Original payload is a user-created file; the console persists it before dispatch.
                    path = safe_path(args.file)
                    if path.stat().st_size > 2 * 1024 * 1024:
                        raise AppError('Saved request exceeds 2 MiB')
                    payload = strict_json(path.read_text(encoding='utf-8-sig'))
                    if args.app_command == 'send':
                        Reviews.prepare(payload)
                        if payload.get('recipient','claude') != 'claude':
                            raise AppError('app send addresses Claude only')
                    else:
                        AppMailbox.validate_release(payload)
                    body['payload'] = payload
                elif args.app_command == 'inbox':
                    if args.cursor: body['cursor'] = args.cursor
                elif args.app_command == 'ack':
                    body['task_id'] = args.task_id
                elif args.app_command == 'checkpoint':
                    body.update(delivery_id=args.delivery_id,browser_id=args.browser_id,tab_id=args.tab_id,url=args.url)
                elif args.app_command == 'reply':
                    path = safe_path(args.file)
                    if path.stat().st_size > MAX_TEXT_BYTES + 3:
                        raise AppError('Reply exceeds 48 KiB')
                    body.update(delivery_id=args.delivery_id,text=path.read_text(encoding='utf-8-sig'),failed=args.failed)
                    if actor == 'claude':
                        body.update(source_url=args.source_url,model_label=args.model_label)
                result = session.post('/api/app',canonical(body))
        else:
            result = activity(config, args.request_id, args.title, args.phase, args.detail, args.ttl)
        result = {**result, 'selection': _selection(config, args)}
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 2 if result.get('ok') is False or result.get('state') in ('failed', 'uncertain', 'manual_review') else 0
    except (ConfigError, CLIError, ConsoleError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc), 'selection': _selection(config, args)}, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError) as exc:
        from .app_mailbox import AppError
        if isinstance(exc, AppError):
            print(json.dumps({'ok': False, 'error': str(exc), 'selection': _selection(config, args)}, ensure_ascii=False), file=sys.stderr)
            return 2
        print(json.dumps({'ok': False, 'error': 'Operation failed: ' + type(exc).__name__ +
                          '; configuration, private runtime identity or service readiness could not be verified',
                          'selection': _selection(config, args)}), file=sys.stderr)
        return 2


def app_release(config, args):
    from .app_mailbox import AppMailbox, current_thread
    thread_id = current_thread(args.thread_id)
    if args.confirm:
        if args.reason is not None or args.successor_thread_id is not None or args.allow_active:
            raise CLIError('Confirm/retry only the original --file; do not change release options')
    elif not args.reason:
        raise CLIError('Preview requires --reason and a new --file; then review it and repeat with --confirm')
    path = safe_path(args.file)
    if not args.confirm and path.exists():
        raise CLIError('Release preview file already exists; review and confirm that file, or choose a new file. It was not overwritten')
    session = ConsoleSession(config).bootstrap()
    session.require_capability('app-release-v2')
    if args.confirm:
        if path.stat().st_size > 16384:
            raise CLIError('Release file exceeds 16 KiB')
        payload = strict_json(path.read_text(encoding='utf-8-sig'))
        AppMailbox.validate_release(payload)
        if payload.get('schema') != 'relay-app-release-request/v2':
            raise CLIError('Use app release-stale for legacy release files')
        result = session.post('/api/app', canonical({'command': 'release', 'actor': 'codex',
                              'thread_id': thread_id, 'payload': payload}))
        return {**result, 'release_file': str(path)}
    result = session.post('/api/app', canonical({'command': 'release-preview', 'actor': 'codex',
                          'thread_id': thread_id, 'reason': args.reason,
                          'successor_thread_id': args.successor_thread_id, 'allow_active': args.allow_active}))
    payload = result.get('payload')
    AppMailbox.validate_release(payload)
    with path.open('xb') as stream:
        stream.write(json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8') + b'\n')
        stream.flush(); os.fsync(stream.fileno())
    return {**result, 'release_file': str(path), 'requires_confirmation': True,
            'next_step': 'Review current_binding and payload; repeat app release --file with --confirm. No binding has been released'}


def _selection(config, args):
    if config is None:
        return None
    value = {'instance': args.instance if args is not None else None,
             'config': str(config.path), 'runtime_root': str(config.runtime_root),
             'console_url': config.console_url}
    # Metadata only: no provider auth, App internals, or private task reads.
    try:
        value['runtime_id'] = require_runtime(config)['runtime_id']
    except (OSError, ValueError):
        value['runtime_id'] = None
    return value


if __name__ == '__main__':
    raise SystemExit(main())
