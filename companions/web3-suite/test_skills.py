import io
import tempfile
import unittest
from unittest.mock import patch
from skills_manager import Skills,source_url

class SkillTests(unittest.TestCase):
 def test_github(self):
  self.assertEqual(source_url('https://github.com/a/b/blob/main/SKILL.md'),'https://raw.githubusercontent.com/a/b/main/SKILL.md')
  with self.assertRaises(ValueError): source_url('file:///secret')
 def test_install_and_reload(self):
  with tempfile.TemporaryDirectory() as d:
   content=b'---\nname: reviewer\ndescription: Review code\n---\nRead carefully.'
   response=io.BytesIO(content); response.url='https://example.com/SKILL.md'
   with patch('urllib.request.urlopen',return_value=response):
    result=Skills(d).install(response.url,lambda _:True)
   manager=Skills(d); name=manager.list()[0]['name']
   self.assertIn(name,result); self.assertIn('Read carefully',manager.read(name))
 def test_denial(self):
  with tempfile.TemporaryDirectory() as d:
   response=io.BytesIO(b'# Skill'); response.url='https://example.com/SKILL.md'
   with patch('urllib.request.urlopen',return_value=response): Skills(d).install(response.url,lambda _:False)
   self.assertEqual(Skills(d).list(),[])
 def test_traversal(self):
  with tempfile.TemporaryDirectory() as d:
   with self.assertRaises(ValueError): Skills(d).read('../outside')
 def test_html_rejected(self):
  with tempfile.TemporaryDirectory() as d:
   response=io.BytesIO(b'<html>not markdown</html>'); response.url='https://example.com/'
   with patch('urllib.request.urlopen',return_value=response):
    with self.assertRaises(ValueError): Skills(d).install(response.url,lambda _:True)

if __name__=='__main__': unittest.main()
