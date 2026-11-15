import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from autonomy import Budget,BudgetExceeded
from freellmapi import Agent
import advanced_tools
import connections
import market_data
import workspaces


class AdvancedTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.agent=Agent(self.root,'http://localhost','test','auto'); self.agent.session=self.root/'s.json'
        self.agent.context_limit=131072
        self.addCleanup(self.agent.debugger.close)

    def test_shared_budget_counts_usage_and_stops(self):
        budget=Budget(tokens=20,steps=10)
        budget.request(); budget.account({'usage':{'total_tokens':20}})
        with self.assertRaises(BudgetExceeded): budget.request()
        self.assertEqual(budget.steps,1)

    def test_unknown_usage_not_invented(self):
        budget=Budget(); budget.account({})
        self.assertEqual(budget.tokens,0); self.assertEqual(budget.unknown_usage,1)

    def test_repeated_mutation_stops(self):
        budget=Budget()
        for _ in range(3): budget.action('shell',{'command':'repeat'})
        with self.assertRaises(BudgetExceeded): budget.action('shell',{'command':'repeat'})
        for _ in range(20): budget.action('read_file',{'path':'x'})

    def test_tool_discovery(self):
        self.assertNotIn('debug_start',[t['function']['name'] for t in self.agent.tool_definitions()])
        self.agent.tool('tools_enable',{'group':'debug'})
        self.assertIn('debug_start',[t['function']['name'] for t in self.agent.tool_definitions()])

    def test_stop_budget_keeps_tool_result_and_no_repeat(self):
        self.agent.mode='auto'; self.agent.budget_settings['steps']=1
        response={'choices':[{'message':{'role':'assistant','tool_calls':[{'id':'x','function':{'name':'list_files','arguments':'{}'}}]}}],'usage':{'total_tokens':10}}
        with patch.object(self.agent,'_api',return_value=response) as api,patch('freellmapi.output'):
            self.agent.turn('list')
        self.assertEqual(api.call_count,1)
        saved=json.loads(self.agent.session.read_text())
        self.assertTrue(any(m.get('tool_call_id')=='x' for m in saved))

    def test_vision_payload_contains_pixels_not_saved_base64(self):
        from PIL import Image
        from vision_input import inject
        path=self.root/'picture.png'; Image.new('RGB',(20,20),'red').save(path)
        payload={'messages':[{'role':'user','content':'inspect'}]}
        result=inject(payload,[path])
        self.assertEqual(len(payload['messages']),1)
        self.assertTrue(result['messages'][-1]['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,'))
        self.agent.mode='auto'
        self.agent.tool('image_read',{'path':str(path)})
        reply={'choices':[{'message':{'role':'assistant','content':'Image received'}}]}
        with patch.object(self.agent,'api',return_value=reply) as api,patch('freellmapi.output'):
            self.agent.turn('describe the image')
        self.assertIn('image_url',str(api.call_args.args[1]))
        self.assertNotIn('base64',self.agent.session.read_text())
        self.assertEqual(self.agent.pending_images,[])

    def test_plan_denies_new_mutation_and_image_disclosure(self):
        self.agent.mode='plan'
        with patch.object(self.agent.debugger,'start') as start:
            result=json.loads(self.agent.tool('debug_start',{'path':'game.js'}))
        self.assertTrue(result['denied']); start.assert_not_called()

    def test_market_exact_pair_and_closed_candles(self):
        candles=[[i*1000,'1','2','1',str(i+1),'3',i*1000+999,'4'] for i in range(21)]
        with patch('market_data.get',side_effect=[{'symbols':[{'symbol':'PUMPUSDC','baseAsset':'PUMP','quoteAsset':'USDC','status':'TRADING'}]}, {},candles,{},20000]) as get:
            # Match the server's dictionary shape, including an unclosed final candle.
            get.side_effect=[{'symbols':[{'symbol':'PUMPUSDC','baseAsset':'PUMP','quoteAsset':'USDC','status':'TRADING'}]}, {},candles,{}, {'serverTime':20000}]
            result=market_data.snapshot('PUMP/USDC')
        self.assertEqual(get.call_args_list[0].args[1]['symbol'],'PUMPUSDC')
        self.assertFalse(result['candles'][-1]['closed'])
        self.assertEqual(result['indicators']['sma20_closed'],10.5)

    def test_invalid_pair_never_falls_back(self):
        with patch('market_data.get',return_value={'symbols':[]}) as get:
            with self.assertRaises(ValueError): market_data.snapshot('MISSING/USDC')
        self.assertEqual(get.call_count,1)

    def test_connector_import_references_no_secret(self):
        config={'mcpServers':{'test':{'command':'node','env':{'API_KEY':'${TEST_FREELLMAPI_KEY}'}}}}
        connections.validate(config)
        with patch.dict(os.environ,{'TEST_FREELLMAPI_KEY':'not-printed'}):
            self.assertEqual(connections.resolve_env(config['mcpServers']['test'])['env']['API_KEY'],'not-printed')
        config['mcpServers']['test']['env']['API_KEY']='literal-secret'
        with self.assertRaises(ValueError): connections.validate(config)

    def test_sqlite_read_only(self):
        path=self.root/'db.sqlite'
        with sqlite3.connect(path) as db:
            db.execute('create table items(value text)'); db.execute("insert into items values ('test')")
        db.close()
        self.assertEqual(connections.query(path,'select * from items')['rows'],[('test',)])
        with self.assertRaises(sqlite3.DatabaseError): connections.query(path,'delete from items')
        outside=self.root/'created.db'
        with self.assertRaises(sqlite3.DatabaseError): connections.query(path,"ATTACH DATABASE '"+outside.as_posix()+"' AS other")
        self.assertFalse(outside.exists())

    def test_desktop_stale_ref_rejected_without_input(self):
        with self.assertRaises(ValueError): self.agent.desktop.click('stale',1,1)

    def test_visual_probe_accepts_explanation_not_wrong_or_partial_code(self):
        from model_probe import recognizes
        self.assertTrue(recognizes('The image says "42B0EE".','42B0EE'))
        self.assertFalse(recognizes('42BOEE','42B0EE'))
        self.assertFalse(recognizes('142B0EE0','42B0EE'))

    def test_known_failed_vision_model_does_not_send_image(self):
        self.agent.model='no-vision'
        self.agent.model_observations['no-vision']={'vision_probe_passed':False}
        with self.assertRaisesRegex(ValueError,'prova visiva'): self.agent.tool('image_read',{'path':'x.png'})
        self.assertFalse(self.agent.pending_images)

    def test_compaction_preserves_live_instruction_and_latest_tool_pairs(self):
        prompt={'role':'user','content':'Read once, do not repeat'}
        rounds=[]
        for i in range(4):
            rounds.extend([{'role':'assistant','tool_calls':[{'id':str(i)}]},
                           {'role':'tool','tool_call_id':str(i),'content':'evidence '+str(i)}])
        self.agent.messages=[prompt]+rounds
        with patch.object(self.agent,'api',return_value={'choices':[{'message':{'content':'past observations'}}]}): self.agent.compact(True)
        self.assertEqual(self.agent.messages[1],prompt)
        self.assertEqual(self.agent.messages[2:],rounds[-4:])

    def test_compaction_does_not_erase_first_tool_result(self):
        original=[{'role':'user','content':'Read once'}, {'role':'assistant','tool_calls':[{'id':'x'}]},
                  {'role':'tool','tool_call_id':'x','content':'data'*10000}]
        self.agent.messages=list(original)
        with patch.object(self.agent,'api') as api: self.agent.compact(True)
        api.assert_not_called(); self.assertEqual(self.agent.messages,original)

    @unittest.skipUnless(shutil.which('node'),'Node required')
    def test_real_node_breakpoint_variables_step(self):
        path=self.root/'debug.js'
        path.write_text('let count = 1;\ncount += 2;\nconsole.log(count);\n',encoding='utf-8')
        debugger=self.agent.debugger
        state=debugger.start(path)
        self.assertIsNotNone(state['paused'])
        debugger.breakpoint(path,2)
        state=debugger.move('continue')
        self.assertIsNotNone(state['paused'])
        self.assertEqual(state['paused']['callFrames'][0]['location']['lineNumber'],1)
        self.assertIn('count',json.dumps(debugger.variables()))
        state=debugger.move('step_over')
        variables=debugger.variables()
        values=[p['value'].get('value') for s in variables for p in s['properties'] if p['name']=='count']
        self.assertEqual(values,[3])

    @unittest.skipUnless(shutil.which('git'),'Git required')
    def test_real_worktree_checkpoint_revert(self):
        root=self.root/'repo'; root.mkdir()
        workspaces.git(root,'init'); workspaces.git(root,'config','user.name','Test'); workspaces.git(root,'config','user.email','test@example.invalid')
        (root/'file.txt').write_text('before')
        workspaces.git(root,'add','file.txt'); workspaces.git(root,'commit','-m','initial')
        worktree=workspaces.create(root,self.root/'home')
        self.assertEqual((Path(worktree['path'])/'file.txt').read_text(),'before')
        (root/'file.txt').write_text('after')
        commit=workspaces.checkpoint(root,'change')['commit']
        workspaces.revert(root,commit)
        self.assertEqual((root/'file.txt').read_text(),'before')
        self.assertEqual((Path(worktree['path'])/'file.txt').read_text(),'before')


if __name__=='__main__': unittest.main()
