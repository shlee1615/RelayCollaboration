import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid
from relay_collaboration.console_backend import Backend, Reviews, ConsoleError, sha, canonical, atomic
from relay_collaboration.console_server import handler_for
from http.server import ThreadingHTTPServer

class FakeWorker:
    def __init__(self): self.actions=[]
    def snapshot(self): return {'state':'idle','verified':True,'checked_at':1,'model':'fable','effort':None}
    def action(self,action):
        if action not in {'start','stop'}: raise ConsoleError('bad_action','bad action')
        self.actions.append(action);return {'state':'accepted','action':action}
class FakeObserver:
    def __init__(self):self.calls=0;self.fail=False
    def get(self,kind,**args):
        self.calls+=1
        if self.fail: raise ConsoleError('offline','offline',503)
        if kind=='summary':return {'server_time':1,'leader':{'actor':None,'active':False},'counts':{'pending':1,'running':0,'completed':0,'failed':0,'attention':0,'total':1}}
        return {'items':[],'server_time':1,'next_cursor':None,'total':0}

class ConsoleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'work',prefix='console-fixture-')
        self.root=Path(self.temp.name)
        self.observer=FakeObserver();self.worker=FakeWorker()
        self.backend=Backend(self.root/'runtime',self.root/'output',self.observer,self.worker,execute=False)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),handler_for(self.backend,'cookie-secret','csrf-secret','activity-secret'))
        self.server.daemon_threads=True
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.origin='http://127.0.0.1:'+str(self.server.server_port)
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.temp.cleanup()
    def body(self):return {'client_request_id':str(uuid.uuid4()),'title':'GUI驗收<script>','prompt':'請審查以下程式','contexts':[{'name':'sample.py','text':'def add(a,b): return a-b'}]}
    def http(self,path,method='GET',body=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        defaults={'Cookie':'relay_session=cookie-secret','Origin':self.origin,'X-CSRF-Token':'csrf-secret','Content-Type':'application/json'}
        defaults.update(headers or {})
        data=None if body is None else json.dumps(body).encode()
        connection.request(method,path,body=data,headers=defaults)
        response=connection.getresponse();code=response.status;response_headers=dict(response.getheaders());data=response.read();connection.close()
        return code,response_headers,json.loads(data)
    def test_get_session_cookie_and_headers(self):
        code,headers,value=self.http('/api/session',headers={'Cookie':''})
        self.assertEqual(code,200);self.assertEqual(value['csrf'],'csrf-secret')
        self.assertIn('HttpOnly',headers['Set-Cookie']);self.assertIn('SameSite=Strict',headers['Set-Cookie'])
        self.assertNotIn('Access-Control-Allow-Origin',headers);self.assertIn("frame-ancestors 'none'",headers['Content-Security-Policy'])

    def test_translation_module_is_served_without_exposing_other_files(self):
        connection=http.client.HTTPConnection('127.0.0.1',self.server.server_port)
        connection.request('GET','/i18n.js')
        response=connection.getresponse();raw=response.read();connection.close()
        self.assertEqual(response.status,200)
        self.assertIn('javascript',response.getheader('Content-Type'))
        self.assertIn(b'export function initializeLanguage',raw)
        self.assertEqual(self.http('/docs/agent-log.md')[0],404)
    def test_read_requires_session_and_no_runtime_files(self):
        self.assertEqual(self.http('/api/summary',headers={'Cookie':''})[0],401)
        self.assertEqual(self.http('/api/review/'+str(uuid.uuid4()),headers={'Cookie':''})[0],401)
        self.assertEqual(self.http('/runtime/activity.token')[0],404)
        self.assertEqual(self.http('/../console_backend.py')[0],404)

    def test_receipt_observation_never_dispatches_or_retries(self):
        body = self.body()
        self.backend.reviews.submit(body)
        with patch.object(self.backend.reviews, 'submit', side_effect=AssertionError('no submit')), \
                patch.object(self.backend.reviews, 'retry', side_effect=AssertionError('no retry')):
            code, _, receipt = self.http('/api/review/'+body['client_request_id'])
            self.assertEqual(code, 200)
            self.assertTrue(receipt['read_only'])
            self.assertEqual(receipt['request_id'], body['client_request_id'])
            self.assertEqual(self.http('/api/review/'+str(uuid.uuid4()))[0], 404)
    def test_cross_origin_host_and_missing_csrf(self):
        for headers in [{'Host':'example.com'},{'Origin':'http://evil.invalid'},{'X-CSRF-Token':''},{'Origin':''},{'Sec-Fetch-Site':'cross-site'}]:
            self.assertEqual(self.http('/api/review','POST',self.body(),headers)[0],403)
    def test_body_limit_invalid_json_and_fields(self):
        body=self.body();body['extra']='shell'
        self.assertEqual(self.http('/api/review','POST',body)[0],400)
        self.assertEqual(self.http('/api/review','POST',self.body(),{'Content-Type':'text/plain'})[0],415)
    def test_browser_cannot_publish_codex_activity(self):
        body={'title':'work','phase':'test','detail':'fixture','ttl_s':30}
        self.assertEqual(self.http('/api/codex/activity','POST',body)[0],403)
        self.assertEqual(self.http('/api/codex/activity','POST',body,{'Authorization':'Bearer activity-secret'})[0],200)
    def test_activity_expiry_not_refreshed_by_poll(self):
        with patch('relay_collaboration.console_backend.time.time',return_value=100):
            self.backend.report({'title':'review','phase':'checking','detail':'test','ttl_s':30})
        with patch('relay_collaboration.console_backend.time.time',return_value=131):
            for _ in range(3):self.assertEqual(self.backend.summary()['codex']['state'],'stale')
        self.assertEqual(self.backend.activity()['reported_at'],100)
    def test_duplicate_submit_and_changed_content_conflict(self):
        body=self.body()
        first=self.http('/api/review','POST',body)
        second=self.http('/api/review','POST',body)
        self.assertEqual((first[0],second[0]),(202,202));self.assertTrue(second[2]['deduped'])
        self.assertEqual(len(self.backend.reviews.jobs),1)
        body['prompt']='changed';self.assertEqual(self.http('/api/review','POST',body)[0],409)
    def test_restart_preserves_identity_and_prompt(self):
        body=self.body();self.backend.reviews.submit(body)
        new=Reviews(self.root/'runtime',self.root/'output',self.observer,execute=False)
        result=new.submit(body)
        self.assertTrue(result['deduped']);self.assertEqual(result['state'],'uncertain')
        self.assertEqual(len(new.jobs),1)
    def test_retry_same_id_never_creates_new_job(self):
        body=self.body();self.backend.reviews.submit(body)
        result=self.http('/api/review/retry','POST',{'client_request_id':body['client_request_id']})
        self.assertEqual(result[0],202);self.assertEqual(len(self.backend.reviews.jobs),1)
    def test_context_hash_and_no_path_attachment(self):
        body=self.body();self.backend.reviews.submit(body)
        source=self.backend.reviews.jobs[body['client_request_id']]['sources'][0]
        self.assertEqual(source['sha256'],sha(body['contexts'][0]['text'].encode()))
        for name in ['../config','C:\\secret','a\nb']:
            body['client_request_id']=str(uuid.uuid4());body['contexts'][0]['name']=name
            with self.assertRaises(ConsoleError):self.backend.reviews.submit(body)
    def test_unicode_byte_limit_duplicate_source_and_count(self):
        for mutate in [lambda b:b.update(prompt='電'*24000), lambda b:b.update(contexts=b['contexts']*2), lambda b:b.update(contexts=[{'name':str(i),'text':''} for i in range(9)])]:
            body=self.body();mutate(body)
            with self.assertRaises(ConsoleError):Reviews.prepare(body)
    def test_worker_action_allowlist(self):
        self.assertEqual(self.http('/api/worker','POST',{'action':'stop','path':'evil'})[0],400)
        self.assertEqual(self.worker.actions,[])
        self.assertEqual(self.http('/api/worker','POST',{'action':'start'})[0],202)
        self.assertEqual(self.worker.actions,['start'])
    def test_observation_cache_and_offline_unknown_counts(self):
        self.backend.summary();self.backend.summary();self.assertEqual(self.observer.calls,1)
        self.observer.fail=True;self.backend.cache.clear()
        value=self.backend.summary();self.assertFalse(value['broker']['ok']);self.assertIsNone(value['counts']['pending'])
    def test_invalid_ids_before_filesystem(self):
        for value in ['../token','UPPER','a'*40,None]:
            body=self.body();body['client_request_id']=value
            with self.assertRaises(ConsoleError):self.backend.reviews.submit(body)
    def test_filters_do_not_allow_backend_path_or_actor(self):
        self.assertEqual(self.http('/api/tasks?actor=claude')[0],404)
        self.assertEqual(self.http('/api/tasks?q=a&q=b')[0],400)
    def test_chinese_activity_limit_and_unreadable_state_degrade(self):
        body={'title':'電'*2000,'phase':'工作','detail':'說明','ttl_s':30}
        self.assertEqual(self.http('/api/codex/activity','POST',body,{'Authorization':'Bearer activity-secret'})[0],400)
        file=self.root/'runtime/codex-activity.json'
        for raw in [b'{bad',b'{}',b'x'*9000]:
            file.write_bytes(raw)
            value=self.backend.summary()
            self.assertEqual(value['codex']['state'],'unknown');self.assertTrue(value['broker']['ok'])
    def test_slow_query_does_not_block_other_keys(self):
        entered,release,other_done=threading.Event(),threading.Event(),threading.Event()
        original=self.observer.get
        def slow(kind,**args):
            if kind=='summary':entered.set();release.wait(3)
            return original(kind,**args)
        self.observer.get=slow
        first=threading.Thread(target=lambda:self.backend.observe('summary'));first.start()
        self.assertTrue(entered.wait(1))
        second=threading.Thread(target=lambda:(self.backend.observe('tasks'),other_done.set()));second.start()
        try:self.assertTrue(other_done.wait(1),'unrelated observation blocked behind summary')
        finally:release.set();first.join();second.join()
    def test_negative_cache_avoids_repeated_failed_requests(self):
        self.observer.fail=True
        self.backend.summary();self.backend.summary()
        self.assertEqual(self.observer.calls,1)
    def test_atomic_replace_retries_and_cleans_failed_temp(self):
        target=self.root/'runtime/atomic-test.json';target.write_bytes(b'old')
        import os
        real=os.replace
        calls=[]
        def once(source,destination):
            calls.append(1)
            if len(calls)==1:raise PermissionError('reader holding file')
            real(source,destination)
        with patch('relay_collaboration.console_backend.os.replace',side_effect=once),patch('relay_collaboration.console_backend.time.sleep'):
            atomic(target,b'new')
        self.assertEqual(target.read_bytes(),b'new')
        with patch('relay_collaboration.console_backend.os.replace',side_effect=PermissionError()),patch('relay_collaboration.console_backend.time.sleep'):
            with self.assertRaises(PermissionError):atomic(target,b'failed')
        self.assertEqual(target.read_bytes(),b'new');self.assertFalse(list(target.parent.glob('.atomic-test.json.*')))
    # Process identity and WorkerMonitor behavior are covered in test_lifecycle.py.

if __name__=='__main__':unittest.main()
