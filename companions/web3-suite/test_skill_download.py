import io,zipfile,tempfile,unittest
from unittest.mock import patch
from skills_manager import Skills
from skill_download import unpack,fetch

def archive(entries):
 b=io.BytesIO()
 with zipfile.ZipFile(b,'w') as z:
  for name,text in entries.items(): z.writestr(name,text)
 return b.getvalue()

class DownloadTests(unittest.TestCase):
 def test_large_markdown(self):
  files,body=unpack(b'# Skill\n'+b'x'*200000); self.assertGreater(len(body),128000)
 def test_package_support_files(self):
  files,body=unpack(archive({'builder/SKILL.md':'# Main','builder/references/guide.md':'guide','builder/vendor/child/SKILL.md':'# Child'}))
  self.assertIn('references/guide.md',files); self.assertEqual(body,'# Main')
 def test_traversal(self):
  for bad in ('../escape','/escape','C:/escape','a/../../escape'):
   with self.assertRaises(ValueError): unpack(archive({'SKILL.md':'ok',bad:'bad'}))
 def test_html_follow_and_update(self):
  with tempfile.TemporaryDirectory() as d:
   def response(data,url):
    r=io.BytesIO(data); r.url=url; return r
   page=response(b'<html><a href="/skill.zip">download</a></html>','https://example.com/skill')
   zipped=archive({'builder/SKILL.md':'---\nname: builder\n---\nnew','builder/reference.md':'guide'})
   with patch('urllib.request.urlopen',side_effect=[page,response(zipped,'https://example.com/skill.zip')]):
    s=Skills(d); s.install('example.com/skill',lambda _:True)
   name=s.list()[0]['name']; self.assertEqual(s.read_file(name,'reference.md'),'guide')
   with patch('urllib.request.urlopen',return_value=response(zipped,'https://example.com/skill.zip')):
    s.install('example.com/skill',lambda _:True)
   self.assertTrue(list((s.home/'.backups').glob('*')))

if __name__=='__main__': unittest.main()
