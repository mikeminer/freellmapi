import copy,json,tempfile,threading,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import desktop_auth as auth

USER={'user_id':'123','tailnet_id':'tail-a','node_id':'node-a','login':'user@example.test','name':'Mìchele','windows_user':'sid-a','provider':'tailscale-local-sso'}

class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.status={'BackendState':'Running','Self':{'Online':True,'ID':'node-a','UserID':123,'TailscaleIPs':['100.100.100.100']},'CurrentTailnet':{'StableID':'tail-a'},'User':{'123':{'LoginName':'user@example.test'}}}
        self.who={'UserProfile':{'ID':123,'LoginName':'user@example.test','DisplayName':'User'},'Node':{'StableID':'node-a','User':123,'KeyExpiry':'2099-01-01T00:00:00Z'}}
    def read(self):
        with patch.object(auth,'windows_user',return_value='sid-a'):
            return auth.identity(lambda args:copy.deepcopy(self.status if args[0]=='status' else self.who))
    def test_cross_checked_identity(self):
        value=self.read();self.assertEqual(value['user_id'],'123');self.assertEqual(value['provider'],'tailscale-local-sso')
        self.assertNotIn('token',value)
    def test_offline_tagged_expired_and_missing_identity_rejected(self):
        baseline=copy.deepcopy(self.status)
        for change in ({'Online':False},{'Tags':['tag:server']},{'KeyExpired':True},{'ID':None},{'UserID':None},{'TailscaleIPs':['127.0.0.1']}):
            with self.subTest(change=change):
                self.status=copy.deepcopy(baseline);self.status['Self'].update(change)
                with self.assertRaises(auth.LoginRequired):self.read()
    def test_whois_mismatch_rejected(self):
        baseline=copy.deepcopy(self.who)
        for change in ({'StableID':'another-node'},{'User':999},{'Tags':['tag:server']},{'KeyExpiry':'2000-01-01T00:00:00Z'}):
            with self.subTest(change=change):
                self.who=copy.deepcopy(baseline);self.who['Node'].update(change)
                with self.assertRaises(auth.LoginRequired):self.read()
    def test_whois_user_not_trusted_without_matching_status_profile(self):
        self.status['User']['123']['LoginName']='someone-else@example.test'
        with self.assertRaises(auth.LoginRequired):self.read()

class SessionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name);self.user=dict(USER);self.a=auth.DesktopAuth(self.home,reader=lambda:dict(self.user))
    def tearDown(self):self.tmp.cleanup()
    def test_explicit_login_and_restart(self):
        self.assertFalse(self.a.status()['authenticated']);self.assertTrue(self.a.login()['authenticated'])
        value=auth.DesktopAuth(self.home,reader=lambda:dict(self.user)).require()
        self.assertEqual(value['identity']['name'],USER['name']);self.assertNotIn('windows_user',value['identity'])
        self.assertNotIn(USER['login'].encode(),self.a.session.read_bytes())
    def test_logout_persists_and_does_not_read_daemon_or_delete_data(self):
        self.a.login();folder=self.home/'sessions';folder.mkdir();history=folder/'chat.json';history.write_text('context')
        self.a.logout();self.a.reader=lambda:(_ for _ in ()).throw(AssertionError('No silent login'))
        for _ in range(5):self.assertFalse(self.a.status(fresh=True)['authenticated'])
        self.assertFalse(auth.DesktopAuth(self.home,reader=self.a.reader).status()['authenticated']);self.assertEqual(history.read_text(),'context')
    def test_other_account_tailnet_and_windows_user_cannot_take_over(self):
        self.a.login();owner=self.a.owner.read_bytes()
        for key in ('user_id','tailnet_id','windows_user'):
            with self.subTest(key=key):
                self.user={**USER,key:'different'}
                self.assertFalse(self.a.status(fresh=True)['authenticated'])
                with self.assertRaises(auth.LoginRequired):self.a.login()
                self.assertEqual(self.a.owner.read_bytes(),owner)
    def test_node_change_requires_explicit_login(self):
        self.a.login();self.user['node_id']='new-node'
        self.assertEqual(self.a.status(fresh=True)['state'],'device_changed')
        self.assertTrue(self.a.login()['authenticated'])
    def test_unavailable_fails_closed_but_same_account_recovers(self):
        self.a.login();reader=self.a.reader;self.a.reader=lambda:(_ for _ in ()).throw(auth.LoginRequired('offline'))
        self.assertFalse(self.a.status(fresh=True)['authenticated']);self.a.reader=reader
        self.assertTrue(self.a.status(fresh=True)['authenticated'])
    def test_corrupt_or_malformed_owner_never_overwritten(self):
        self.a.login()
        for value in (b'corrupt',None,{},[],{'user_id':'only-one-field'}):
            if isinstance(value,bytes):self.a.owner.write_bytes(value)
            else:self.a._put(self.a.owner,value)
            previous=self.a.owner.read_bytes()
            with self.assertRaises(auth.LoginRequired):self.a.login()
            self.assertEqual(self.a.owner.read_bytes(),previous);self.assertFalse(self.a.status(fresh=True)['authenticated'])
    def test_corrupt_session_does_not_automatically_reauthenticate(self):
        self.a.login();self.a.session.write_bytes(b'corrupt')
        self.assertFalse(self.a.status(fresh=True)['authenticated']);self.assertTrue(self.a.login()['authenticated'])
    def test_open_tailscale_never_logs_out_vpn_or_passes_secrets(self):
        with patch.object(auth,'executable',return_value=Path('C:/Trusted/tailscale-ipn.exe')),patch.object(auth.subprocess,'Popen') as popen:
            self.a.open_tailscale();args=popen.call_args.args[0];self.assertEqual(args,['C:\\Trusted\\tailscale-ipn.exe'])

class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import desktop_app
        from PySide6.QtWidgets import QApplication
        cls.app=QApplication.instance() or QApplication([]);cls.module=desktop_app
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name);self.responses={};self.events=[];self.executed=[]
        with patch.object(self.module,'HOME',self.home):self.api=self.module.Api(None)
        self.api.timer.stop();self.api.auth.reader=lambda:dict(USER)
        self.client=SimpleNamespace(token='session-token',metadata={'url':'test'},last_state={'messages':['secret']},request=lambda *a:self.events.append({'remote_request':a}) or {'cursor':0})
        self.api.backend=SimpleNamespace(client=self.client,connect=lambda:{'session':'same','messages':['preserved']},state=lambda:{'session':'same','messages':['preserved']},command=lambda *a:self.executed.append(a),close=lambda:None)
        from PySide6.QtCore import Qt
        self.api.response.connect(lambda id,raw:self.responses.update({id:json.loads(raw)}),Qt.ConnectionType.DirectConnection)
        self.api.event.connect(lambda raw:self.events.append(json.loads(raw)),Qt.ConnectionType.DirectConnection)
    def tearDown(self):self.api.close();self.api.pool.shutdown(wait=True);self.tmp.cleanup()
    def rpc(self,id,method,data=None):self.api.rpc(id,method,json.dumps(data or {}))
    def wait(self,condition):
        end=time.monotonic()+5
        while not condition() and time.monotonic()<end:time.sleep(.01)
        self.assertTrue(condition(),'Asynchronous operation did not finish')
    def login(self):self.api.auth.login()
    def test_every_private_rpc_denied_while_signed_out(self):
        methods=['command','answer','connector','library','snapshot','workspace_action','cancel','check_pending','drop_file','last_input','record','copy','speak','pick_files','pick_folder','pick_project']
        for method in methods:
            self.rpc(method,method);self.assertFalse(self.responses[method]['ok'],method)
        self.assertEqual(self.executed,[]);self.assertFalse(self.api.connected)
    def test_signed_out_poll_never_connects_or_reads_coordinator(self):
        self.api.poll();self.wait(lambda:not self.api.polling)
        self.assertFalse(self.api.connected);self.assertFalse(any(e.get('type')=='state' or 'remote_request' in e for e in self.events))
    def test_new_webchannel_gets_auth_even_if_initial_poll_preceded_page_load(self):
        self.login();self.api.publish_auth(self.api.auth.status());self.events.clear()
        self.rpc('ready','ready');self.wait(lambda:not self.api.polling)
        self.assertTrue(any(e.get('type')=='auth' and e['value']['authenticated'] for e in self.events))
    def test_workers_recheck_account_before_actions(self):
        self.login();self.api.auth.reader=lambda:{**USER,'tailnet_id':'other'}
        self.rpc('cmd','command',{'text':'do not execute'});self.wait(lambda:'cmd' in self.responses)
        self.assertFalse(self.responses['cmd']['ok']);self.assertEqual(self.executed,[])
    def test_logout_detaches_without_deleting_pairing_context_or_cancelling(self):
        self.login();credential=self.home/'clients/desktop-remote.dpapi';credential.write_bytes(b'preserve-existing-pairing')
        context=self.home/'context.json';context.write_text('same context')
        self.rpc('out','auth_logout');self.wait(lambda:'out' in self.responses)
        self.assertTrue(self.responses['out']['ok']);self.assertIsNone(self.client.token);self.assertIsNone(self.client.metadata);self.assertEqual(self.client.last_state,{})
        self.assertEqual(credential.read_bytes(),b'preserve-existing-pairing');self.assertEqual(context.read_text(),'same context');self.assertEqual(self.executed,[])
        self.rpc('in','auth_login');self.wait(lambda:'in' in self.responses);self.assertTrue(self.responses['in']['value']['authenticated'])
    def test_queued_command_not_executed_after_logout_requested(self):
        self.login()
        with self.api.auth.lock:
            self.rpc('cmd','command',{'text':'queued'});self.rpc('out','auth_logout')
        self.wait(lambda:all(x in self.responses for x in ('cmd','out')))
        self.assertEqual(self.executed,[]);self.assertFalse(self.responses['cmd']['ok'])
    def test_inflight_poll_cannot_repopulate_private_state_after_logout(self):
        self.login();started=threading.Event();release=threading.Event()
        def connect():started.set();release.wait(3);return {'session':'same','messages':['secret']}
        self.api.backend.connect=connect;self.api.poll();self.assertTrue(started.wait(2))
        self.rpc('out','auth_logout');release.set();self.wait(lambda:'out' in self.responses and not self.api.polling)
        self.assertFalse(any(e.get('type')=='state' for e in self.events));self.assertFalse(self.api.connected)
    def test_ui_qa_fixture_never_unlocks_real_home(self):
        with patch.object(self.module,'HOME',self.home),patch.dict('os.environ',{'FREELLMAPI_HOME':''}):api=self.module.Api(None,{'session':'fixture'})
        try:
            api.timer.stop();self.assertNotEqual(api.backend.home,self.home);self.assertTrue(api.authenticated()['authenticated'])
        finally:api.close();api.pool.shutdown(wait=True);api.qa_folder.cleanup()

if __name__=='__main__':unittest.main()
