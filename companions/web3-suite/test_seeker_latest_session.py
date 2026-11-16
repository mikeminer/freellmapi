"""Replay the latest Seeker request and split MSIX state, without real orders."""
import io,json,os,socket,tempfile,time,unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
import freellmapi as f,hyperliquid_tools as h,hyperliquid_authority as authority
from app_home import effective_home,default_home
from financial_completion import delegated_signal,requirement
from market_routing import intent
from session_store import Sessions
from test_hyperliquid_leverage import LeverageFixture

PROMPT='trovami il migliore trade su hyperliquid ed eseguilo. usa metà del capitale'


class SeekerLatestSessionTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.home=self.root/'physical';self.home.mkdir()
        (self.home/'config.json').write_text('{}')
        self.fixture=LeverageFixture(self.home);self.addCleanup(self.fixture.close)
        self.fixture.cash='1000';authority.enable(self.home,'main',loss_limit=None)
        with h.database(self.home) as db:db.execute('DELETE FROM grants');db.commit()
        self.old_info=h.info
        base=self.fixture.info
        def info(profile,args):
            if args['type']=='metaAndAssetCtxs':
                return [base(profile,{'type':'meta'}),[{'markPx':'0.01','prevDayPx':'0.011','dayNtlVlm':'1000000','openInterest':'100000','funding':'0.00001'}]]
            return base(profile,args)
        self.info=info
    def turn(self,replies,answers=None):
        with patch.object(f,'HOME',self.home),patch.object(h,'info',side_effect=self.info):
            agent=f.Agent(self.home,'http://127.0.0.1:9','synthetic','auto',interactive=True)
            sessions=Sessions(self.home);sessions.activate(agent,sessions.create(self.home))
            agent.auto_skills=True;agent.streaming=False;agent.mode='auto';agent.context_limit=200000
            responses=[{'model':'scripted-seeker-regression','choices':[{'message':message}]} for message in replies]
            with patch.object(agent,'api',side_effect=responses) as api,patch.object(agent.skill_router,'run') as skills,patch.object(f,'ask_question',side_effect=answers) as ask,patch.object(f,'output'),redirect_stdout(io.StringIO()):
                agent.turn(PROMPT)
            skills.assert_not_called()
            return agent,api,ask
    def hold(self):
        return {'role':'assistant','content':None,'tool_calls':[{'id':'hold-once','type':'function','function':{'name':'hyperliquid_execute_plan','arguments':json.dumps({'profile':'main','plan':json.dumps({'decision':'hold','reason':'Synthetic scan alone is insufficient for a justified entry.'})})}}]}
    def test_actual_prompt_selects_current_universe_instead_of_previous_coin(self):
        request=intent(PROMPT,[{'role':'user','content':'analizza ETH/USDC'}])
        self.assertEqual(request['kind'],'scan');self.assertIsNone(request['coin']);self.assertEqual(request['venue'],'hyperliquid')
        self.assertEqual(requirement(PROMPT,request),'order');self.assertTrue(delegated_signal(PROMPT))
        readonly=intent(PROMPT+'. Solo analisi, non inviare ordini')
        self.assertFalse(readonly['trading']);self.assertIsNone(requirement(PROMPT+'. Solo analisi, non inviare ordini',readonly))
    def test_balance_only_draft_gets_one_structured_recovery_then_real_hold(self):
        agent,api,ask=self.turn([{'role':'assistant','content':'Ho letto il saldo. Sto preparando la selezione.'},self.hold()])
        self.assertEqual(api.call_count,2);ask.assert_not_called();self.assertEqual(agent.turn_outcome,'completed')
        payload=api.call_args_list[0].args[1]
        self.assertIn('hyperliquid_scan',{t['function']['name'] for t in payload['tools']})
        names=[c['function']['name'] for m in agent.messages for c in m.get('tool_calls',[])]
        self.assertEqual(names.count('hyperliquid_scan'),1);self.assertEqual(names.count('hyperliquid_preflight'),1)
        self.assertTrue(agent.messages[-1]['content'].startswith('HOLD'))
        self.assertEqual(agent.messages[0]['content'],PROMPT);self.fixture.exchange.bulk_orders.assert_not_called();self.fixture.secret.assert_not_called()
        with h.database(self.home) as db:self.assertEqual(db.execute('SELECT count(*) FROM grants').fetchone()[0],0)
    def test_repeated_prose_cannot_loop_or_falsely_report_an_order(self):
        prose={'role':'assistant','content':'Preparo il piano dopo la lettura del saldo.'}
        agent,api,ask=self.turn([prose,prose])
        self.assertEqual(api.call_count,2);self.assertEqual(agent.turn_outcome,'incomplete');ask.assert_not_called()
        self.assertIn('nessuna ricevuta',agent.messages[-1]['content']);self.assertIn('market_analysis_not_completed',agent.messages[-1]['content']);self.fixture.exchange.bulk_orders.assert_not_called()
    def test_recovery_can_complete_missing_analysis_before_a_structured_outcome(self):
        analyze={'role':'assistant','content':None,'tool_calls':[{'id':'candidate','type':'function','function':{'name':'hyperliquid_analyze','arguments':json.dumps({'profile':'main','coin':'PUMP'})}}]}
        analysis={'profile':'main','network':'mainnet','coin':'PUMP','data_ready':True,'as_of_ms':int(time.time()*1000),'scope':h.fingerprint(h.profile(self.home,'main'))}
        with patch('hyperliquid_analysis.analyze',return_value=analysis):
            agent,api,ask=self.turn([{'role':'assistant','content':'Ho letto il saldo.'},analyze,self.hold()])
        self.assertEqual(api.call_count,3);self.assertEqual(agent.turn_outcome,'completed');ask.assert_not_called()
        second={t['function']['name'] for t in api.call_args_list[1].args[1]['tools']}
        third={t['function']['name'] for t in api.call_args_list[2].args[1]['tools']}
        self.assertIn('hyperliquid_analyze',second);self.assertNotIn('hyperliquid_analyze',third)
        self.fixture.exchange.bulk_orders.assert_not_called()
    def test_missing_margin_vs_notional_is_asked_once_and_kept(self):
        question={'role':'assistant','content':None,'tool_calls':[{'id':'size-choice','type':'function','function':{'name':'ask_user','arguments':json.dumps({'question':'Come vuoi usare metà del capitale?','options':['Come nozionale totale','Come margine']})}}]}
        answers=[{'question':'Come vuoi usare metà del capitale?','answer':'Come nozionale totale','cancelled':False}]
        agent,api,ask=self.turn([question,self.hold()],answers)
        self.assertEqual(api.call_count,2);ask.assert_called_once();self.assertEqual(agent.turn_outcome,'completed')
        self.assertIn('Come nozionale totale',str(agent.question_answers));self.fixture.exchange.bulk_orders.assert_not_called()
    def test_registered_native_monitor_is_counted_as_completion_in_the_same_turn(self):
        import trading_service as service
        from trading_engine import Store
        config=service.load(self.home);store=Store(service.folder(self.home)/'live.sqlite')
        def monitor(name,args):
            self.assertEqual(name,'hyperliquid_monitor');self.assertEqual(args['action'],'watch')
            ident=store.create(config);now=time.time()
            with store.db() as db:
                body=json.loads(db.execute('SELECT body FROM cycles WHERE id=?',(ident,)).fetchone()[0])
                body['watch']={'plan':{'coin':'PUMP','take_profit':'0.012','stop_loss':'0.009'}}
                db.execute('INSERT INTO entry_watches VALUES(?,?,?,?,?,?,?)',(ident,ident,'PUMP','watching',now,now,json.dumps(body)))
                db.execute("UPDATE cycles SET phase='watching',body=? WHERE id=?",(json.dumps(body),ident))
            return json.dumps({'id':ident,'state':'watching','mode':'live'})
        message={'role':'assistant','content':None,'tool_calls':[{'id':'watch','type':'function','function':{'name':'hyperliquid_monitor','arguments':json.dumps({'action':'watch','plan':'{}'})}}]}
        with patch.object(f,'HOME',self.home):
            agent=f.Agent(self.home,'http://127.0.0.1:9','synthetic','auto');sessions=Sessions(self.home);sessions.activate(agent,sessions.create(self.home))
            agent.streaming=False;agent.auto_skills=False;agent.mode='auto';agent.context_limit=200000
            with patch.object(agent,'api',return_value={'choices':[{'message':message}]}) as api,patch.object(agent,'tool',side_effect=monitor),patch('market_routing.collect',return_value=[]),patch.object(f,'output'),redirect_stdout(io.StringIO()):
                agent.turn('Monitora PUMP/USDC su Hyperliquid e attendi il livello di ingresso')
        api.assert_called_once();self.assertEqual(agent.turn_outcome,'completed')
        self.assertIn('Monitor LIVE verificato',agent.messages[-1]['content']);self.fixture.exchange.bulk_orders.assert_not_called()
    def test_actual_current_stop_reports_native_blocker_before_model(self):
        h.stop(self.home,'main')
        agent,api,ask=self.turn([])
        api.assert_not_called();ask.assert_not_called();self.assertEqual(agent.turn_outcome,'incomplete')
        self.assertIn('key_authority_inactive',agent.messages[-1]['content']);self.fixture.exchange.bulk_orders.assert_not_called()
    def test_redirected_config_anchors_key_profile_and_stop_in_one_namespace(self):
        logical=self.root/'logical';logical.mkdir();(logical/'config.json').write_text('{}')
        (logical/'hyperliquid').mkdir();shadow=logical/'hyperliquid/STOP-main';shadow.write_text('old-shadow-stop')
        original_resolve=Path.resolve
        def resolve(path,*args,**kwargs):
            if path==logical/'config.json':return self.home/'config.json'
            return original_resolve(path,*args,**kwargs)
        with patch.object(Path,'resolve',resolve):
            self.assertEqual(effective_home(logical),self.home)
            self.assertTrue(h.status(logical)['execution']['main']['connected'])
            self.assertEqual(h.secret_path(logical,'main'),self.home/'hyperliquid/main.dpapi')
            self.assertEqual(h.stop_epoch(logical,'main'),h.stop_epoch(self.home,'main'))
            h.stop(logical,'main')
            self.assertFalse(authority.current(self.home,h.profile(logical,'main'))['active'])
            self.assertEqual(shadow.read_text(),'old-shadow-stop')
        self.fixture.secret.assert_not_called()

if __name__=='__main__':unittest.main()
