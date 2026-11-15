import contextlib,io,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import qrcode
import remote_access as r
from remote_pair_browser import open_pair

class PairBrowserTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.home=Path(self.temp.name)
        qr=self.home/'pair.png';qrcode.make('freellmapi://synthetic-test-only').save(qr)
        self.pair={'qr':str(qr),'url':'http://127.0.0.1:31416','code':'00000000','expires_seconds':120,'fingerprint':'','link':'freellmapi://synthetic-test-only'}
    def test_page_is_local_embedded_and_opens_default_browser_in_a_window(self):
        with patch('webbrowser.open',return_value=True) as browser:result=open_pair(self.pair)
        page=Path(result['page']);text=page.read_text(encoding='utf-8')
        browser.assert_called_once_with(page.as_uri(),new=1,autoraise=True)
        self.assertIn('data:image/png;base64,',text);self.assertIn("default-src 'none'",text);self.assertIn('Codice scaduto',text)
        self.assertTrue(result['opened'])
    def test_visible_fields_are_escaped_and_never_become_script(self):
        with patch('webbrowser.open',return_value=True):result=open_pair(dict(self.pair,url='<script>injected()</script>'))
        text=Path(result['page']).read_text(encoding='utf-8');self.assertNotIn('<script>injected()',text);self.assertIn('&lt;script&gt;',text)
    def test_start_already_active_refreshes_and_opens_qr_without_model(self):
        remote=SimpleNamespace(url=self.pair['url'],pair=Mock(return_value=self.pair));agent=SimpleNamespace(api=Mock())
        with patch.object(r,'active',return_value=remote),patch('webbrowser.open',return_value=True) as browser,contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(r.command(agent,'/remote start',self.home,[]))
        browser.assert_called_once();agent.api.assert_not_called()
    def test_unattended_start_does_not_open_a_browser(self):
        remote=SimpleNamespace(url=self.pair['url'],pair=Mock(return_value=self.pair))
        with patch.object(r,'active',return_value=remote),patch('webbrowser.open') as browser,contextlib.redirect_stdout(io.StringIO()):
            r.command(SimpleNamespace(),'/remote start',self.home,[],open_browser=False)
        browser.assert_not_called()
    def test_existing_other_process_bridge_pair_opens_browser(self):
        with patch.object(r,'active',return_value=None),patch('remote_pairing.request_pair',return_value=self.pair),patch('webbrowser.open',return_value=True) as browser,contextlib.redirect_stdout(io.StringIO()):
            r.command(SimpleNamespace(),'/remote pair',self.home,[])
        browser.assert_called_once()
    def test_browser_failure_keeps_valid_pairing_and_api_status(self):
        agent=SimpleNamespace(api_state='OK');remote=SimpleNamespace(url=self.pair['url'],pair=Mock(return_value=self.pair))
        with patch.object(r,'active',return_value=remote),patch('webbrowser.open',side_effect=OSError('synthetic failure')),contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(r.command(agent,'/remote start',self.home,[]))
        self.assertEqual(agent.api_state,'OK');self.assertTrue((self.home/'pair.html').exists())

if __name__=='__main__':unittest.main()
