import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from skills_manager import Skills
from web_search import Results,search

class SearchTests(unittest.TestCase):
 def test_ddg_links(self):
  p=Results(); p.feed('<a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com">A <b>result</b></a>')
  self.assertEqual(p.items,[{'title':'A result','url':'https://example.com'}])
 def test_fallback(self):
  with patch('web_search.fetch',side_effect=[RuntimeError('challenge'),'<rss><channel><item><title>Found</title><link>https://example.com</link></item></channel></rss>']):
   result=search('example')
  self.assertEqual(result['engine'],'Bing RSS'); self.assertTrue(result['warnings'])
 def test_no_fabricated_results(self):
  with patch('web_search.fetch',side_effect=RuntimeError('offline')): result=search('x')
  self.assertEqual(result['results'],[]); self.assertEqual(len(result['errors']),2)
 def test_local_package(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'package'; p.mkdir(); (p/'SKILL.md').write_text('# Test skill')
   (p/'guide.md').write_text('Supporting guide')
   s=Skills(Path(d)/'app'); name=s.link(p,lambda _:True)
   self.assertEqual(s.read(name),'# Test skill')
   self.assertEqual(s.read_file(name,'guide.md'),'Supporting guide')
   with self.assertRaises(ValueError): s.read_file(name,'../outside.txt')
 def test_discovery(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'.codex/skills/devfridge-game-builder'; p.mkdir(parents=True)
   (p/'SKILL.md').write_text('description: Build games')
   with patch('pathlib.Path.home',return_value=Path(d)):
    self.assertEqual(len(Skills(Path(d)/'app').discover('devfridge game builder')),1)

if __name__=='__main__': unittest.main()
