import tempfile
import unittest
from browser_control import Browser
from x_publisher import prepare,submit

PAGE='''<button data-testid="SideNav_AccountSwitcher_Button">Alice @alice</button>
<div contenteditable="true" data-testid="tweetTextarea_0"></div>
<button data-testid="addButton" onclick="let d=document.createElement('div');d.contentEditable='true';d.dataset.testid='tweetTextarea_'+document.querySelectorAll('[contenteditable]').length;document.body.append(d)">Add</button>
<button data-testid="tweetButton" onclick="document.querySelector('[data-testid=toast]').innerHTML='<a href=https://x.com/alice/status/123>View</a>'">Post</button>
<div data-testid="toast"></div>'''

class PublisherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(); cls.browser=Browser(cls.tmp.name);cls.browser.start(headless=True)
    @classmethod
    def tearDownClass(cls): cls.browser.close();cls.tmp.cleanup()
    def setUp(self):
        self.browser.context.unroute_all()
        self.browser.context.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html',body=PAGE))
    def test_thread_and_acknowledgement(self):
        page,button=prepare(self.browser,{'account':'alice','text':'First\n---\nSecond'})
        self.assertEqual(page.locator('[data-testid="tweetTextarea_1"]').inner_text(),'Second')
        result=submit(page,button)
        self.assertEqual(result['state'],'submitted');self.assertEqual(result['url'],'https://x.com/alice/status/123')
    def test_wrong_account_blocked(self):
        with self.assertRaisesRegex(ValueError,'Account X attivo diverso'):
            prepare(self.browser,{'account':'bob','text':'Do not send'})
        self.assertEqual(self.browser.page.locator('[data-testid="toast"]').inner_text(),'')

if __name__=='__main__': unittest.main()
