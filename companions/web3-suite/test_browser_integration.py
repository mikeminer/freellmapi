"""Real Chrome integration test on a local fixture; no external websites."""
import tempfile
import unittest
from pathlib import Path
from browser_control import Browser

class BrowserIntegration(unittest.TestCase):
 def test_page_actions(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'test.html'
   p.write_text('''<!doctype html><html><title>freellmapi browser test</title><body>
   <input aria-label="Nome"><button id="go">Conferma</button><p id="result">Pronto</p>
   <script>document.getElementById('go').onclick=()=>{document.getElementById('result').textContent='OK '+document.querySelector('input').value;};</script>
   </body></html>''',encoding='utf-8')
   b=Browser(d)
   try:
    b.start(headless=True)
    self.assertIn('e1: Nome',b.navigate(str(p),d))
    b.fill('e1','freellmapi')
    self.assertIn('OK freellmapi',b.click('e2'))
    self.assertTrue(Path(b.screenshot()).is_file())
    with self.assertRaises(ValueError): b.click('e999')
   finally: b.close()
   self.assertFalse(b.active())

if __name__=='__main__': unittest.main()
