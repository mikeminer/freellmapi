import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from secret_output import StreamFilter,redact
from node_preflight import check_script
from work_ledger import record_request,load,render
from shell_runner import run

MINT='39kMeX4HVRW9qbbiHSPbRQ9xeXUF18GrNP6gL61Ppump'
class TokenWorkflowTests(unittest.TestCase):
 def test_empty_compaction_preserves_history_and_does_not_repeat(self):
  from freellmapi import Agent
  with tempfile.TemporaryDirectory() as tmp:
   agent=Agent(tmp,'http://localhost','test','auto');agent.session=Path(tmp)/'s.json';agent.context_limit=4096
   original=[{'role':'user','content':'original'},{'role':'assistant','content':'work'},{'role':'user','content':'continue build'}];agent.messages=list(original)
   with patch.object(agent,'request_turn',return_value={'choices':[{'message':{'content':None}}]}) as request:
    agent.compact(True);agent.compact()
   self.assertEqual(agent.messages,original);self.assertEqual(request.call_count,1)
 def test_build_reuse_requires_identical_sources_and_apk(self):
  from build_checkpoint import cached,record
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'src').mkdir();(root/'src/main.js').write_text('game')
   (root/'build-debug.ps1').write_text('build');apk=root/'android/app/build/outputs/apk/debug/app-debug.apk';apk.parent.mkdir(parents=True);apk.write_bytes(b'apk')
   session=root/'s.json';command='pwsh -File ./build-debug.ps1'
   record(session,root,command,'Exit code: 0')
   self.assertIn('nessun comando rieseguito',cached(session,root,command))
   apk.write_bytes(b'changed');self.assertIsNone(cached(session,root,command))
   apk.write_bytes(b'apk');(root/'src/main.js').write_text('new source');self.assertIsNone(cached(session,root,command))
 def test_denied_shell_does_not_execute_preflight(self):
  from freellmapi import Agent
  with tempfile.TemporaryDirectory() as tmp:
   agent=Agent(tmp,'http://localhost','test','auto',interactive=False);agent.mode='ask'
   with patch('node_preflight.check_script') as check:
    self.assertEqual(agent.tool('shell',{'command':'node create-token.js'}),'Comando negato.')
   check.assert_not_called()
 def test_model_alias_uses_catalog_context_window(self):
  from model_health import catalog_context
  self.assertEqual(catalog_context([{'id':'gpt-oss-120b','context_window':131072}],'openai/gpt-oss-120b'),131072)
  self.assertEqual(catalog_context([], 'unknown'),16000)
  self.assertEqual(catalog_context([{'id':'auto','context_window':131072}],'auto:fast'),131072)
 def test_compaction_recovers_from_selected_model_cooldown(self):
  from freellmapi import Agent
  from model_health import RateLimited
  with tempfile.TemporaryDirectory() as tmp:
   agent=Agent(tmp,'http://localhost','test','selected');agent.mode='auto';agent.session=Path(tmp)/'s.json'
   agent.messages=[{'role':'user','content':'original task'},{'role':'assistant','content':'previous work'},{'role':'user','content':'continue the build'}]
   summary={'choices':[{'message':{'content':'Original task, completed work and existing token preserved.'}}]}
   with patch.object(agent,'api',side_effect=[RateLimited('cooldown'),summary]):agent.compact(True)
   self.assertIn('existing token',agent.messages[0]['content']);self.assertEqual(agent.messages[-1]['content'],'continue the build')
 def test_background_log_is_also_filtered(self):
  import time
  from coding_tools import Processes
  with tempfile.TemporaryDirectory() as tmp:
   manager=Processes(tmp);row=manager.start("Write-Output 'secretKey: synthetic-background-secret'; Write-Output 'done'",tmp,10)
   for _ in range(100):
    current=manager.read(row['id'])
    if current['process']['state']!='running':break
    time.sleep(.05)
   self.assertNotIn('synthetic-background-secret',current['output']);self.assertIn('done',current['output'])
 def test_explicit_skill_loads_in_noninteractive_mode_without_model_selection(self):
  from types import SimpleNamespace
  from skill_router import Router
  skills=SimpleNamespace(list=lambda:[{'name':'devfridge-game-builder-abcdef12'}])
  calls=[];agent=SimpleNamespace(depth=0,interactive=False,skills=skills,tool=lambda name,args:calls.append((name,args)))
  Router().run(agent,'Usa devfridge game builder e completa APK.',lambda _:None)
  self.assertEqual(calls,[('skill_load',{'name':'devfridge-game-builder-abcdef12'})])
 def test_token_survives_continuation_and_long_request(self):
  with tempfile.TemporaryDirectory() as tmp:
   session=Path(tmp)/'s.json';record_request(session,'Build game '+('x'*5000))
   record_request(session,'usa '+MINT+' come token');record_request(session,'procedi',continuation=True)
   self.assertEqual(load(session)['token_mint'],MINT);self.assertIn(MINT,render(session))
 def test_existing_mint_blocks_creation_before_any_process(self):
  with tempfile.TemporaryDirectory() as tmp:
   script=Path(tmp)/'token.js';script.write_text('createMint(connection);')
   with patch('node_preflight.subprocess.run') as command,self.assertRaisesRegex(ValueError,'existing_token_required'):check_script(script,tmp,MINT)
   command.assert_not_called()
 def test_private_key_print_blocked_before_execution(self):
  with tempfile.TemporaryDirectory() as tmp:
   script=Path(tmp)/'token.js';script.write_text('console.log(pair.secretKey);')
   with self.assertRaisesRegex(ValueError,'secret_output_blocked'):check_script(script,tmp)
 def test_split_private_output_is_redacted(self):
  stream=StreamFilter();out=stream.feed('Chiave pri')+stream.feed('vata: '+'12,'*63+'12\nPublic address: abc\n')
  self.assertNotIn('12,',out);self.assertIn('Public address: abc',out)
 def test_multiline_key_array_is_redacted(self):
  stream=StreamFilter();out=stream.feed('secretKey: Uint8Array(64) [\n 1,2,3,\n 4,5\n]\ndone\n')
  self.assertNotIn('1,2,3',out);self.assertIn('done',out)
 def test_shell_logs_and_ui_receive_only_redacted_output(self):
  with tempfile.TemporaryDirectory() as tmp,patch('shell_runner.ui.command_output') as display:
   result=run("Write-Output 'Chiave privata: synthetic-secret'; Write-Output 'done'",tmp,tmp,10)
   self.assertNotIn('synthetic-secret',result)
   self.assertNotIn('synthetic-secret',str(display.call_args_list))
   self.assertNotIn('synthetic-secret',''.join(p.read_text() for p in (Path(tmp)/'shell-logs').glob('*.log')))
 def test_real_missing_export_is_caught_without_running_target(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);package=root/'node_modules'/'fake-api';package.mkdir(parents=True)
   (package/'package.json').write_text(json.dumps({'type':'module','exports':'./index.js'}));(package/'index.js').write_text('export function valid(){}')
   script=root/'target.mjs';script.write_text("import { invented } from 'fake-api';\nthrow new Error('must never run');")
   with self.assertRaisesRegex(ValueError,'invented'):check_script(script,root)
