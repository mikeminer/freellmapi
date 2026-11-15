import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import remote_access
from remote_client import RemoteClient
from session_lease import SessionLease
from session_routing import owner

class SessionRoutingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.home=Path(self.tmp.name);self.aid='a'*32;self.bid='b'*32
        self.a=remote_access.Remote(self.home,lambda:{'session':self.aid,'messages':[{'role':'user','content':'favola'}]}).start(port=0,private_vpn=True)
        pair=self.a.pair();login=self.a.pair_device({'code':pair['code']})
        # Two actual listeners/process identities, isolated fixture in one test process.
        with patch.object(remote_access,'_active',None):self.b=remote_access.Remote(self.home,lambda:{'session':self.bid,'messages':[{'role':'user','content':'other context'}]}).start(port=0,private_vpn=True)
        self.client=RemoteClient(self.home);self.client.metadata={'url':self.b.url,'instance':self.b.instance};self.client.token=login['token'];self.client.device=login['device'];self.client.state()
        folder=self.home/'sessions/conversations';folder.mkdir(parents=True);self.lease=SessionLease(folder/(self.aid+'.json'))
    def tearDown(self):self.lease.close();self.a.close();self.b.close();self.tmp.cleanup()
    def test_native_client_attaches_existing_writer_without_queue_or_replay(self):
        result=self.client.send('/resume '+self.aid)
        self.assertEqual(result['state'],'attached');self.assertEqual(self.client.last_state['session'],self.aid)
        self.assertEqual(self.client.last_state['messages'][0]['content'],'favola');self.assertTrue(self.a.inputs.empty());self.assertTrue(self.b.inputs.empty());self.assertFalse(self.client.pending.exists())
        with self.assertRaises(ValueError):SessionLease(self.lease.session)
    def test_phone_resume_gets_explicit_nonqueued_handoff(self):
        result=self.client.request('/v1/input',{'request_id':'select-session-001','text':'/resume '+self.aid,'session':self.bid,'session_handoff':True})
        self.assertEqual(result['state'],'handoff');self.assertTrue(result['not_enqueued']);self.assertEqual(result['coordinator']['url'],self.a.url);self.assertTrue(self.b.inputs.empty())
    def test_unknown_or_other_pc_handoff_cannot_send_token_or_change_client(self):
        previous=self.client.metadata.copy()
        with self.assertRaises(RuntimeError):self.client.attach({'url':'http://100.99.1.2:31416','instance':'x'},self.aid)
        self.assertEqual(self.client.metadata,previous)
    def test_registry_refresh_accepts_existing_pair_on_other_bridge(self):
        p=self.a.pair();new=self.a.pair_device({'code':p['code']})
        self.assertEqual(self.b.authorize('Bearer '+new['token']),new['device'])
        self.a.revoke()
        with self.assertRaises(PermissionError):self.b.authorize('Bearer '+new['token'])

if __name__=='__main__':unittest.main()
