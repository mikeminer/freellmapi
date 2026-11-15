import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from test_desktop_auth import USER
from desktop_auth import DesktopAuth,LoginRequired
from mobile_account import MobileAccount,AccountRequired,peer_identity

class MobileAccountTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.home=Path(self.temp.name);self.identity=dict(USER)
        self.account=MobileAccount(self.home,reader=lambda address:dict(self.identity))
    def tearDown(self):self.temp.cleanup()
    def test_login_logout_restart_preserves_pairing_and_context(self):
        (self.home/'remote').mkdir()
        (self.home/'remote/devices.json').write_text('existing pairing');(self.home/'context.json').write_text('context')
        self.assertFalse(self.account.status('phone','peer')['authenticated'])
        self.assertTrue(self.account.login('phone','peer')['authenticated'])
        other=MobileAccount(self.home,reader=lambda address:dict(self.identity))
        self.assertTrue(other.status('phone','peer')['authenticated']);self.account.logout('phone')
        self.assertFalse(other.status('phone','peer')['authenticated'])
        with self.assertRaises(AccountRequired):other.require('phone','peer')
        self.assertEqual((self.home/'remote/devices.json').read_text(),'existing pairing');self.assertEqual((self.home/'context.json').read_text(),'context')
    def test_account_node_tailnet_and_windows_change_rejected(self):
        self.account.login('phone','peer')
        for field in ('user_id','tailnet_id','windows_user','node_id'):
            self.identity={**USER,field:'different'}
            self.assertFalse(self.account.status('phone','peer',fresh=True)['authenticated'])
    def test_logout_is_per_device_and_does_not_touch_desktop_login(self):
        desktop=DesktopAuth(self.home,reader=lambda:dict(USER));desktop.login();before=desktop.session.read_bytes()
        self.account.login('phone','peer');self.account.login('other-phone','peer');self.account.logout('phone')
        self.assertTrue(self.account.status('other-phone','peer')['authenticated']);self.assertEqual(desktop.session.read_bytes(),before)
    def test_corrupt_registry_fails_closed_without_replacing_it(self):
        self.account.login('phone','peer');self.account.path.write_bytes(b'broken')
        with self.assertRaises(AccountRequired):self.account.login('phone','peer')
        self.assertEqual(self.account.path.read_bytes(),b'broken')
    def test_peer_identity_comes_from_local_whois_not_caller_claims(self):
        proof={'UserProfile':{'ID':123},'Node':{'User':123,'StableID':'phone','Addresses':['100.80.1.2/32'],'KeyExpiry':'2099-01-01T00:00:00Z'}}
        with patch.object(DesktopAuth,'verified_owner',return_value=dict(USER)),patch('mobile_account._run',return_value=proof) as run:
            self.assertEqual(peer_identity(self.home,'100.80.1.2')['node_id'],'phone');run.assert_called_once_with(['whois','--json','100.80.1.2'])
            for ip in ('127.0.0.1','8.8.8.8','192.168.1.5'):
                with self.assertRaises(LoginRequired):peer_identity(self.home,ip)
    def test_wrong_user_tagged_node_address_or_expired_peer_rejected(self):
        base={'UserProfile':{'ID':123},'Node':{'User':123,'StableID':'phone','Addresses':['100.80.1.2/32'],'KeyExpiry':'2099-01-01T00:00:00Z'}}
        for change in ({'User':999},{'Tags':['tag:server']},{'Addresses':['100.80.1.3/32']},{'KeyExpiry':'2000-01-01T00:00:00Z'}):
            proof=copy.deepcopy(base);proof['Node'].update(change)
            with patch.object(DesktopAuth,'verified_owner',return_value=dict(USER)),patch('mobile_account._run',return_value=proof):
                with self.assertRaises(LoginRequired):peer_identity(self.home,'100.80.1.2')

class AccountHttpTests(unittest.TestCase):
    def setUp(self):
        import remote_access
        self.temp=tempfile.TemporaryDirectory();self.home=Path(self.temp.name);self.data={'session':'a'*32,'messages':[]}
        self.remote=remote_access.Remote(self.home,lambda:dict(self.data)).start(port=0,private_vpn=True)
        self.remote.account.reader=lambda address:dict(USER)
        pair=self.remote.pair();paired=self.remote.pair_device({'code':pair['code']});self.device=paired['device']
        from remote_client import RemoteClient
        self.client=RemoteClient(self.home);self.client.metadata={'url':self.remote.url};self.client.token=paired['token']
    def tearDown(self):self.remote.close();self.temp.cleanup()
    def test_server_enforces_logout_even_with_valid_pairing_and_no_client_header(self):
        self.assertTrue(self.client.request('/v1/account/login',{})['authenticated'])
        self.client.state();self.client.request('/v1/account/logout',{})
        from remote_client import RemoteRejected
        for path,body in [('/v1/state',None),('/v1/input',{'request_id':'NO-ORDER-123','text':'never execute','session':'a'*32}),('/v1/cancel',{})]:
            with self.subTest(path=path),self.assertRaises(RemoteRejected) as raised:self.client.request(path,body)
            self.assertEqual(raised.exception.status,403)
        self.assertFalse(self.client.request('/v1/account/status')['authenticated']);self.assertTrue(self.remote.inputs.empty())
        self.assertTrue(self.client.request('/v1/account/login',{})['authenticated']);self.client.state()
    def test_unenrolled_mobile_header_already_requires_login(self):
        import urllib.request,urllib.error
        request=urllib.request.Request(self.remote.url+'/v1/state',headers={'Authorization':'Bearer '+self.client.token,'X-Freellmapi-Account':'required'})
        with self.assertRaises(urllib.error.HTTPError) as raised:urllib.request.urlopen(request)
        self.assertEqual(raised.exception.code,403)
    def test_peer_login_does_not_change_desktop_or_pairing(self):
        before=self.remote.devices_path.read_bytes();self.client.request('/v1/account/login',{});self.client.request('/v1/account/logout',{})
        self.assertEqual(self.remote.devices_path.read_bytes(),before);self.assertFalse((self.home/'desktop/account-session.dpapi').exists())

if __name__=='__main__':unittest.main()
