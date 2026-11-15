import importlib.util,json,tempfile,threading,unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from hl_credentials import transform

spec=importlib.util.spec_from_file_location('suite_setup',Path(__file__).parent/'packaging/windows/suite_setup.py')
setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)

class SuiteSetupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.home=self.root/'home';self.seen=[];self.accept=True
        outer=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                outer.seen.append(self.path)
                self.send_response(200 if outer.accept else 401);self.end_headers()
                self.wfile.write(json.dumps({'data':[{'id':'synthetic-chat-model'}]}).encode())
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
        self.base='http://127.0.0.1:'+str(self.server.server_port)
    def test_existing_key_and_settings_survive_update(self):
        setup.save_settings(self.home,self.base,'synthetic-key-not-an-account',self.root/'vault')
        key=(self.home/'key.dpapi').read_bytes()
        original=json.loads((self.home/'config.json').read_text());original['streaming']=False
        (self.home/'config.json').write_text(json.dumps(original))
        count=setup.save_settings(self.home,self.base+'/v1','',self.root/'vault')
        self.assertEqual(count,1);self.assertEqual((self.home/'key.dpapi').read_bytes(),key)
        self.assertFalse(json.loads((self.home/'config.json').read_text())['streaming'])
        self.assertEqual(transform(bytes.fromhex(key.decode()),False).decode('utf-16-le'),'synthetic-key-not-an-account')
        self.assertEqual(self.seen,['/v1/models?execution_status=ready']*2)
    def test_failed_api_check_does_not_replace_existing_configuration_or_key(self):
        setup.save_settings(self.home,self.base,'synthetic-original',self.root/'vault')
        before={name:(self.home/name).read_bytes() for name in ('key.dpapi','config.json')}
        self.accept=False
        with self.assertRaisesRegex(ValueError,'API check failed'):
            setup.save_settings(self.home,self.base,'synthetic-replacement',self.root/'other-vault')
        self.assertEqual(before,{name:(self.home/name).read_bytes() for name in before})
        self.assertFalse((self.root/'other-vault').exists())
    def test_missing_credentials_do_not_generate_tokens_or_call_api(self):
        with self.assertRaisesRegex(ValueError,'Paste your unified'):
            setup.save_settings(self.home,self.base,'',self.root/'vault')
        self.assertFalse(self.home.exists());self.assertEqual(self.seen,[])

if __name__=='__main__':unittest.main()
