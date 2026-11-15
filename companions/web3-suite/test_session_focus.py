"""Reproduce mixed Space Shooter memory and optional checkpoint stalls offline."""
import copy
import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import freellmapi as f
import market_routing
import reliable_runtime
from memory_vault import Vault
from project_context import bounded_inspection
from session_store import Sessions


class SessionFocusTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.home=Path(tmp.name)
        with patch.object(f,'HOME',self.home):self.agent=f.Agent(self.home,'http://127.0.0.1:9','synthetic','auto')
        store=Sessions(self.home);store.activate(self.agent,store.create(self.home))
        self.agent.memory=Vault(self.home/'vault',self.home);self.context=self.agent.memory.operational
        self.agent.auto_skills=False;self.agent.streaming=False;self.agent.context_limit=200000;self.agent.mode='auto'

    def add(self,session,text,claim):
        msgs=[{'role':'user','content':text}];self.context.ingest(msgs,session)
        ident=hashlib.sha256((str(session)+json.dumps(msgs[0],sort_keys=True,ensure_ascii=False)).encode()).hexdigest()[:20]
        self.context.checkpoint('decision',claim,[ident]);return msgs,ident

    def test_shared_root_scopes_memory_without_deleting_old_game(self):
        self.add('old-game','Crea Space Shooter','Space Shooter: risorse APK incluse, TOKEN-GAME')
        msgs,source=self.add(self.agent.session,'Analizza SOL Hyperliquid','SOL Hyperliquid: 250 USDC, leva 5, TOKEN-TRADING')
        self.agent.messages=msgs
        before=(self.context.folder/'context.json').read_bytes()
        result=json.loads(self.agent.tool('context_inspect',{}))
        self.assertNotIn('TOKEN-GAME',json.dumps(result));self.assertIn('TOKEN-TRADING',json.dumps(result))
        self.assertIn(source,{e['id'] for e in result['recent_evidence']})
        snap=self.context.snapshot('SOL Hyperliquid',session=self.agent.session,messages=msgs)
        self.assertIn('TOKEN-TRADING',snap['text']);self.assertNotIn('TOKEN-GAME',snap['text'])
        self.assertEqual((self.context.folder/'context.json').read_bytes(),before)
        recalled=self.context.inspect(session=self.agent.session,messages=msgs,query='Space Shooter')
        self.assertIn('TOKEN-GAME',json.dumps(recalled))

    def test_legacy_headers_indexed_once_and_revision_invalidates_cache(self):
        msgs,ident=self.add('legacy','Decisione SOL','SOL leva cinque')
        data=self.context.load();del data['events'][ident]['session'];self.context.persist(data)
        self.context.inspect(session='legacy',messages=());self.context.inspect(session='legacy')
        self.assertEqual(self.context.inspection_metrics['legacy_headers_read'],1)
        self.assertEqual(self.context.inspection_metrics['index_builds'],1)
        self.add('legacy','Decisione ETH','ETH leva due')
        value=self.context.inspect(session='legacy')
        self.assertEqual(len(value['claims']),2)
        self.assertEqual(self.context.inspection_metrics['legacy_headers_read'],1)
        self.assertEqual(self.context.inspection_metrics['index_builds'],2)

    def test_structured_pages_retain_ids_and_allow_entire_history(self):
        data=self.context.load()
        for i in range(90):
            ident=f'{i:020x}'
            data['events'][ident]={'id':ident,'session':self.agent.session.stem,'kind':'observed','tool':'shell','actor':'main','date':'synthetic','summary':'z'*900,'path':f'Projects/{self.agent.memory.project_id}/Evidence/{ident}.md'}
            data['claims'].append({'id':f'{i:012x}','text':'relevant '*300,'kind':'verified','sources':[ident],'date':'synthetic','authority':'historical'})
        self.context.v.atomic(self.context.folder/'context.json',json.dumps(data))
        self.agent.messages=[{'role':'user','content':'relevant history'}]
        offsets={};claims=set();events=set();pages=0
        while True:
            value=json.loads(reliable_runtime.execute(self.agent,'inspect-'+str(pages),'context_inspect',offsets,self.agent.tool_definitions()))
            self.assertNotIn('preview',value);self.assertIn('recent_evidence',value)
            self.assertLess(len(json.dumps(value)),32000)
            claims.update(c['id'] for c in value['claims']);events.update(e['id'] for e in value['recent_evidence'])
            pages+=1
            offsets=value['pagination'].get('next_arguments')
            if not offsets:break
            self.assertLess(pages,20)
        self.assertEqual(len(claims),90);self.assertEqual(len(events),90)
        self.assertEqual(self.context.inspection_metrics['index_builds'],1)

    def test_tight_budget_preserves_structured_context_and_continuation(self):
        self.add(self.agent.session,'SOL mercato','SOL '+'x'*2900)
        value=bounded_inspection(self.context.inspect(session=self.agent.session),budget=4000)
        self.assertIn('recent_evidence',value);self.assertIn('pagination',value)
        self.assertLess(len(json.dumps(value,ensure_ascii=False)),5000)
        json.loads(json.dumps(value))

    def test_repeat_answer_has_no_model_tools_or_duplicate_order(self):
        previous='SOL SHORT: ricevuta storica ABC, TP e SL verificati a quel momento.'
        self.agent.messages=[{'role':'assistant','content':None,'tool_calls':[{'id':'old-order','type':'function','function':{'name':'hyperliquid_execute_plan','arguments':'{}'}}]},
                             {'role':'tool','tool_call_id':'old-order','content':'{"receipt":"ABC"}'},
                             {'role':'assistant','content':previous}]
        with patch.object(f,'HOME',self.home),patch.object(self.agent,'api') as api,patch.object(self.agent,'tool') as tool,patch.object(f,'output'),redirect_stdout(io.StringIO()):
            self.agent.turn('ripeti');self.agent.model='another-model';self.agent.turn('ripeti')
        api.assert_not_called();tool.assert_not_called()
        self.assertEqual(self.agent.messages[-1]['content'].count('senza un nuovo ordine'),1)
        self.assertIn(previous,self.agent.messages[-1]['content']);self.assertEqual(self.agent.turn_outcome,'completed')
        metrics=json.loads(self.agent.session.with_suffix('.timings.json').read_text())
        self.assertNotIn('inference',metrics['phases']);self.assertIn('persistence',metrics['phases'])

    def test_market_followup_scans_afresh_without_inheriting_old_coin_or_execution(self):
        history=[{'role':'user','content':'Trova un segnale sui mercati perpetual di Hyperliquid ed esegui ordine SOL'}, {'role':'user','content':'ripeti'}]
        prompt='ho chiuso manualmente in profitto. voglio rianalizzare il mercato per la prossima nuova opportunità'
        result=market_routing.intent(prompt,history)
        self.assertEqual(result['kind'],'scan');self.assertEqual(result['venue'],'hyperliquid')
        self.assertFalse(result['trading']);self.assertIsNone(result['coin'])
        history.append({'role':'user','content':'Adesso sviluppa un nuovo gioco Space Shooter'})
        self.assertIsNone(market_routing.intent(prompt,history))

    def run_suspended_read(self,final):
        import checkpoint_recovery as recovery
        evidence=[{'tool':'market_snapshot','arguments':{},'result':{'symbol':'BTCUSDC','venue':'synthetic','ticker_24h':{'lastPrice':'123'},'source':'https://example.invalid'}}]
        count=0
        def suspend(agent):
            nonlocal count
            count+=1;agent.checkpoint_recovery['suspended']=True
        reply={'choices':[{'message':final}]}
        with patch.object(f,'HOME',self.home),patch('market_routing.collect',return_value=evidence),patch.object(recovery,'inspect_pending',side_effect=suspend),patch.object(self.agent,'api',return_value=reply) as api,patch.object(self.agent,'tool') as tool,patch.object(f,'output'),redirect_stdout(io.StringIO()):
            self.agent.turn('Analizza BTC/USDC, non inviare ordini')
        self.assertEqual(api.call_count,1);tool.assert_not_called()
        payload=api.call_args.args[1];self.assertEqual(payload['tools'],[]);self.assertEqual(payload['tool_choice'],'none')
        self.assertIn('123',self.agent.messages[-1]['content'])
        return self.agent.turn_outcome

    def test_failed_optional_checkpoint_still_completes_read_only_analysis(self):
        self.assertEqual(self.run_suspended_read({'role':'assistant','content':'BTC osservato a 123, dati sintetici, nessun invio.'}),'completed')

    def test_provider_ignoring_no_tools_is_blocked_without_loop(self):
        final={'role':'assistant','content':None,'tool_calls':[{'id':'bad','type':'function','function':{'name':'hyperliquid_submit','arguments':'{}'}}]}
        self.assertEqual(self.run_suspended_read(final),'incomplete')

    def test_note_cache_detects_external_edits_and_skips_archives(self):
        vault=self.agent.memory
        vault.atomic(vault.path/'Conversations/old.md','ARCHIVE-NEVER-READ')
        vault.atomic(self.context.folder/'Evidence/old.md','EVIDENCE-NEVER-READ')
        name=vault.remember('SOL Hyperliquid memo TOKEN-BEFORE','project')
        first=vault.documents('auto');reads=vault.note_catalog.reads
        second=vault.documents('auto')
        self.assertEqual(first,second);self.assertEqual(vault.note_catalog.reads,reads)
        self.assertNotIn('NEVER-READ',json.dumps(second))
        vault.note_path(name).write_text('Progetto: '+vault.project_id+'\nSOL Hyperliquid memo TOKEN-AFTER with external edit',encoding='utf-8')
        updated=vault.documents('auto')
        self.assertIn('TOKEN-AFTER',json.dumps(updated));self.assertNotIn('TOKEN-BEFORE',json.dumps(updated))

    def test_model_switch_reuses_relevant_view_and_keeps_session(self):
        self.add('old-game','Build Space Shooter','Space Shooter TOKEN-GAME')
        self.agent.messages,_=self.add(self.agent.session,'Analizza SOL Hyperliquid','SOL Hyperliquid TOKEN-TRADE')
        before=self.agent.session
        with patch.object(f,'HOME',self.home):
            first=self.agent.system();metrics=dict(self.context.inspection_metrics)
            self.agent.model='model-b';second=self.agent.system()
        self.assertIn('TOKEN-TRADE',first);self.assertIn('TOKEN-TRADE',second)
        self.assertNotIn('TOKEN-GAME',first);self.assertNotIn('TOKEN-GAME',second)
        self.assertEqual(self.agent.session,before)
        self.assertEqual(self.context.inspection_metrics['index_builds'],metrics['index_builds'])

    def test_phase_measurement_records_failures_without_logging_arguments(self):
        from phase_metrics import Timings,measured,render
        class Probe:
            phase_metrics=Timings()
            @measured('tools')
            def broken(self,secret):raise ValueError('synthetic')
        probe=Probe()
        with self.assertRaises(ValueError):probe.broken('MUST-NOT-LOG')
        state=probe.phase_metrics.snapshot()
        self.assertEqual(state['phases']['tools']['calls'],1);self.assertEqual(state['active'],[])
        self.assertNotIn('MUST-NOT-LOG',json.dumps(state));self.assertIn('non vanno sommati',render(state))

    def test_read_only_projection_keeps_answers_and_current_evidence_not_old_bulk(self):
        messages=[{'role':'system','content':'system'},
                  {'role':'user','content':'Trova opportunità Hyperliquid'},
                  {'role':'assistant','content':None,'tool_calls':[{'id':'q','function':{'name':'ask_user'}},{'id':'old','function':{'name':'hyperliquid_read'}}]},
                  {'role':'tool','tool_call_id':'q','content':'{"answer":"250 USDC nozionale, 5x"}'},
                  {'role':'tool','tool_call_id':'old','content':'OLD-BULK '*10000},
                  {'role':'assistant','content':'Ricevuta storica ABC.'},
                  {'role':'user','content':'Rianalizza il mercato'},
                  {'role':'tool','tool_call_id':'new','content':'CURRENT-OBSERVATION'}]
        payload={'messages':copy.deepcopy(messages),'tools':copy.deepcopy(f.TOOLS)}
        value=market_routing.analysis_payload(payload,{'kind':'scan','trading':False})
        self.assertGreater(value['before_characters'],value['after_characters']+50000)
        text=json.dumps(payload);self.assertIn('250 USDC',text);self.assertIn('CURRENT-OBSERVATION',text)
        self.assertIn('Ricevuta storica ABC',text);self.assertNotIn('OLD-BULK',text)
        self.assertEqual(len(messages),8);self.assertIn('OLD-BULK',messages[4]['content'])
        self.assertNotIn('hyperliquid_submit',{t['function']['name'] for t in payload['tools']})


if __name__=='__main__':unittest.main()
