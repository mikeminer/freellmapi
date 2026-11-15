import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from desktop_actions import local_request,open_chrome,target_uri
from freellmapi import Agent

class DesktopTests(unittest.TestCase):
 def test_local_action_avoids_api(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'front end.html'; p.write_text('<html></html>')
   a=Agent(d,'http://localhost','test','auto'); a.session=Path(d)/'session.json'
   a.messages=[{'role':'assistant','content':f'Creato `{p}`'}]
   with patch.object(a,'api') as api,patch('freellmapi.open_chrome',return_value='Aperto') as launch:
    a.turn('aprimelo su chrome')
    api.assert_not_called(); launch.assert_called_once_with(str(p),a.root)
 def test_missing_and_unsafe_targets(self):
  with self.assertRaises(ValueError): target_uri('javascript:alert(1)',Path.cwd())
  with self.assertRaises(FileNotFoundError): target_uri('missing-nonexistent.html',Path.cwd())
 def test_chrome_arguments(self):
  with patch('desktop_actions.chrome_path',return_value='chrome.exe'),patch('desktop_actions.subprocess.Popen') as popen:
   open_chrome('https://example.com/?a=1&b=2',Path.cwd())
   self.assertEqual(popen.call_args.args[0],['chrome.exe','--new-tab','https://example.com/?a=1&b=2'])
 def test_ambiguous(self):
  with tempfile.TemporaryDirectory() as d:
   for name in ('a.html','b.html'): (Path(d)/name).write_text('test')
   with self.assertRaises(ValueError): local_request('aprilo in chrome',[{'role':'assistant','content':'`a.html` e `b.html`'}],d)
 def test_plan_does_not_launch(self):
  with tempfile.TemporaryDirectory() as d:
   a=Agent(d,'http://localhost','test','auto'); a.mode='plan'
   with patch('freellmapi.open_chrome') as launch:
    a.turn('/chrome https://example.com'); launch.assert_not_called()

if __name__=='__main__': unittest.main()
