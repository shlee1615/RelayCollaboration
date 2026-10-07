import json
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch

from relay_collaboration.app_mailbox import AppMailbox, AppError
from relay_collaboration.cli import ConsoleSession, CLIError
from relay_collaboration.config import load_config, initialize, ConfigError
from relay_collaboration.console_backend import Backend, ConsoleError, canonical
from relay_collaboration.instances import create, overview, register
from tests import test_app_mailbox
from tests.test_console import FakeObserver, FakeWorker


class BindingConsoleTests(unittest.TestCase):
    setUp = test_app_mailbox.AppMailboxTests.setUp

    def backend(self):
        return Backend(self.config.console_root, self.config.output_root, FakeObserver(), FakeWorker(),
                       execute=False, config=self.config)

    def test_english_connection_instructions_keep_the_exact_selected_config(self):
        created=create(self.config.path,'english-guide')
        selected=load_config(created['config']);initialize(selected)
        backend=Backend(selected.console_root,selected.output_root,FakeObserver(),FakeWorker(),execute=False,config=selected)
        value=backend.summary()
        for field in ('app_connect_prompt','app_connect_prompt_en'):
            self.assertIn(str(selected.path),value[field])
            self.assertIn('SKILL.md',value[field])
        self.assertIn('AGENT_GUIDE.md',value['app_connect_prompt_en'])
        self.assertIn('current Codex App task',value['app_connect_prompt_en'])

    def test_console_has_its_own_audited_identity_and_receipt_survives_restart(self):
        self.box.attach(self.thread)
        b=self.backend()
        before=(self.box.root/'binding.json').read_bytes()
        preview=b.binding_command({'command':'preview','allow_active':True})
        self.assertEqual(before,(self.box.root/'binding.json').read_bytes())
        self.assertEqual(preview['operator_kind'],'local_console_operator')
        self.assertIsNone(preview['requesting_thread_id'])
        self.assertNotEqual(preview['operator_id'],self.thread)
        body={'command':'release','payload':preview['payload']}
        self.assertFalse(b.binding_command(body)['deduped'])
        self.box.attach(str(uuid.uuid4()))
        after=(self.box.root/'binding.json').read_bytes()
        self.assertTrue(self.backend().binding_command(body)['deduped'])
        self.assertEqual(after,(self.box.root/'binding.json').read_bytes())
        record=self.box._read('releases/'+preview['payload']['request_id']+'.json')
        self.assertEqual(record['operator_kind'],'local_console_operator')
        with self.assertRaisesRegex(AppError,'release_conflict'):
            self.box.release(preview['operator_id'],preview['payload'])

    def test_browser_rejects_target_identity_and_extra_scope_inputs(self):
        self.box.attach(self.thread)
        b=self.backend()
        for body in ({'command':'preview','allow_active':1},
                     {'command':'preview','allow_active':True,'thread_id':self.thread},
                     {'command':'preview','allow_active':True,'config':'another.json'}):
            with self.assertRaises(ConsoleError):b.binding_command(body)
        preview=b.binding_command({'command':'preview','allow_active':False})
        self.assertFalse(preview['can_release'])
        with self.assertRaises(ConsoleError):
            b.binding_command({'command':'release','payload':preview['payload']})
        for changes in ({'successor_thread_id':str(uuid.uuid4())},{'runtime_id':str(uuid.uuid4())}):
            with self.assertRaises(ConsoleError):
                b.binding_command({'command':'release','payload':preview['payload']|changes})
        self.assertEqual(self.box.status()['thread_id'],self.thread)

    def test_overview_discovers_base_from_named_console_and_does_not_read_auth(self):
        self.box.attach(self.thread)
        original=(self.box.root/'binding.json').read_bytes()
        created=create(self.config.path,'second')
        second=load_config(created['config']);initialize(second)
        owner=str(uuid.uuid4());AppMailbox(second).attach(owner)
        read=Path.read_text
        def guarded(path,*args,**kwargs):
            self.assertNotIn('provider-auth',path.parts)
            return read(path,*args,**kwargs)
        with patch.object(Path,'read_text',guarded):
            value=overview(second)
        items={x['name']:x for x in value['instances']}
        self.assertEqual(set(items),{'base','second'})
        self.assertEqual(items['base']['app']['thread_id'],self.thread)
        self.assertEqual(items['second']['app']['thread_id'],owner)
        self.assertTrue(items['second']['current'])
        self.assertFalse(items['base']['current'])
        self.assertEqual(original,(self.box.root/'binding.json').read_bytes())
        self.assertEqual(register(self.config.path)['runtime_changed'],False)
        raw=(self.root/'instances/.base-config.json').read_text(encoding='utf-8')
        self.assertNotIn(str(self.root),raw) # Relocatable basename, no absolute host path.

    def test_bad_registration_is_isolated_and_cannot_point_outside_catalog(self):
        second=load_config(create(self.config.path,'second')['config'])
        path=self.root/'instances/.base-config.json'
        for value in (None,[],{'schema':'relay-instance-catalog/v1','base_config':'../other.json'}):
            path.write_text(json.dumps(value),encoding='utf-8')
            result=overview(second)
            self.assertEqual(result['catalog_error'],'base_registration_invalid')
            self.assertTrue(any(x.get('current') for x in result['instances']))
            with self.assertRaises(ConfigError):register(self.config.path)

    def test_live_http_session_csrf_and_runtime_checks_before_release(self):
        from relay_collaboration import service
        self.box.attach(self.thread)
        self.addCleanup(service.stop,self.config,15)
        self.assertTrue(service.start(self.config)['running'])
        session=ConsoleSession(self.config).bootstrap()
        session.require_capability('binding-console-v1')
        self.assertTrue(session._request('/api/instances')['instances'][0]['current'])
        raw=canonical({'command':'preview','allow_active':True})
        fresh=ConsoleSession(self.config)
        with self.assertRaises(CLIError):fresh._request('/api/instances')
        for headers in ({'Content-Type':'application/json'},
                        {'Content-Type':'application/json','Origin':session.origin,'X-CSRF-Token':'wrong'}):
            with self.assertRaises(CLIError):session._request('/api/binding',raw,headers)
        preview=session.post('/api/binding',raw)
        released=session.post('/api/binding',canonical({'command':'release','payload':preview['payload']}))
        self.assertFalse(released['deduped'])
        self.box.attach(str(uuid.uuid4()))
        self.assertTrue(session.post('/api/binding',canonical({'command':'release','payload':preview['payload']}))['deduped'])


if __name__=='__main__':unittest.main()
