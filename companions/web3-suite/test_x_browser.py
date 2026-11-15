import tempfile
import unittest
from types import SimpleNamespace
from browser_control import Browser
from x_browser import read,target

class DirectXTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory()
        cls.browser=Browser(cls.tmp.name); cls.browser.start(headless=True)
        cls.agent=SimpleNamespace(browser=cls.browser,interactive=True,depth=0)
    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.tmp.cleanup()
    def page(self,html):
        self.browser.context.unroute_all()
        self.browser.context.route('**/*',lambda route:route.fulfill(status=200,content_type='text/html',body=html))
    def test_extract_actual_dom_and_deduplicate(self):
        self.page('''<article data-testid="tweet"><div data-testid="User-Name">Alice @alice</div>
          <a href="https://x.com/alice/status/123"><time datetime="2026-11-12T10:00:00Z">Today</time></a>
          <div data-testid="tweetText">Real observed text</div><div role="group" aria-label="2 likes"></div></article>''')
        result=read(self.agent,'x_search',{'query':'from:alice','limit':2})
        self.assertEqual(result['status'],'observed'); self.assertEqual(len(result['posts']),1)
        self.assertEqual(result['posts'][0]['text'],'Real observed text')
        self.assertEqual(result['posts'][0]['timestamp'],'2026-11-12T10:00:00Z')
        self.assertFalse(result['complete'])
    def test_login_is_blocker_not_empty_success(self):
        self.page('<h1>Sign in to X</h1><input autocomplete="username">')
        result=read(self.agent,'x_search',{'query':'from:alice'})
        self.assertEqual(result['status'],'login_required'); self.assertEqual(result['posts'],[])
    def test_empty_page_is_unavailable(self):
        self.page('<div data-testid="emptyState">Nothing found</div>')
        self.assertEqual(read(self.agent,'x_search',{'query':'nothing'})['status'],'unavailable')
    def test_post_does_not_return_reply_as_requested_post(self):
        self.page('<article data-testid="tweet"><a href="https://x.com/alice/status/999"><time>Today</time></a><div data-testid="tweetText">A reply</div></article>')
        self.assertEqual(read(self.agent,'x_post',{'id':'123'})['posts'],[])
    def test_no_cursor_or_key(self):
        import social_tools
        self.assertFalse(hasattr(social_tools,'BASES'))
        self.assertFalse(social_tools.status(self.tmp.name)['x']['api_key_required'])
        self.assertEqual(target('x_profile',{'username':'@anonimocommando'})[0],'https://x.com/anonimocommando')
        with self.assertRaises(ValueError): target('x_search',{'query':'test','cursor':'old-api-cursor'})

if __name__=='__main__': unittest.main()
