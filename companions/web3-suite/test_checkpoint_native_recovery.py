"""The real mobile failure: provider ignores inspection and sends timestamps."""
import copy
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import freellmapi as f
import hyperliquid_tools as h
from memory_vault import Vault
from session_store import Sessions

STAMP=1794643578060
RISK={'account_mode':'unifiedAccount','collateral_usdc':'22.37870064','available_usd':'22.37870064',
      'positions':[],'orders':[],'dexs_scanned':11}


class NativeCheckpointRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.home=Path(temp.name)
        h.configure(self.home,'mainnet-master','mainnet','0x'+'1'*40)
        with patch.object(f,'HOME',self.home):self.agent=f.Agent(self.home,'http://127.0.0.1:9','synthetic','auto')
        store=Sessions(self.home);store.activate(self.agent,store.create(self.home))
        self.agent.memory=Vault(self.home/'vault',self.home);self.agent.context_limit=200000
        self.agent.auto_skills=False;self.agent.streaming=False;self.agent.mode='auto'
        self.args={'kind':'verified','text':'Saldo osservato 22.37870064 USDC; nessuna posizione o ordine aperto.',
                   'sources':[str(STAMP)]}
        self.index=0

    def reply(self,name=None,args=None,text=None):
        self.index+=1;msg={'role':'assistant','content':text}
        if name:msg['tool_calls']=[{'id':'scripted-'+str(self.index),'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]
        return {'choices':[{'message':msg,'finish_reason':'tool_calls' if name else 'stop'}]}

    def run_turn(self,responses):
        def read(home,args):
            return {'profile':'mainnet-master','network':'mainnet','queried_address':'0x'+'1'*40,
                    'resource':args['resource'],'retrieved_ms':STAMP,'source':'https://api.hyperliquid.xyz/info',
                    'data':copy.deepcopy(RISK) if args['resource']=='risk' else []}
        with patch.object(f,'HOME',self.home),patch.object(self.agent,'api',side_effect=responses) as api, \
             patch.object(h,'read',side_effect=read) as reads,patch.object(h,'submit') as submit, \
             patch.object(f,'output'),redirect_stdout(io.StringIO()):
            self.agent.turn('vedi il saldo e le posizioni attive')
        submit.assert_not_called()
        return api,reads

    def names(self):return {c['id']:c['function']['name'] for m in self.agent.messages for c in m.get('tool_calls',[])}

    def test_mobile_timestamp_loop_is_repaired_by_native_inspection_not_model_choice(self):
        # This model NEVER calls context_inspect, even after the first error.
        responses=[self.reply('context_checkpoint',self.args),self.reply('context_checkpoint',self.args),
                   self.reply(text='Saldo 22.37870064 USDC, nessuna posizione e nessun ordine aperto.')]
        api,reads=self.run_turn(responses)
        self.assertEqual(api.call_count,3);self.assertEqual(reads.call_count,1)
        names=self.names();inspections=[m for m in self.agent.messages if m.get('role')=='tool' and names.get(m.get('tool_call_id'))=='context_inspect']
        self.assertEqual(len(inspections),1)
        inspected=json.loads(inspections[0]['content']);ids={e['id'] for e in inspected['recent_evidence']}
        claims=self.agent.memory.operational.load()['claims'];self.assertEqual(len(claims),1)
        self.assertTrue(set(claims[0]['sources'])<=ids);self.assertNotIn(str(STAMP),claims[0]['sources'])
        checkpoints=[m['content'] for m in self.agent.messages if m.get('role')=='tool' and names.get(m.get('tool_call_id'))=='context_checkpoint']
        self.assertIn('inspection_required',checkpoints[0]);self.assertTrue(json.loads(checkpoints[1])['ok'])
        self.assertEqual(json.loads(checkpoints[1])['source_id_repairs'][str(STAMP)],claims[0]['sources'][0])
        self.assertEqual(self.agent.turn_outcome,'completed')
        self.assertEqual(json.loads(self.agent.session.read_text(encoding='utf-8')),self.agent.messages)

    def test_unknown_ids_interleaved_with_reads_cannot_restart_checkpoint_loop(self):
        args=dict(self.args,sources=['1794000000000'])
        responses=[self.reply('context_checkpoint',args),self.reply('hyperliquid_read',{'resource':'orders'}),
                   self.reply('context_checkpoint',args),self.reply('hyperliquid_read',{'resource':'orders'}),
                   self.reply('context_checkpoint',args)]
        api,reads=self.run_turn(responses)
        self.assertEqual(api.call_count,5);self.assertEqual(reads.call_count,3)
        self.assertEqual(self.agent.checkpoint_recovery['inspections'],2)
        self.assertEqual(self.agent.memory.operational.load()['claims'],[])
        self.assertTrue(self.agent.checkpoint_recovery['suspended'])
        self.assertEqual(self.agent.turn_outcome,'completed')
        self.assertIn('22.37870064',self.agent.messages[-1]['content'])
        self.assertIn('checkpoint sospesi',self.agent.messages[-1]['content'])

    def test_successful_checkpoint_repetition_reuses_claim_and_finishes(self):
        api,reads=self.run_turn([self.reply('context_checkpoint',self.args) for _ in range(4)])
        self.assertEqual(api.call_count,4);self.assertEqual(len(self.agent.memory.operational.load()['claims']),1)
        self.assertTrue(self.agent.checkpoint_recovery['suspended'])

    def test_repeated_calls_in_one_batch_still_get_native_inspection(self):
        batch={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[
            self.reply('context_checkpoint',self.args)['choices'][0]['message']['tool_calls'][0] for _ in range(3)]}}]}
        api,reads=self.run_turn([batch,self.reply('context_checkpoint',self.args),self.reply(text='Saldo e posizioni verificati.')])
        self.assertEqual(api.call_count,3)
        self.assertEqual(self.agent.checkpoint_recovery['inspections'],1)
        self.assertEqual(len(self.agent.memory.operational.load()['claims']),1)

    def test_model_inspection_in_same_batch_does_not_reset_failure_budget(self):
        responses=[]
        args=dict(self.args,kind='not-a-category')
        for _ in range(3):
            calls=[self.reply('context_checkpoint',args)['choices'][0]['message']['tool_calls'][0],
                   self.reply('context_inspect',{})['choices'][0]['message']['tool_calls'][0]]
            responses.append({'choices':[{'message':{'role':'assistant','content':None,'tool_calls':calls}}]})
        api,reads=self.run_turn(responses)
        self.assertEqual(api.call_count,3);self.assertEqual(self.agent.checkpoint_recovery['failures'],3)
        self.assertEqual(self.agent.memory.operational.load()['claims'],[])
        self.assertTrue(self.agent.checkpoint_recovery['suspended'])

    def test_ambiguous_timestamp_is_not_assigned_an_arbitrary_source(self):
        import checkpoint_recovery as recovery
        recovery.begin(self.agent)
        for index,resource in enumerate(('risk','account')):
            ident='read-'+str(index)
            self.agent.messages.extend([
                {'role':'assistant','content':None,'tool_calls':[{'id':ident,'type':'function','function':{'name':'hyperliquid_read','arguments':json.dumps({'resource':resource})}}]},
                {'role':'tool','tool_call_id':ident,'content':json.dumps({'retrieved_ms':STAMP,'data':resource})}])
        with patch.object(f,'HOME',self.home):self.agent.save()
        self.agent.tool('context_inspect',{})
        normalized,changes=recovery.sources(self.agent,self.args)
        self.assertEqual(normalized,self.args);self.assertEqual(changes,{})
        self.assertIn('invalid_sources',self.agent.tool('context_checkpoint',self.args))
        self.assertEqual(self.agent.memory.operational.load()['claims'],[])


if __name__=='__main__':unittest.main()
