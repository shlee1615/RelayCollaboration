"""App mailbox protocol, identity, retries and real broker/worker integration.

These callbacks simulate App replies; no fixture is evidence of a live model call.
"""
from dataclasses import replace
from pathlib import Path
import json
import os
import shutil
import threading
import time
import unittest
import uuid
from unittest.mock import patch

from relay_collaboration.app_mailbox import AppMailbox, AppExecutor, AppError, current_thread
from relay_collaboration.config import initialize, load_config, ConfigError
from relay_collaboration.worker_core import ExecutionContext, Worker
from relay_collaboration.bridge_server import BridgeService
from relay_collaboration.console_backend import Observer
from relay_collaboration.context_dispatch import Controller
from relay_collaboration import service
from tests.test_lifecycle import new_config


def remove_fixture(path):
    # Windows may briefly retain the child's log handle after verified process exit.
    # Retry only sharing violations, only within this test's explicit workspace.
    path = path.resolve()
    path.relative_to((Path(__file__).resolve().parents[1] / 'work').resolve())
    deadline = time.monotonic() + 5
    while True:
        try:
            shutil.rmtree(path)
            return
        except PermissionError as exc:
            if getattr(exc, 'winerror', None) != 32 or time.monotonic() >= deadline:
                raise
            time.sleep(.05)


class AppMailboxTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parents[1] / 'work' / ('app-test-' + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(remove_fixture, self.root)
        config = new_config(self.root)
        raw = json.loads(config.path.read_text(encoding='utf-8'))
        raw['codex_provider'] = {'kind': 'codex_app'}
        config.path.write_text(json.dumps(raw), encoding='utf-8')
        self.config = load_config(config.path)
        initialize(self.config)
        self.box = AppMailbox(self.config)
        self.thread = str(uuid.uuid4())

    def context(self, timeout=5):
        return ExecutionContext(str(uuid.uuid4()), str(uuid.uuid4()), timeout, time.time() + timeout,
                                threading.Event(), (), self.config.project_root, self.root)

    def execute(self, task=None, timeout=5, actor='codex'):
        context = self.context(timeout)
        task = task or {'task_id': context.task_id, 'prompt': '中文提案 <script>資料</script>'}
        holder = {}
        def run():
            try:
                holder['result'] = AppExecutor(self.config,actor)(task, context)
            except Exception as exc:
                holder['error'] = exc
        thread = threading.Thread(target=run)
        thread.start()
        def cleanup():
            context.stop_event.set(); thread.join(6)
        self.addCleanup(cleanup)
        return context, thread, holder

    def wait_request(self, box=None):
        end = time.monotonic() + 4
        while time.monotonic() < end:
            inbox = (box or self.box).inbox(self.thread, include_replies=False)
            if inbox['requests']:
                return inbox['requests'][0]
            time.sleep(.03)
        self.fail('No App delivery')

    def test_existing_app_binding_no_model_process_or_auth_read(self):
        self.assertFalse(self.config.public()['codex_daemon'])
        self.assertTrue(self.config.public()['codex_app'])
        adapter = self.config.adapter('codex')
        self.assertFalse(adapter.preflight()['ok'])
        self.assertFalse(adapter.preflight()['login_required'])
        with patch('subprocess.Popen', side_effect=AssertionError('must not launch model CLI')):
            # The no-process assertion covers preflight; private atomic writes may use OS ACL helpers.
            self.assertEqual(self.box.status()['state'], 'unbound')
        self.box.attach(self.thread)
        self.assertTrue(adapter.preflight()['ok'])
        for key, value in [('model','a-model'), ('command',['C:/codex.exe']), ('effort','high')]:
            raw = json.loads(self.config.path.read_text(encoding='utf-8'))
            raw['codex_provider'][key] = value
            self.config.path.write_text(json.dumps(raw), encoding='utf-8')
            with self.assertRaises(ConfigError):
                load_config(self.config.path)
            raw['codex_provider'] = {'kind':'codex_app'}
            self.config.path.write_text(json.dumps(raw), encoding='utf-8')

    def test_binding_cannot_be_stolen_or_inherited_from_another_task(self):
        self.box.attach(self.thread)
        other = str(uuid.uuid4())
        for action in (self.box.attach, self.box.detach, lambda t:self.box.inbox(t, include_replies=False)):
            with self.assertRaises(AppError): action(other)
        with patch.dict(os.environ, {'CODEX_THREAD_ID': self.thread}):
            self.assertEqual(current_thread(), self.thread)
            with self.assertRaises(AppError): current_thread(other)
        self.box.detach(self.thread)
        self.box.attach(other)
        self.assertEqual(self.box.status()['thread_id'], other)

    def test_receive_resume_reply_dedupe_conflict_and_restart(self):
        self.box.attach(self.thread)
        context, thread, holder = self.execute()
        first = self.wait_request()
        again = AppMailbox(self.config).inbox(self.thread, include_replies=False)['requests'][0]
        self.assertEqual(first['delivery_id'], again['delivery_id'])
        self.assertTrue(again['resumed'])
        reply = '這是 App 測試回覆；不是模型驗證。'
        self.assertFalse(self.box.reply(self.thread, context.attempt_id, reply)['deduped'])
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(holder['result']['text'], reply)
        self.assertEqual(holder['result']['execution_kind'], 'codex_app')
        self.assertFalse(holder['result']['model_execution_verified'])
        self.assertTrue(self.box.reply(self.thread, context.attempt_id, reply)['deduped'])
        with self.assertRaises(AppError): self.box.reply(self.thread, context.attempt_id, '不同內容')
        self.assertEqual(self.box.inbox(self.thread, include_replies=False)['requests'], [])

    def test_expired_cancelled_and_old_binding_cannot_accept_late_reply(self):
        self.box.attach(self.thread)
        context, thread, holder = self.execute()
        self.wait_request()
        context.stop_event.set(); thread.join(5)
        with self.assertRaises(AppError): self.box.reply(self.thread, context.attempt_id, '太晚')
        self.assertIn('error', holder)
        context, thread, holder = self.execute(timeout=.5)
        thread.join(5)
        with self.assertRaises(AppError): self.box.reply(self.thread, context.attempt_id, '逾時')
        context, thread, holder = self.execute()
        self.wait_request()
        with self.assertRaisesRegex(AppError, 'release_blocked'):
            self.box.detach(self.thread)
        context.stop_event.set(); thread.join(5)
        self.assertFalse(thread.is_alive())
        self.box.detach(self.thread); self.box.attach(self.thread)
        with self.assertRaises(AppError): self.box.reply(self.thread, context.attempt_id, '舊綁定')
        thread.join(5)
        self.assertIn('error', holder)

    def test_heartbeat_stale_and_changed_configuration_not_connected(self):
        self.box.attach(self.thread)
        with self.box._lock():
            value = self.box._read('binding.json'); value['last_seen_at'] -= 1000
            self.box._write('binding.json', value)
        self.assertFalse(AppExecutor(self.config).preflight()['ok'])
        self.assertEqual(self.box.status()['state'], 'stale')
        self.box.inbox(self.thread, include_replies=False)
        self.assertTrue(self.box.status()['connected'])
        changed = AppMailbox(replace(self.config, digest='changed'))
        self.assertEqual(changed.status()['state'], 'configuration_changed')
        with self.assertRaises(AppError): changed.inbox(self.thread, include_replies=False)
        changed.detach(self.thread)
        self.assertEqual(changed.status()['state'], 'unbound')

    def test_private_record_tamper_and_path_escape_rejected(self):
        self.box.attach(self.thread)
        with self.assertRaises(AppError): self.box.reply(self.thread, '../binding', 'bad')
        path = self.box.root / 'binding.json'
        value = json.loads(path.read_text(encoding='utf-8'))
        value['runtime_id'] = str(uuid.uuid4())
        path.write_text(json.dumps(value), encoding='utf-8')
        with self.assertRaises(AppError): self.box.status()

    def test_modified_delivery_prompt_and_invalid_presence_fail_closed(self):
        self.box.attach(self.thread)
        context, thread, holder = self.execute()
        self.wait_request()
        with self.box._lock():
            name = 'deliveries/' + context.attempt_id + '.json'
            value = self.box._read(name)
            value['task']['prompt'] = 'altered after dispatch'
            self.box._write(name, value)
        with self.assertRaises(AppError): self.box.inbox(self.thread, include_replies=False)
        thread.join(5)
        self.assertIn('error',holder)
        with self.box._lock():
            value = self.box._read('binding.json'); value['last_seen_at'] = 'bad'
            self.box._write('binding.json',value)
        with self.assertRaises(AppError): self.box.status()

    def test_real_broker_roundtrip_both_transports_and_claude_notifications(self):
        from relay_collaboration.bridge_client import request
        from urllib.parse import urlsplit
        self.box.attach(self.thread)
        bridge = BridgeService(self.config.broker_root, poll_seconds=.02)
        bridge.start(port=urlsplit(self.config.broker_url).port)
        self.addCleanup(bridge.close)
        for transport in ('http', 'file'):
            with self.subTest(transport=transport):
                config = replace(self.config, transport=transport)
                def rpc(actor, op, args):
                    value = request(config.broker_root, actor, op, args, transport=transport,
                                    url=config.broker_url, request_id=str(uuid.uuid4()))
                    self.assertTrue(value['ok'], value)
                    return value['result']
                lease = rpc('claude','lead.acquire', {'project':config.project,'ttl_seconds':30})
                task = rpc('claude','task.submit', {'leader_token':lease['leader_token'], 'project':config.project,
                           'to':'codex','title':'App 接收 '+transport,'prompt':'只需分析提供文字','mode':'proposal'})
                rpc('claude','lead.release', {'project':config.project,'leader_token':lease['leader_token']})
                stopped = threading.Event()
                monitor = service.WorkerMonitor(config, stopped, 'codex')
                monitor.action('start')
                try:
                    delivery = self.wait_request()
                    self.box.reply(self.thread, delivery['delivery_id'], 'App fixture proposal')
                    end = time.monotonic()+5
                    while time.monotonic()<end:
                        terminal = rpc('codex','task.get', {'task_id':task['task_id']})
                        if terminal['status']=='completed': break
                        time.sleep(.05)
                    self.assertEqual(terminal['status'],'completed', terminal)
                    self.assertEqual(terminal['result']['text'],'App fixture proposal')
                    self.assertFalse(monitor.snapshot()['codex_daemon'])
                finally:
                    stopped.set(); monitor.close()
                lease = rpc('codex','lead.acquire', {'project':config.project,'ttl_seconds':30})
                task = rpc('codex','task.submit', {'leader_token':lease['leader_token'], 'project':config.project,
                           'to':'claude','title':'Claude fixture','prompt':'fixture','mode':'proposal'})
                rpc('codex','lead.release', {'project':config.project,'leader_token':lease['leader_token']})
                claim = rpc('claude','task.claim', {'task_id':task['task_id'],'ttl_seconds':30})
                rpc('claude','task.complete', {'task_id':task['task_id'],'claim_token':claim['claim_token'],
                    'status':'completed','result':{'ok':True,'text':'Claude fixture','execution_kind':'mock_test'}})
                inbox = self.box.inbox(self.thread)
                self.assertEqual([r['task_id'] for r in inbox['replies']], [task['task_id']])
                self.assertFalse(self.box.acknowledge(self.thread,task['task_id'])['deduped'])
                self.assertTrue(self.box.acknowledge(self.thread,task['task_id'])['deduped'])
                self.assertEqual(self.box.inbox(self.thread)['replies'], [])

    def test_claude_web_checkpoint_prevents_resubmission_and_preserves_source(self):
        # Draft protocol fixture only; consumer-web automation is disabled in production.
        patcher = patch('relay_collaboration.app_mailbox.CLAUDE_WEB_AUTOMATION_ENABLED',True)
        patcher.start(); self.addCleanup(patcher.stop)
        self.config = replace(self.config,provider={'kind':'claude_web'})
        box = AppMailbox(self.config,'claude')
        box.attach(self.thread)
        context, thread, holder = self.execute(actor='claude')
        self.wait_request(box)
        url = 'https://claude.ai/chat/' + str(uuid.uuid4())
        with self.assertRaises(AppError):
            box.reply(self.thread,context.attempt_id,'fixture',source_url=url)
        for target in ('https://evil.example/new','https://claude.ai.evil.example/new','http://claude.ai/new'):
            with self.assertRaises(AppError): box.checkpoint(self.thread,context.attempt_id,'2','123',target)
        self.assertFalse(box.checkpoint(self.thread,context.attempt_id,'2','123','https://claude.ai/new')['deduped'])
        self.assertTrue(box.checkpoint(self.thread,context.attempt_id,'2','123','https://claude.ai/new')['deduped'])
        with self.assertRaises(AppError): box.checkpoint(self.thread,context.attempt_id,'2','456','https://claude.ai/new')
        resumed = box.inbox(self.thread,include_replies=False)['requests'][0]
        self.assertEqual(resumed['browser_checkpoint']['tab_id'],'123')
        box.reply(self.thread,context.attempt_id,'Web fixture reply',source_url=url,model_label='Fixture visible label')
        thread.join(5)
        self.assertEqual(holder['result']['execution_kind'],'claude_web')
        self.assertEqual(holder['result']['source_url'],url)
        self.assertFalse(holder['result']['model_execution_verified'])

    def test_consumer_web_automation_disabled_in_production(self):
        config=replace(self.config,provider={'kind':'claude_web'})
        box=AppMailbox(config,'claude')
        with self.assertRaises(AppError): box.attach(self.thread)
        executor=AppExecutor(config,'claude')
        self.assertFalse(executor.preflight()['ok'])
        with self.assertRaises(AppError): executor({},self.context())

    def test_http_app_commands_use_session_csrf_and_do_not_write_in_client(self):
        from http.server import ThreadingHTTPServer
        from relay_collaboration.console_backend import Backend
        from relay_collaboration.console_server import handler_for
        from relay_collaboration.cli import ConsoleSession, CLIError
        from urllib.parse import urlsplit
        from urllib.request import Request
        from urllib.error import HTTPError
        bridge = BridgeService(self.config.broker_root)
        bridge.start(port=urlsplit(self.config.broker_url).port)
        self.addCleanup(bridge.close)
        workers = service.WorkerGroup(self.config,threading.Event())
        backend = Backend(self.config.console_root,self.config.output_root,
                          Observer(self.config.broker_root,self.config.broker_url,self.config.project),workers,
                          execute=False,config=self.config)
        server = ThreadingHTTPServer(('127.0.0.1',urlsplit(self.config.console_url).port),handler_for(backend,'test-cookie','csrf','activity'))
        thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        def cleanup(): server.shutdown(); server.server_close(); thread.join()
        self.addCleanup(cleanup)
        session = ConsoleSession(self.config).bootstrap()
        body = {'actor':'codex','thread_id':self.thread,'command':'attach'}
        result = session.post('/api/app',json.dumps(body).encode())
        self.assertTrue(result['connected'])
        body['command'] = 'inbox'
        self.assertEqual(session.post('/api/app',json.dumps(body).encode())['state'],'idle')
        body['thread_id'] = str(uuid.uuid4())
        with self.assertRaises(CLIError): session.post('/api/app',json.dumps(body).encode())
        request = Request(self.config.console_url+'/api/app',data=json.dumps(body).encode(),
                          headers={'Content-Type':'application/json','Origin':self.config.console_url})
        with self.assertRaises(HTTPError) as error: session.opener.open(request)
        self.assertEqual(error.exception.code,403)


if __name__ == '__main__':
    unittest.main()
