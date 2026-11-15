import tempfile,unittest,json
from pathlib import Path
from unittest.mock import Mock
from memory_relevance import lexical_score,select_semantic
from memory_vault import Vault

class RelevanceTests(unittest.TestCase):
 def test_http_batches_reassemble_indices_without_mixing_models(self):
  from test_semantic_regressions import serve,good_body
  from semantic_memory import EmbeddingClient
  server=serve([(200,{},json.dumps(good_body([[1,0]]*n)).encode()) for n in (8,8,2)])
  self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
  with tempfile.TemporaryDirectory() as tmp:
   client=EmbeddingClient(f'http://127.0.0.1:{server.server_port}','test',Path(tmp)/'status.json')
   model,rows=client.embed(['synthetic']*18,wait_s=4)
   self.assertEqual(len(rows),18);self.assertEqual(client.http_calls,3);self.assertEqual(server.calls,3)
 def test_changed_model_between_http_batches_is_rejected(self):
  from test_semantic_regressions import serve,good_body
  from semantic_memory import EmbeddingClient
  server=serve([(200,{},json.dumps(good_body([[1,0]]*8,model='first')).encode()),(200,{},json.dumps(good_body([[1,0]],model='second')).encode())])
  self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
  with tempfile.TemporaryDirectory() as tmp:
   client=EmbeddingClient(f'http://127.0.0.1:{server.server_port}','test',Path(tmp)/'status.json')
   with self.assertRaisesRegex(ValueError,'identity changed'):client.embed(['synthetic']*9,wait_s=4)
   self.assertEqual(client.http_calls,2);self.assertEqual(client.calls,1)
 def test_metadata_and_project_substrings_do_not_inject_notes(self):
  self.assertEqual(lexical_score('In beta-archive, where is the exotic flower protected?', '# Ricordo\nProgetto: beta-archive\nBETA-ORCHID is in a conservatory.','beta-archive'),0)
  self.assertEqual(lexical_score('cat','The catalog describes a cataloguer.'),0)
  self.assertGreater(lexical_score('AUTH_TIMEOUT','The AUTH_TIMEOUT error recurs.'),0)
 def test_neighbor_above_absolute_threshold_can_be_rejected(self):
  rows=[{'path':'good','score':.75},{'path':'neighbor','score':.68},{'path':'weak','score':.59}]
  self.assertEqual([r['path'] for r in select_semantic(rows)],['good'])
 def test_abstention_when_no_supported_note_remains(self):
  self.assertEqual(select_semantic([{'path':'wrong','score':.6117}]),[])
 def test_same_note_lexical_and_semantic_is_counted_as_semantic(self):
  with tempfile.TemporaryDirectory() as tmp:
   vault=Vault(Path(tmp)/'vault',Path(tmp)/'beta-archive')
   name=vault.remember('The orchid flower is protected in a conservatory.','project')
   text=vault.read(name)
   vault.semantic=Mock();vault.semantic.status={'mode':'semantic','index_state':'ready'}
   vault.semantic_search=Mock(return_value=[{'path':name,'text':text,'score':.75}])
   snapshot=vault.operational.snapshot('Where is the orchid flower?')
   self.assertEqual(snapshot['retrieval']['mode'],'semantic')
   self.assertEqual(snapshot['retrieval']['semantic_inserted'],[name])
   self.assertEqual(sum(i['path']==name for i in snapshot['items']),1)
 def test_auto_snapshot_does_not_resurrect_old_request_through_state(self):
  with tempfile.TemporaryDirectory() as tmp:
   vault=Vault(Path(tmp)/'vault',Path(tmp)/'alpha')
   vault.operational.ingest([{'role':'user','content':'Where is the optical instrument?'}],'s')
   snapshot=vault.operational.snapshot('optical instrument')
   self.assertNotIn('State.md',[Path(i['path']).name for i in snapshot['items']])
   self.assertTrue(any(i['path'].endswith('Consolidated.md') for i in snapshot['items']))
 def test_semantic_chunk_is_inserted_when_lexical_excerpt_is_different(self):
  with tempfile.TemporaryDirectory() as tmp:
   vault=Vault(Path(tmp)/'vault',Path(tmp)/'alpha')
   name=vault.remember('orchid flower introduction. '+'filler '*300+'FINAL-RELEVANT-CHUNK','project')
   vault.semantic=Mock();vault.semantic.status={'mode':'semantic','index_state':'ready'}
   vault.semantic_search=Mock(return_value=[{'path':name,'text':'FINAL-RELEVANT-CHUNK','score':.75}])
   snapshot=vault.operational.snapshot('orchid flower')
   self.assertIn('FINAL-RELEVANT-CHUNK',snapshot['text'])
   self.assertEqual(snapshot['retrieval']['mode'],'semantic')
