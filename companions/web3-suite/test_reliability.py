import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import coding_tools
import reliable_runtime as rt
from freellmapi import Agent,TOOLS
from runtime_policy import Policy
from language_server import LanguageServer


class ReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); self.agent=Agent(self.root,'http://localhost','test','auto')
        self.agent.session=self.root/'session.json'; self.agent.mode='auto'; self.agent.context_limit=131072

    def test_journal_replays_result_without_side_effect(self):
        with patch.object(self.agent,'tool',return_value='saved') as tool:
            one=rt.execute(self.agent,'id','write_file',{'path':'x','content':'y'},TOOLS)
            two=rt.execute(self.agent,'id','write_file',{'path':'x','content':'y'},TOOLS)
        self.assertEqual(one,two); tool.assert_called_once()
        with self.assertRaisesRegex(ValueError,'conflict'): rt.execute(self.agent,'id','write_file',{'path':'x','content':'other'},TOOLS)

    def test_uncertain_action_is_not_replayed(self):
        with patch.object(self.agent,'tool',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt): rt.execute(self.agent,'id','shell',{'command':'echo run'},TOOLS)
        with patch.object(self.agent,'tool') as tool:
            with self.assertRaisesRegex(ValueError,'execution_uncertain'): rt.execute(self.agent,'id','shell',{'command':'echo run'},TOOLS)
            tool.assert_not_called()

    def test_schema_invalid_args_never_reach_tool(self):
        with patch.object(self.agent,'tool') as tool:
            with self.assertRaisesRegex(ValueError,'invalid_arguments'): rt.execute(self.agent,'id','write_file',{'path':12,'content':'x'},TOOLS)
            tool.assert_not_called()

    def test_duplicate_batch_ids_rejected_before_any_tool(self):
        call={'id':'same','function':{'name':'shell','arguments':'{}'}}
        with self.assertRaisesRegex(ValueError,'duplicato'): rt.validate_message({'role':'assistant','tool_calls':[call,call]})

    def test_scheduler_claims_once_and_retains_restart_state(self):
        from session_schedule import Schedule
        scheduler=Schedule(self.agent.session)
        with patch('session_schedule.time.time',return_value=100): row=scheduler.create('read status',delay=1)
        with patch('session_schedule.time.time',return_value=102):
            self.assertEqual(scheduler.poll(),'/_scheduled '+row['id'])
            self.assertIsNone(Schedule(self.agent.session).poll())
        scheduler.finish(row['id'],False)
        self.assertFalse(scheduler.read()[row['id']]['enabled'])

    def test_scheduler_coalesces_missed_intervals(self):
        from session_schedule import Schedule
        scheduler=Schedule(self.agent.session)
        with patch('session_schedule.time.time',return_value=100): row=scheduler.create('read',1,60)
        with patch('session_schedule.time.time',return_value=1000):
            self.assertIsNotNone(scheduler.poll()); self.assertIsNone(scheduler.poll())
        self.assertEqual(scheduler.read()[row['id']]['next_due'],1060)

    def test_pdf_text_and_image(self):
        import pymupdf
        path=self.root/'doc.pdf'
        with pymupdf.open() as document:
            page=document.new_page(); page.insert_text((50,50),'Verified PDF'); document.save(path)
        result=json.loads(self.agent.tool('pdf_read',{'path':'doc.pdf','render':True}))
        self.assertIn('Verified PDF',result['pages'][0]['text'])
        self.assertTrue(Path(result['pages'][0]['image_path']).is_file())

    def test_tool_search_loads_requested_schema(self):
        result=json.loads(self.agent.tool('tool_search',{'query':'notebook_edit'}))
        self.assertIn('notebook_edit',[row['name'] for row in result['loaded']])
        self.assertIn('notebook_edit',[row['function']['name'] for row in self.agent.tool_definitions()])

    def test_scoped_project_instructions(self):
        folder=self.root/'sub'; folder.mkdir(); (folder/'AGENTS.md').write_text('Use project conventions.')
        (folder/'code.py').write_text('value=1')
        self.assertIn('Use project conventions',self.agent.tool('read_file',{'path':'sub/code.py'}))

    def test_lsp_windows_uri_normalization(self):
        server=LanguageServer(); path=self.root/'x.py'; path.write_text('x=1')
        server.root=self.root; server.process=type('Process',(),{'poll':lambda self:None})()
        from urllib.parse import quote
        uri=path.as_uri(); server.diagnostics[uri.replace('C:','c%3A').replace('c:','c%3A')]={'version':1,'diagnostics':[{'message':'test'}]}
        with patch.object(server,'notify'): result=server.query(path,'diagnostics')
        self.assertIsNotNone(result['latest_published'])

    def test_output_spool_is_readable(self):
        with patch.object(self.agent,'tool',return_value='α'*40000): result=json.loads(rt.execute(self.agent,'x','list_files',{},TOOLS))
        self.assertTrue(result['truncated'])
        self.assertEqual(rt.output_read(self.agent.session,result['output_id'],24000)['text'],'α'*16000)

    def test_changed_file_after_read_rejected(self):
        path=self.root/'x.txt'; path.write_text('before')
        self.agent.tool('read_file',{'path':'x.txt'}); path.write_text('external')
        with self.assertRaisesRegex(ValueError,'file_changed'): self.agent.tool('edit_file',{'path':'x.txt','old':'before','new':'after'})
        self.assertEqual(path.read_text(),'external')

    def test_changed_during_approval_rejected(self):
        path=self.root/'x'; path.write_bytes(b'before')
        def approve(diff): path.write_bytes(b'external'); return True
        with self.assertRaisesRegex(ValueError,'file_changed'): rt.replace_file(path,b'before',b'after',self.agent.session,approve)
        self.assertEqual(path.read_bytes(),b'external')

    def test_bom_crlf_preserved_and_rewind_conflict(self):
        path=self.root/'x'; original=b'\xef\xbb\xbfhello\r\nworld\r\n'; path.write_bytes(original)
        result=json.loads(self.agent.tool('edit_file',{'path':'x','old':'hello\n','new':'changed\n'}))
        self.assertEqual(path.read_bytes(),b'\xef\xbb\xbfchanged\r\nworld\r\n')
        rt.rewind(self.agent.session,result['checkpoint'],self.root,lambda _:True)
        self.assertEqual(path.read_bytes(),original)
        result=rt.replace_file(path,original,b'new',self.agent.session,lambda _:True)
        path.write_bytes(b'external')
        with self.assertRaisesRegex(ValueError,'file_changed'): rt.rewind(self.agent.session,result['checkpoint'],self.root,lambda _:True)

    def test_task_dependencies_persist_and_cycle_rejected(self):
        tasks=coding_tools.Tasks(self.root/'home',self.root)
        one=tasks.apply('task_create',{'title':'one'})['id']; two=tasks.apply('task_create',{'title':'two'})['id']
        tasks.apply('task_update',{'id':two,'status':'pending','dependencies':[one]})
        with self.assertRaises(ValueError): tasks.apply('task_update',{'id':one,'status':'pending','dependencies':[two]})
        with self.assertRaises(ValueError): tasks.apply('task_update',{'id':two,'status':'completed'})
        tasks.apply('task_update',{'id':one,'status':'completed'}); tasks.apply('task_update',{'id':two,'status':'completed'})
        self.assertEqual(len(coding_tools.Tasks(self.root/'home',self.root).apply('task_list',{})),2)

    def test_notebook_edit_clears_stale_execution(self):
        path=self.root/'n.ipynb'
        path.write_text(json.dumps({'nbformat':4,'nbformat_minor':5,'metadata':{},'cells':[{'cell_type':'code','metadata':{},'source':['1+1'],'outputs':[{'text':['2']}],'execution_count':1}]}))
        self.agent.tool('notebook_edit',{'path':'n.ipynb','action':'replace','index':0,'source':'1+2'})
        cell=json.loads(path.read_text())['cells'][0]
        self.assertEqual(cell['outputs'],[]); self.assertIsNone(cell['execution_count'])

    def test_deny_policy_applies_in_auto(self):
        self.agent.policy.config={'deny':['shell'],'hooks':[]}
        with self.assertRaises(PermissionError): self.agent.tool('shell',{'command':'echo no'})

    def test_post_hook_failure_does_not_hide_completed_edit(self):
        with patch.object(self.agent.policy,'run',side_effect=[[],RuntimeError('formatter failed')]):
            result=json.loads(self.agent.tool('write_file',{'path':'x','content':'saved'}))
        self.assertIn('post_hook_error',result); self.assertEqual((self.root/'x').read_text(),'saved')

    def test_pre_hook_blocks_before_side_effect(self):
        policy=self.root/'policy.json'
        policy.write_text(json.dumps({'hooks':[{'event':'pre_tool','tool':'write_file','argv':[sys.executable,'-c','import sys;sys.exit(2)']}]}))
        self.agent.policy.load(policy,lambda _:True)
        with self.assertRaisesRegex(RuntimeError,'hook_failed'): self.agent.tool('write_file',{'path':'x','content':'no'})
        self.assertFalse((self.root/'x').exists())

    def test_first_tool_durable_when_next_tool_crashes(self):
        calls=[{'id':str(i),'type':'function','function':{'name':'list_files','arguments':'{}'}} for i in range(2)]
        response={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':calls}}]}
        class Crash(BaseException): pass
        with patch.object(self.agent,'api',return_value=response),patch.object(self.agent,'tool',side_effect=['first complete',Crash]):
            with self.assertRaises(Crash): self.agent.turn('test crash')
        saved=json.loads(self.agent.session.read_text())
        self.assertEqual(saved[-1]['content'],'first complete')
        self.assertEqual(saved[-1]['tool_call_id'],'0')

    @unittest.skipUnless(os.name=='nt','Windows')
    def test_background_process_real_exit_and_restart_state(self):
        manager=coding_tools.Processes(self.root); self.addCleanup(manager.close)
        row=manager.start('Write-Output verified',self.root,10)
        deadline=time.monotonic()+15
        while manager.live[row['id']][1]['state']=='running' and time.monotonic()<deadline: time.sleep(.1)
        result=manager.read(row['id'])
        self.assertEqual(result['process']['state'],'completed'); self.assertIn('verified',result['output'])
        other=coding_tools.Processes(self.root)
        with self.assertRaises(ValueError): other.stop(row['id'])

    def test_lsp_framing_with_real_local_protocol_fixture(self):
        script=self.root/'server.py'
        script.write_text('''import sys,json
while True:
    headers={}
    while True:
        line=sys.stdin.buffer.readline()
        if not line: sys.exit(0)
        if line==b'\\r\\n': break
        k,v=line.decode().split(':',1); headers[k.lower()]=v.strip()
    message=json.loads(sys.stdin.buffer.read(int(headers['content-length'])))
    if 'id' not in message: continue
    result={'capabilities':{}} if message['method']=='initialize' else {'contents':'hover from fixture'}
    raw=json.dumps({'jsonrpc':'2.0','id':message['id'],'result':result}).encode()
    sys.stdout.buffer.write(('Content-Length: '+str(len(raw))+'\\r\\n\\r\\n').encode()+raw); sys.stdout.buffer.flush()
''')
        source=self.root/'x.py'; source.write_text('value=1')
        server=LanguageServer(); self.addCleanup(server.close)
        server.start(sys.executable,[str(script)],self.root)
        self.assertEqual(server.query(source,'hover')['contents'],'hover from fixture')
        self.assertIsNone(server.query(source,'diagnostics')['latest_published'])


if __name__=='__main__': unittest.main()
