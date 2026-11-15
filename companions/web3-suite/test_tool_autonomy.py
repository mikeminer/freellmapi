import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
from freellmapi import Agent,TOOLS
from loop_guard import LoopGuard,LoopDetected
from tool_routing import suggested,simple_native_request
import advanced_tools


class AutonomyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.agent=Agent(self.root,'http://localhost','test','auto')
        self.agent.session=self.root/'s.json'; self.agent.mode='auto'; self.agent.context_limit=131072

    def test_all_native_tools_discoverable_and_schemas_valid(self):
        from jsonschema import Draft202012Validator
        names=[tool['function']['name'] for tool in TOOLS]
        self.assertEqual(len(set(names)),len(names))
        for group in advanced_tools.GROUPS: self.agent.tool('tools_enable',{'group':group})
        self.assertEqual(set(names),{tool['function']['name'] for tool in self.agent.tool_definitions()})
        for tool in TOOLS: Draft202012Validator.check_schema(tool['function']['parameters'])

    def test_all_tools_retrievable_by_description_not_command(self):
        for tool in TOOLS:
            function=tool['function']
            result=json.loads(self.agent.tool('tool_search',{'query':function['description']}))
            self.assertIn(function['name'],{row['name'] for row in result['loaded']},function['name'])

    def test_italian_search_does_not_require_tool_names(self):
        for text,expected in [('leggere un quaderno','notebook_read'),('errori di tipo','lsp_query'),('attività persistenti','task_create'),('ripristina il checkpoint','file_rewind')]:
            result=json.loads(self.agent.tool('tool_search',{'query':text}))
            self.assertIn(expected,{row['name'] for row in result['loaded']},text)

    def test_native_requests_skip_skill_search_but_substantial_builds_do_not(self):
        self.assertTrue(simple_native_request('Leggi fattura.pdf e dimmi il totale'))
        self.assertTrue(simple_native_request('Il test fallisce: correggi il programma'))
        self.assertFalse(simple_native_request('Crea un gioco 3D per Seeker'))
        self.assertFalse(simple_native_request('Crea una app Android'))
        self.assertFalse(simple_native_request('Installa una skill per database'))

    def test_repeat_reads_warn_then_stop(self):
        guard=LoopGuard()
        self.assertIsNone(guard.observe('read_file',{'path':'a'},'same'))
        guard.observe('read_file',{'path':'a'},'same')
        self.assertIn('Nessun progresso',guard.observe('read_file',{'path':'a'},'same'))
        guard.observe('read_file',{'path':'a'},'same')
        with self.assertRaises(LoopDetected): guard.observe('read_file',{'path':'a'},'same')

    def test_genuine_changed_results_are_progress(self):
        guard=LoopGuard()
        for i in range(20): self.assertIsNone(guard.observe('read_file',{'path':'a'},str(i)))

    def test_varying_bad_ids_cannot_evade_error_guard(self):
        guard=LoopGuard()
        for i in range(3): guard.observe('task_get',{'id':str(i)},'Errore: ID sconosciuto',failed=True)
        with self.assertRaises(LoopDetected): guard.observe('task_get',{'id':'changed'},'Errore: ID sconosciuto',failed=True)

    def test_waiting_for_real_process_is_not_flagged_as_read_loop(self):
        guard=LoopGuard()
        for _ in range(20): self.assertIsNone(guard.observe('process_read',{'id':'active'},'{"state":"running"}'))

    def test_real_turn_stops_alternating_reads_with_new_call_ids(self):
        (self.root/'a').write_text('A'); (self.root/'b').write_text('B')
        counter=[0]
        def response(*args):
            counter[0]+=1
            return {'choices':[{'message':{'role':'assistant','tool_calls':[{'id':str(counter[0]),'function':{'name':'read_file','arguments':json.dumps({'path':'a' if counter[0]%2 else 'b'})}}]}}]}
        with patch.object(self.agent,'api',side_effect=response),patch('freellmapi.output'):
            self.agent.turn('Leggi i file')
        self.assertEqual(counter[0],9); self.assertEqual(self.agent.turn_outcome,'loop_detected')
        self.assertEqual(sum(m['role']=='tool' for m in self.agent.messages),9)

    def test_completed_process_or_question_has_own_controls(self):
        guard=LoopGuard()
        for _ in range(8): guard.observe('ask_user',{'question':'same'},'same')

    def test_notebook_source_echo_reveals_double_serialization(self):
        path=self.root/'n.ipynb'; path.write_text(json.dumps({'nbformat':4,'cells':[{'cell_type':'code','source':['1+1'],'outputs':[],'execution_count':None}]}))
        result=json.loads(self.agent.tool('notebook_edit',{'path':'n.ipynb','index':0,'action':'replace','source':'["1+2"]'}))
        self.assertEqual(result['source'],['["1+2"]']); self.assertIn('warning',result)

    def test_retry_stops_when_request_deadline_is_exhausted(self):
        self.agent.request_timeout=.01
        clock=[100.0]
        def failed(*args,**kwargs):
            clock[0]+=.02; raise TimeoutError('upstream silent')
        # Real 10ms scheduling jitter could expire before the first mock open,
        # testing OS load rather than retry-after-deadline behavior.
        with patch('freellmapi.time.monotonic',side_effect=lambda:clock[0]),patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect=failed
            with self.assertRaises(TimeoutError): self.agent._api('chat/completions',{'model':'auto'})
            self.assertEqual(opener.return_value.open.call_count,1)

    def test_transient_auto_fallback_preserves_completed_tools(self):
        self.agent.last_successful_model='known-good'
        payload={'model':'auto','messages':[{'role':'tool','tool_call_id':'old','content':'already written'}]}
        result={'choices':[{'message':{'role':'assistant','content':'done'}}]}
        with patch.object(self.agent,'api',side_effect=[TimeoutError('upstream silent'),result]) as api,patch.object(self.agent,'tool') as tool:
            self.assertEqual(self.agent.request_turn(payload),result)
        self.assertEqual(api.call_count,2); tool.assert_not_called()
        retry=api.call_args_list[1].args[1]
        self.assertEqual(retry['model'],'known-good'); self.assertEqual(retry['messages'],payload['messages'])
        self.assertEqual(payload['model'],'auto')

    def test_explicit_model_is_never_silently_switched(self):
        self.agent.last_successful_model='other'
        with patch.object(self.agent,'api',side_effect=TimeoutError('offline')) as api:
            with self.assertRaises(TimeoutError): self.agent._request_attempt({'model':'chosen','messages':[]})
        self.assertEqual(api.call_count,1)

    def test_running_lsp_is_not_restarted_by_guessed_command(self):
        self.agent.language_server.process=type('P',(),{'poll':lambda self:None})()
        with patch.object(self.agent.language_server,'start') as start:
            result=json.loads(self.agent.tool('lsp_start',{'command':'does-not-exist'}))
        self.assertTrue(result['already_running']); start.assert_not_called()

    def test_pending_diagnostics_cannot_be_reported_as_clean(self):
        def reply(content=None,calls=None): return {'choices':[{'message':{'role':'assistant','content':content,**({'tool_calls':calls} if calls else {})}}]}
        call={'id':'diag','function':{'name':'lsp_query','arguments':json.dumps({'path':'app.py','action':'diagnostics'})}}
        responses=[reply(calls=[call]),reply('Nessun errore di tipo.'),reply('Il file non ha errori.')]
        with patch.object(self.agent,'api',side_effect=responses),patch.object(self.agent,'tool',return_value=json.dumps({'status':'pending'})),patch('freellmapi.output') as output:
            self.agent.turn('Controlla errori di tipo')
        self.assertEqual(self.agent.turn_outcome,'incomplete')
        self.assertFalse(any('Nessun errore di tipo.' in str(item) for item in output.call_args_list))
        self.assertFalse(any('Il file non ha errori.' in str(item) for item in output.call_args_list))

    def test_lsp_failures_not_exempt_from_loop_control(self):
        guard=LoopGuard()
        for _ in range(3): guard.observe('lsp_query',{'path':'absent'},'Errore: file non disponibile',True)
        with self.assertRaises(LoopDetected): guard.observe('lsp_query',{'path':'absent'},'Errore: file non disponibile',True)

    def test_local_browser_uri_with_spaces(self):
        from desktop_actions import target_uri
        path=self.root/'pagina con spazi.html'; path.write_text('<p>test</p>')
        self.assertEqual(target_uri(path.as_uri(),self.root),path.as_uri())
        with self.assertRaises(ValueError): target_uri('file://remote/share/page.html',self.root)

    def test_connected_mcp_without_manifest_is_visible(self):
        from connections import inventory
        client=Mock(); client.process.poll.return_value=None
        self.agent.extensions.clients['live']=client
        rows=inventory(self.agent,self.root)['servers']
        self.assertEqual(rows[0]['name'],'live'); self.assertTrue(rows[0]['process_running'])
        self.assertFalse(rows[0]['configured'])

    def test_final_answer_collects_undelivered_agents(self):
        jobs=Mock(); jobs.jobs={'abc':{'state':'completed'}}; jobs.delivered=set()
        jobs.result.side_effect=lambda ident,compact: (jobs.delivered.add(ident) or 'RESULT-628')
        self.agent.jobs=jobs
        def reply(content): return {'choices':[{'message':{'role':'assistant','content':content}}]}
        with patch.object(self.agent,'api',side_effect=[reply('Finito senza risultati'),reply('RESULT-628')]),patch('freellmapi.output') as output:
            self.agent.turn('Raccogli i risultati')
        self.assertEqual(self.agent.turn_outcome,'completed')
        self.assertTrue(any(m.get('role')=='tool' and m.get('content')=='RESULT-628' for m in self.agent.messages))
        self.assertFalse(any('Finito senza risultati' in str(c) for c in output.call_args_list))


if __name__=='__main__': unittest.main()
