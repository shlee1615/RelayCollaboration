import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
import uuid
from contextlib import closing
from urllib.parse import urlencode
from relay_collaboration.bridge_server import BridgeService

class ObservationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1] / 'work', prefix='observer-fixture-')
        self.service = BridgeService(Path(self.temp.name), poll_seconds=0.02)
        self.port = self.service.start(port=0)
        self.now = 2000000000.0
        self.task = self.create('codex', 'A circuit <script>', 'schematic')
        self.other = self.create('claude', '反向_%', 'schematic')
        self.foreign = self.create('codex', 'Other project', 'other')
    def tearDown(self):
        self.service.close(); self.temp.cleanup()
    def rpc(self, actor, op, args):
        with patch('relay_collaboration.bridge_core.time.time', return_value=self.now):
            result = self.service.dispatch({'protocol':'dual-agent/v1','request_id':str(uuid.uuid4()),'actor':actor,'op':op,'args':args}, self.service.tokens[actor])
        self.assertTrue(result['ok'], result)
        return result['result']
    def create(self, sender, title, project):
        leader = self.rpc(sender,'lead.acquire',{'project':project,'ttl_seconds':60})
        value = self.rpc(sender,'task.submit',{'project':project,'leader_token':leader['leader_token'],'to':'claude' if sender=='codex' else 'codex','title':title,'prompt':'hello中文','mode':'proposal','allowed_paths':[]})
        self.rpc(sender,'lead.release',{'project':project,'leader_token':leader['leader_token']})
        return value['task_id']
    def observe(self, kind='summary', **args):
        with patch('relay_collaboration.bridge_core.time.time', return_value=self.now):
            return self.service.observe('codex',self.service.tokens['codex'],kind,{'project':'schematic',**args})
    def receipts(self):
        with closing(sqlite3.connect(self.service.broker.db_path)) as connection:
            return connection.execute('SELECT COUNT(*) FROM receipts').fetchone()[0]
    def http(self, path, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1',self.port)
        if 'project=' not in path:
            path += ('&' if '?' in path else '?') + 'project=schematic'
        connection.request('GET',path,headers={'Authorization':'Bearer '+self.service.tokens['codex'],**(headers or {})})
        response = connection.getresponse(); code=response.status; data=json.loads(response.read()); connection.close()
        return code,data
    def test_observation_never_creates_receipts(self):
        before=self.receipts()
        for _ in range(30):
            for kind,args in [('summary',{}),('tasks',{}),('task',{'task_id':self.task})]:
                self.assertTrue(self.observe(kind,**args)['ok'])
        self.assertEqual(before,self.receipts())
    def test_summary_counts_project_and_no_capabilities(self):
        result=self.observe()['result']; self.assertEqual(result['counts']['total'],2)
        raw=json.dumps(result)
        self.assertNotIn('token',raw); self.assertNotIn('prompt',raw)
        self.assertFalse(result['leader']['active'])
    def test_leader_active_then_expired(self):
        self.rpc('claude','lead.acquire',{'project':'schematic','ttl_seconds':30})
        self.assertEqual(self.observe()['result']['leader']['actor'],'claude')
        self.now+=31
        self.assertIsNone(self.observe()['result']['leader']['actor'])
    def test_expired_claim_is_attention_not_running(self):
        self.rpc('claude','task.claim',{'task_id':self.task,'ttl_seconds':30})
        self.now+=31
        counts=self.observe()['result']['counts']
        self.assertEqual((counts['running'],counts['attention']),(0,1))
        rows=self.observe('tasks',status='attention')['result']['items']
        self.assertTrue(rows[0]['claim_expired'])
    def test_summaries_exclude_prompt_result_and_tokens(self):
        rows=self.observe('tasks')['result']['items']
        for row in rows:
            self.assertNotIn('prompt',row); self.assertNotIn('result',row); self.assertNotIn('claim_token',row)
            self.assertEqual(row['prompt_bytes'],len('hello中文'.encode()))
    def test_cursor_with_same_timestamp_has_no_duplicates(self):
        first=self.observe('tasks',limit=1)['result']
        second=self.observe('tasks',limit=1,cursor=first['next_cursor'])['result']
        self.assertNotEqual(first['items'][0]['task_id'],second['items'][0]['task_id'])
        self.assertIsNone(second['next_cursor'])
    def test_search_escapes_sql_wildcards_and_direction(self):
        rows=self.observe('tasks',q='_%',direction='claude-codex')['result']['items']
        self.assertEqual([r['task_id'] for r in rows],[self.other])
        self.assertEqual(self.observe('tasks',direction='codex-claude')['result']['total'],1)
    def test_detail_scope_and_tokens(self):
        claimed=self.rpc('claude','task.claim',{'task_id':self.task})
        detail=self.observe('task',task_id=self.task)['result']['task']
        self.assertNotIn(claimed['claim_token'],json.dumps(detail))
        self.assertFalse(self.observe('task',task_id=self.foreign)['ok'])
    def test_filters_fail_closed(self):
        for args in [{'limit':0},{'limit':True},{'status':'oops'},{'cursor':'@@@'},{'q':'x'*513},{'extra':1}]:
            self.assertFalse(self.observe('tasks',**args)['ok'])
    def test_http_auth_host_and_origin(self):
        self.assertEqual(self.http('/observe')[0],200)
        for headers,code in [({'Authorization':''},401),({'Host':'evil.invalid'},403),({'Origin':'http://evil.invalid'},403)]:
            self.assertEqual(self.http('/observe',headers)[0],code)
        self.assertEqual(self.http('/observe?actor=claude')[0],401)
    def test_http_duplicates_unknown_routes_and_limit(self):
        for query in ['/observe?project=a&project=b','/observe/tasks?limit=bad','/observe/unknown']:
            self.assertEqual(self.http(query)[0],400)
    def test_http_roundtrip_does_not_write_receipts(self):
        before=self.receipts()
        code,result=self.http('/observe/tasks?'+urlencode({'q':'反向'}))
        self.assertEqual(code,200);self.assertEqual(result['result']['total'],1)
        self.assertEqual(before,self.receipts())

if __name__=='__main__': unittest.main()
