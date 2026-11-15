"""Regression cases from the Seeker session; synthetic home, no exchange writes."""
import io
import json
import sqlite3
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

from background_poll import BackgroundPoll
from loop_guard import LoopGuard,LoopDetected
import hyperliquid_tools as hl


class SessionRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.home=Path(temp.name)
        hl.configure(self.home,'mainnet-master','mainnet','0x'+'1'*40)
        self.agent=SimpleNamespace(mode='auto',depth=0,interactive=False,session=self.home/'session.json',
                                  messages=[{'role':'user','content':'Trova BTC/USDC e usa leva 40 con 400 usdc'}])

    def test_missing_profile_lists_existing_names_instead_of_requiring_new_login(self):
        result=hl.dispatch(self.agent,'hyperliquid_select',{'profile':'mainnet'},self.home)
        self.assertEqual(result['code'],'profile_missing')
        self.assertEqual(result['available_profiles'],['mainnet-master'])
        self.assertEqual(result['default_profile'],'mainnet-master')
        self.assertEqual(result['recovery_tools'][0]['name'],'hyperliquid_status')
        self.assertFalse(result['sent'])

    def test_usdc_budget_cannot_be_interpreted_as_400_bitcoin(self):
        args={'action':'order','coin':'BTC','side':'buy','size':'400','order_kind':'market'}
        with patch.object(hl,'info',side_effect=AssertionError('No exchange traffic needed')):
            result=hl.dispatch(self.agent,'hyperliquid_preview',args,self.home)
        self.assertEqual(result['code'],'size_unit_mismatch')
        self.assertEqual(result['recovery_tools'][0]['name'],'ask_user')
        self.assertFalse((self.home/'hyperliquid/operations.db').exists())

    def test_order_does_not_silently_ignore_a_leverage_change(self):
        with patch.object(hl,'permissions'),patch.object(hl,'market',return_value=({'name':'BTC','szDecimals':5},'')):
            result=hl.dispatch(self.agent,'hyperliquid_preview',{'action':'order','coin':'BTC','size':'.005','side':'buy','order_kind':'market','leverage':40},self.home)
        self.assertEqual(result['code'],'leverage_action_required')
        self.assertEqual(result['recovery_tools'][0]['arguments']['resource'],'snapshot')
        self.assertFalse((self.home/'hyperliquid/operations.db').exists())

    def test_financial_retry_requires_the_specific_read_and_stays_bounded(self):
        guard=LoopGuard()
        error={'ok':False,'code':'bracket_direction','recovery_tools':[{'name':'hyperliquid_read','arguments':{'resource':'snapshot','profile':'mainnet-master','coin':'BTC'}}]}
        guard.observe('hyperliquid_preview',{},json.dumps(error))
        self.assertEqual(json.loads(guard.before('hyperliquid_preview',{}))['code'],'financial_prerequisite_required')
        guard.observe('hyperliquid_read',{'resource':'snapshot','profile':'mainnet-master','coin':'ETH'},json.dumps({'data':{}}))
        self.assertIsNotNone(guard.before('hyperliquid_preview',{}))
        guard.observe('hyperliquid_read',{'resource':'snapshot','profile':'mainnet-master','coin':'BTC'},json.dumps({'data':{}}))
        self.assertIsNone(guard.before('hyperliquid_preview',{}))
        self.assertEqual(guard.global_failures,1)
        for i in range(2):guard.observe('hyperliquid_preview',{'changed':i},json.dumps(error))
        with self.assertRaises(LoopDetected):
            for i in range(10):guard.observe('hyperliquid_preview',{'changed':i},json.dumps(error))

    def test_user_choice_is_required_for_ambiguous_units(self):
        guard=LoopGuard()
        guard.observe('hyperliquid_preview',{},json.dumps({'ok':False,'recovery_tools':[{'name':'ask_user','arguments':{'question':'Margin or notional?'}}]}))
        guard.observe('ask_user',{},json.dumps({'cancelled':True}))
        self.assertIsNotNone(guard.before('hyperliquid_preview',{}))
        guard.observe('ask_user',{},json.dumps({'answer':'Notional','cancelled':False}))
        self.assertIsNone(guard.before('hyperliquid_preview',{}))

    def test_default_profile_read_uses_effective_native_profile_for_recovery(self):
        guard=LoopGuard()
        error={'ok':False,'recovery_tools':[{'name':'hyperliquid_read','arguments':{'resource':'snapshot','profile':'mainnet-master','coin':'BTC'}}]}
        guard.observe('hyperliquid_preview',{},json.dumps(error))
        args={'resource':'snapshot','coin':'BTC'}
        guard.observe('hyperliquid_read',args,json.dumps({'profile':'different-profile','data':{}}))
        self.assertIsNotNone(guard.before('hyperliquid_preview',{}))
        guard.observe('hyperliquid_read',args,json.dumps({'profile':'mainnet-master','data':{}}))
        self.assertIsNone(guard.before('hyperliquid_preview',{}))

    def test_background_database_failure_does_not_spin_or_block_foreground(self):
        output=[];poll=BackgroundPoll(self.home,output.append)
        broken=Mock(side_effect=sqlite3.OperationalError('unable to open database file'))
        for _ in range(20):self.assertIsNone(poll.run('social','same',broken))
        self.assertEqual(broken.call_count,1);self.assertEqual(len(output),1)
        self.assertEqual(poll.run('unrelated','same',lambda:'/status'),'/status')
        row=json.loads((self.home/'diagnostics/runtime-errors.jsonl').read_text(encoding='utf-8'))
        self.assertEqual(row['source'],'background:social');self.assertTrue(row['frames'])

    def test_financial_background_fault_never_retries_a_cycle_automatically(self):
        poll=BackgroundPoll(self.home,lambda _:None)
        broken=Mock(side_effect=sqlite3.OperationalError('unable to open database file'))
        poll.run('hyperliquid','session',broken,critical=True)
        with patch('background_poll.time.monotonic',return_value=10**20):
            poll.run('hyperliquid','session',broken,critical=True)
        self.assertEqual(broken.call_count,1)

    def test_idle_poll_does_not_reopen_databases_on_each_remote_tick(self):
        poll=BackgroundPoll(self.home,lambda _:None);callback=Mock(return_value=None)
        for _ in range(30):poll.run('social','same',callback)
        self.assertEqual(callback.call_count,1)
        with patch('background_poll.time.monotonic',return_value=poll.retry[('social','same')]+.1):
            poll.run('social','same',callback)
        self.assertEqual(callback.call_count,2)

    def test_remote_main_loop_keeps_accepting_messages_after_sqlite_failure(self):
        import freellmapi
        (self.home/'config.json').write_text(json.dumps({'base_url':'http://127.0.0.1:9','model':'fixture','auto_skills':False}),encoding='utf-8')
        remote=Mock(busy=False,cancel_requested=False)
        remote.pop.side_effect=[None,'/status',EOFError()]
        with patch.object(freellmapi,'HOME',self.home),patch.object(sys,'argv',['freellmapi','--remote-only','--new','--model','fixture']), \
             patch.dict(os.environ,{'FREELLMAPI_SESSION_TOKEN':'synthetic-test-only'}), \
             patch.object(freellmapi.Agent,'api',return_value={'data':[{'id':'fixture'}]}), \
             patch.object(freellmapi.Seeker,'start'),patch('remote_access.active',return_value=remote), \
             patch('remote_access.command',return_value=False), \
             patch.object(hl,'poll',side_effect=sqlite3.OperationalError('unable to open database file')) as poll, \
             patch('social_tools.poll',return_value=None),patch.object(freellmapi,'output') as output:
            freellmapi.main()
        self.assertEqual(poll.call_count,1)
        self.assertTrue(any('fixture | ask' in str(call) for call in output.call_args_list))
        remote.complete.assert_any_call('completed')

    def test_finished_remote_request_keeps_the_actual_failed_outcome(self):
        from remote_access import Remote
        remote=Remote(self.home/'bridge',lambda:{})
        remote.url='http://127.0.0.1:31418'
        pair=remote.pair();login=remote.pair_device({'code':pair['code']})
        remote.enqueue(login['device'],{'request_id':'synthetic-request-01','text':'fixture'})
        self.assertEqual(remote.pop(),'fixture');remote.complete('loop_detected')
        row=next(iter(remote.requests.values()))
        self.assertEqual(row['state'],'finished');self.assertEqual(row['outcome'],'loop_detected')

    def test_boolean_schema_rejects_string_without_executing_tool(self):
        import freellmapi,reliable_runtime
        agent=Mock(session=self.home/'s.json')
        with self.assertRaisesRegex(ValueError,'reduce_only'):
            reliable_runtime.execute(agent,'synthetic-bad-boolean','hyperliquid_preview',{'action':'order','reduce_only':'false'},freellmapi.TOOLS)
        agent.tool.assert_not_called()

    def test_privacy_filter_keeps_public_status_json_valid(self):
        from secret_output import redact
        original={'profiles':[{'name':'mainnet-master'}],'scope':'No private key is sent to models.',
                  'writes':'read-only until local login; per-action review required.'}
        self.assertEqual(json.loads(redact(json.dumps(original))),original)
        original['private_keys']='synthetic-private-value'
        clean=json.loads(redact(json.dumps(original)))
        self.assertEqual(clean['private_keys'],'[REDACTED]')
        self.assertEqual(clean['profiles'],original['profiles'])
        self.assertNotIn('synthetic-private-value',json.dumps(clean))

    def test_privacy_filter_scrubs_nested_credentials_without_losing_recovery_fields(self):
        from secret_output import redact
        value={'api_key':'sk-'+'a'*30,'nested':{'authorization':'Bearer '+'b'*40},'bytes':list(range(64)),
               'recovery_tools':[{'name':'hyperliquid_status','arguments':{}}]}
        clean=json.loads(redact(json.dumps(value)))
        self.assertEqual(clean['recovery_tools'],value['recovery_tools'])
        self.assertNotIn('a'*30,json.dumps(clean));self.assertNotIn('b'*40,json.dumps(clean))
        self.assertIsInstance(clean['bytes'],str)

    def test_public_transaction_hash_remains_usable_but_labelled_private_key_is_removed(self):
        from secret_output import redact
        public='0x'+'a'*64;private='0x'+'b'*64
        clean=json.loads(redact(json.dumps({'transaction_hash':public,'private_key':private})))
        self.assertEqual(clean['transaction_hash'],public);self.assertNotIn(private,json.dumps(clean))

    def test_structured_preview_error_is_displayed_as_failure(self):
        import terminal_ui
        with patch.object(terminal_ui,'fancy',return_value=False),redirect_stdout(io.StringIO()) as output:
            terminal_ui.result('hyperliquid_preview','{"ok":false,"code":"bracket_direction","sent":false}',.1)
        self.assertIn('! hyperliquid_preview',output.getvalue())

    def test_scripted_model_recovers_prerequisites_and_creates_only_unsigned_preview(self):
        import freellmapi
        from session_store import Sessions
        root=self.home/'project';root.mkdir()
        with patch.object(freellmapi,'HOME',self.home):
            agent=freellmapi.Agent(root,'http://127.0.0.1:9','synthetic-token','auto')
            store=Sessions(self.home);store.activate(agent,store.create(root));agent.auto_skills=False;agent.mode='auto';agent.context_limit=200000
            calls=[
                ('hyperliquid_preview',{'profile':'mainnet','action':'order','coin':'BTC','size':'400','side':'buy','order_kind':'market','reduce_only':'false'}),
                ('hyperliquid_preview',{'profile':'mainnet','action':'order','coin':'BTC','size':'400','side':'buy','order_kind':'market','reduce_only':False}),
                ('hyperliquid_status',{}),
                ('hyperliquid_preview',{'profile':'mainnet-master','action':'order','coin':'BTC','size':'400','side':'buy','order_kind':'market'}),
                ('ask_user',{'question':'I 400 USDC sono margine o valore totale della posizione?','options':['Margine','Valore totale']}),
                ('hyperliquid_read',{'resource':'snapshot','profile':'mainnet-master','coin':'BTC'}),
                ('hyperliquid_analyze',{'profile':'mainnet-master','coin':'BTC'}),
                ('hyperliquid_preview',{'profile':'mainnet-master','action':'order','coin':'BTC','size':'.005','side':'buy','order_kind':'market','slippage_bps':50,'take_profit':'70000','stop_loss':'67000','bracket_slippage_bps':50})]
            responses=[]
            for i,(name,args) in enumerate(calls):responses.append({'model':'scripted-fixture','choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'call'+str(i),'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]},'finish_reason':'tool_calls'}]})
            responses.append({'model':'scripted-fixture','choices':[{'message':{'role':'assistant','content':'Solo anteprima di prova: nessun ordine inviato.'},'finish_reason':'stop'}]})
            with patch.object(agent,'api',side_effect=responses) as model,patch.object(hl,'permissions'), \
                 patch('hyperliquid_analysis.analyze',return_value={'profile':'mainnet-master','network':'mainnet','coin':'BTC','as_of_ms':int(__import__('time').time()*1000),'data_ready':True,'evidence_id':'synthetic-analysis'}), \
                 patch.object(hl,'market',return_value=({'name':'BTC','szDecimals':5},'')), \
                 patch.object(hl,'info',return_value={'BTC':'68000'}), \
                 patch.object(hl,'read',return_value={'data':{'mid':'68000'},'resource':'snapshot'}), \
                 patch.object(freellmapi,'ask_question',return_value={'answer':'Valore totale','cancelled':False}), \
                 patch.object(hl,'submit',side_effect=AssertionError('No financial sends')), \
                 patch.object(freellmapi,'output'),redirect_stdout(io.StringIO()):
                try:agent.turn('Trova un segnale BTC/USDC con leva 40 e 400 usdc')
                except StopIteration:
                    raise AssertionError(json.dumps(agent.messages[-8:],ensure_ascii=False)[:6000])
            self.assertEqual(agent.turn_outcome,'completed',json.dumps([m.get('content') for m in agent.messages if m.get('role')=='tool'],ensure_ascii=False)[:4500])
            self.assertEqual(model.call_count,9)
            with hl.database(self.home) as db:
                rows=db.execute('SELECT state,body FROM actions').fetchall()
            self.assertEqual(len(rows),1);self.assertEqual(rows[0][0],'prepared')
            self.assertEqual(json.loads(rows[0][1])['size'],'0.005')
            self.assertTrue(any('size_unit_mismatch' in str(m.get('content','')) for m in agent.messages))
            self.assertEqual(json.loads(agent.session.read_text(encoding='utf-8')),agent.messages)


if __name__=='__main__':unittest.main()
