import json,tempfile,unittest
from pathlib import Path
from memory_vault import Vault
from brain_memory import consolidate,projects
from semantic_memory import SemanticIndex

class BrainTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  self.v=Vault(self.root/'vault'/'freellmapi-memory',self.root/'alpha')
 def test_project_scope_preserves_archives(self):
  other=Vault(self.v.path,self.root/'beta')
  other.record([{'role':'user','content':'secret beta launch'}],self.root/'beta.json')
  self.v.record([{'role':'user','content':'alpha launch plan'}],self.root/'alpha.json')
  self.assertNotIn('secret beta',str(list(self.v.documents())))
  self.assertEqual(len(list((self.v.path/'Conversations').glob('*.md'))),2)
  self.assertEqual(len(projects(self.v)),2)
 def test_consolidation_idempotent_preserves_sources(self):
  self.v.operational.ingest([{'role':'user','content':'Build alpha'}],self.root/'s.json')
  before={p.name:p.read_bytes() for p in (self.v.operational.folder/'Evidence').glob('*.md')}
  result=consolidate(self.v);text=Path(result['path']).read_text();consolidate(self.v)
  self.assertEqual(text,Path(result['path']).read_text())
  self.assertIn('Build alpha',text);self.assertEqual(before,{p.name:p.read_bytes() for p in (self.v.operational.folder/'Evidence').glob('*.md')})
 def test_vectors_match_without_word_overlap_and_cache_is_durable(self):
  calls=[]
  def embed(texts):
   calls.append(texts);return 'fixed',[[1,0] if 'fruit' not in t else [0,1] for t in texts]
  index=SemanticIndex(self.root/'index.db',embed);docs=[('car','vehicle'),('apple','fruit')]
  self.assertEqual(index.search('automobile',docs)[0]['path'],'car')
  index=SemanticIndex(self.root/'index.db',embed);index.search('automobile',docs)
  self.assertEqual(calls[-1],['automobile'])
 def test_embedding_model_change_never_compares_old_vectors(self):
  import sqlite3
  index=SemanticIndex(self.root/'index.db',lambda texts:('a',[[1,0] for _ in texts]));index.search('q',[('note','body')])
  index.embed=lambda texts:('b',[[0,1] for _ in texts])
  rows=index.search('q',[('note','body')])
  self.assertEqual(rows[0]['path'],'note');self.assertEqual(index.status['model'],'b');self.assertFalse(index.status['partial'])
  db=sqlite3.connect(self.root/'index.db')
  try:
   self.assertEqual(db.execute("SELECT COUNT(*) FROM vectors WHERE model='a'").fetchone()[0],0)
   self.assertEqual(db.execute("SELECT COUNT(*) FROM vectors WHERE model='b'").fetchone()[0],1)
  finally: db.close()
 def test_failure_falls_back_without_retries(self):
  calls=[]
  def fail(texts):calls.append(texts);raise TimeoutError()
  index=SemanticIndex(self.root/'index.db',fail)
  index.search('q',[]);index.search('q',[])
  self.assertEqual(len(calls),1);self.assertEqual(index.status['mode'],'lexical_fallback')
 def test_shared_references_are_searchable(self):
  refs=self.v.path.parent/'references';refs.mkdir();(refs/'manual.md').write_text('deployment checklist')
  self.assertIn('deployment checklist',self.v.search('deployment checklist'))
 def test_cross_project_search_requires_known_id_and_keeps_active_project(self):
  other=Vault(self.v.path,self.root/'beta');other.record([{'role':'user','content':'orchid architecture'}],self.root/'beta.json')
  original=self.v.project_id
  self.assertIn('orchid',self.v.search_project('orchid architecture',other.project_id))
  self.assertEqual(self.v.project_id,original)
  with self.assertRaises(ValueError): self.v.search_project('orchid','unknown')
 def test_reload_preserves_consolidation_and_archive(self):
  messages=[{'role':'user','content':'Keep the existing architecture'}];session=self.root/'s.json'
  self.v.record(messages,session);self.v.operational.ingest(messages,session)
  before={p.name:p.read_bytes() for p in (self.v.path/'Conversations').glob('*.md')}
  reloaded=Vault(self.v.path,self.v.project);reloaded.record(messages,session)
  self.assertEqual(before,{p.name:p.read_bytes() for p in (self.v.path/'Conversations').glob('*.md')})
  self.assertIn('architecture',reloaded.operational.snapshot('architecture')['text'])
