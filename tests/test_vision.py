import base64, hashlib, json, shutil, struct, unittest, uuid, zlib
from pathlib import Path
from relay_collaboration.vision import validate_images, png_size, claude_message, image_manifest
from relay_collaboration.bridge_core import Broker, PROTOCOL
from relay_collaboration.console_backend import Reviews, ConsoleError

def chunk(kind,data):
    return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
def png(width=2,height=2,extra=b''):
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+extra+chunk(b'IDAT',zlib.compress((b'\0'+b'\xff\0\0'*width)*height))+chunk(b'IEND',b'')
def item(raw=None):
    raw=png() if raw is None else raw
    return {'name':'fixture.png','media_type':'image/png','data':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest()}

class VisionTests(unittest.TestCase):
    def test_worker_preserves_images_through_compact_listing_and_restart(self):
        from tests import test_worker_core as fixtures
        case=fixtures.WorkerCoreTests();case.setUp();self.addCleanup(case.tearDown)
        lead=case.call('codex','lead.acquire',{'project':'schematic','ttl_seconds':30})
        task=case.call('codex','task.submit',{'project':'schematic','leader_token':lead['leader_token'],'to':'claude','title':'image fixture','prompt':'question','mode':'proposal','images':[item()]})
        worker=case.worker();worker.run_once()
        self.assertEqual(case.executor.calls[0][0]['images'],[item()])
        worker.run_once();self.assertEqual(len(case.executor.calls),1)
        self.assertEqual(case.call('claude','task.get',{'task_id':task['task_id']})['status'],'completed')
    def test_actual_executor_builds_image_stdin_without_tools(self):
        from tests import test_cli_executor as fixtures
        case=fixtures.ExecutorTests();case.setUp();self.addCleanup(case.doCleanups)
        executor=case.executor();self.assertTrue(executor.preflight()['ok'])
        result=executor({'task_id':'fixture','mode':'proposal','prompt':'question','images':[item()]},fixtures.context())
        self.assertTrue(result['ok'],result)
        self.assertEqual(result['input_images'][0]['sha256'],item()['sha256'])
        sent=json.loads(result['text'].removeprefix('fixture response: '))
        self.assertEqual(sent['message']['content'][-1]['source']['data'],item()['data'])
        args=executor.build_command(True)
        self.assertEqual(args[args.index('--input-format')+1],'stream-json')
        self.assertEqual(args[args.index('--tools')+1],'')
    def test_png_roundtrip_and_direct_message(self):
        images=validate_images([item()]);self.assertEqual(image_manifest(images)[0]['width'],2)
        value=json.loads(claude_message('question',images));self.assertEqual(value['message']['content'][-1]['type'],'image')
        self.assertEqual(value['message']['content'][-1]['source']['data'],item()['data'])
    def test_reject_invalid_types_bytes_crc_bombs_and_frames(self):
        bad=[b'notpng',png()[:-1],png()[:30]+b'xx'+png()[32:],png(extra=chunk(b'acTL',b'12345678')),
             png(extra=chunk(b'zTXt',zlib.compress(b'x'*100000))),png(extra=chunk(b'iCCP',b'x')),png(extra=chunk(b'iTXt',b'x'))]
        for raw in bad:
            with self.subTest(raw=raw[:10]):
                with self.assertRaises(ValueError):validate_images([item(raw)])
        oversized=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',999999,999999,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\0'))+chunk(b'IEND',b'')
        with self.assertRaises(ValueError):validate_images([item(oversized)])
        bomb=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',2,2,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'x'*100000))+chunk(b'IEND',b'')
        with self.assertRaises(ValueError):validate_images([item(bomb)])
    def test_sha_names_schema_and_total_limits(self):
        for value in [item()|{'sha256':'0'*64},item()|{'data':'!!'},item()|{'name':'../x.png'},item()|{'media_type':'image/svg+xml'},item()|{'path':'x'}]:
            with self.assertRaises(ValueError):validate_images([value])
        with self.assertRaises(ValueError):validate_images([item(),item()])
        with self.assertRaises(ValueError):validate_images([item()]*9)
        with self.assertRaises(ValueError):validate_images([item()|{'data':'A'*1400000}])
    def test_console_identity_binds_image_bytes(self):
        body={'client_request_id':str(uuid.uuid4()),'title':'fixture','prompt':'question','contexts':[],'images':[item()]}
        a=Reviews.prepare(body);b=Reviews.prepare(body|{'images':[item(png(3,2))]})
        self.assertNotEqual(a[-1],b[-1]);self.assertEqual(a[2][0]['sha256'],item()['sha256'])
        with self.assertRaises(ConsoleError):Reviews.prepare(body|{'contexts':[{'name':'fixture.png','text':'x'}]})
    def test_broker_migration_and_image_list_is_compact(self):
        root=Path(__file__).resolve().parents[1]/'work'/('vision-'+uuid.uuid4().hex);root.mkdir();self.addCleanup(shutil.rmtree,root)
        broker=Broker(root/'db.sqlite')
        def call(op,args):
            return broker.handle({'protocol':PROTOCOL,'request_id':str(uuid.uuid4()),'actor':'codex','op':op,'args':args})
        # Broker protocol fixture uses the same validated envelope as HTTP.
        lead=call('lead.acquire',{'project':'relay','ttl_seconds':120})
        self.assertTrue(lead['ok'],lead)
        args={'project':'relay','leader_token':lead['result']['leader_token'],'to':'claude','title':'x','prompt':'x','images':[item()]}
        task=call('task.submit',args);self.assertTrue(task['ok'],task)
        self.assertEqual(task['result']['images'],[item()])
        listed=call('task.list',{'to':'claude','project':'relay'});self.assertTrue(listed['ok'],listed)
        self.assertNotIn('images',listed['result']['tasks'][0]);self.assertEqual(listed['result']['tasks'][0]['image_count'],1)

if __name__=='__main__':unittest.main()
