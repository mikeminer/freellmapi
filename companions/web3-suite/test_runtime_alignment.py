"""Shared runtime selection and non-replaying migration, using temporary data."""
import json,os,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import app_home,hyperliquid_tools as h,hyperliquid_authority as authority,hyperliquid_execution as execution
from runtime_alignment import merge_sessions,merge_receipts,publish_binding,sha
from test_hyperliquid_leverage import LeverageFixture

class RuntimeAlignmentTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.root=Path(temp.name)
        self.old=self.root/'normal/freellmapi-cli';self.new=self.root/'remote/freellmapi-cli';self.pointer=self.root/'runtime-home.json'
        for home in (self.old,self.new):home.mkdir(parents=True);(home/'config.json').write_text('{}')
    def bind(self):publish_binding(self.pointer,self.new,[self.old,self.new])
    def test_both_original_namespaces_choose_the_same_existing_home(self):
        self.bind()
        with patch.object(app_home,'binding_path',return_value=self.pointer),patch.dict(os.environ,{},clear=True):
            for home in (self.old,self.new):
                with patch.dict(os.environ,{'APPDATA':str(home.parent)}):self.assertEqual(app_home.default_home(),self.new)
    def test_missing_shared_archive_fails_closed_without_creating_another(self):
        self.bind();(self.new/'config.json').unlink()
        with patch.object(app_home,'binding_path',return_value=self.pointer),patch.dict(os.environ,{'APPDATA':str(self.old.parent)},clear=True):
            with self.assertRaisesRegex(RuntimeError,'Avvio fermato'):app_home.default_home()
        self.assertFalse((self.new/'config.json').exists())
    def test_explicit_isolated_appdata_and_home_remain_isolated(self):
        self.bind();custom=self.root/'qa/freellmapi-cli'
        with patch.object(app_home,'binding_path',return_value=self.pointer),patch.dict(os.environ,{'APPDATA':str(custom.parent)},clear=True):
            self.assertEqual(app_home.default_home(),custom)
            with patch.dict(os.environ,{'FREELLMAPI_HOME':str(self.old)}):self.assertEqual(app_home.default_home(),self.old)
    def test_explicit_service_and_signer_home_aliases_also_choose_shared_archive(self):
        import trading_service
        self.bind()
        with patch.object(app_home,'binding_path',return_value=self.pointer):
            self.assertEqual(app_home.runtime_home(self.old),self.new)
            self.assertEqual(trading_service.folder(self.old),self.new/'trading-service')
            # Historical import tools retain explicit access to the old archive.
            self.assertEqual(app_home.effective_home(self.old),self.old)
    def conversation(self,home,sid,text,**sidecars):
        folder=home/'sessions/conversations';folder.mkdir(parents=True,exist_ok=True)
        (folder/(sid+'.json')).write_text(json.dumps([{'role':'user','content':text}]))
        (folder/(sid+'.meta.json')).write_text(json.dumps({'id':sid,'root':str(self.root),'title':text}))
        for suffix,value in sidecars.items():(folder/(sid+'.'+suffix+'.json')).write_text(json.dumps(value))
    def test_session_collision_keeps_both_contexts_and_is_idempotent(self):
        sid='a'*32;self.conversation(self.old,sid,'source',work={'marker':'keep source'},answers={'q':'100 USDC, 5x'})
        self.conversation(self.new,sid,'destination',work={'marker':'keep destination'})
        original=sha(self.new/'sessions/conversations'/(sid+'.json'))
        copied=merge_sessions(self.old,self.new,self.root/'archive');selected=copied[0]['selected']
        self.assertNotEqual(selected,sid);self.assertEqual(sha(self.new/'sessions/conversations'/(sid+'.json')),original)
        self.assertIn('keep source',(self.new/'sessions/conversations'/(selected+'.work.json')).read_text())
        self.assertIn('100 USDC',(self.new/'sessions/conversations'/(selected+'.answers.json')).read_text())
        self.assertEqual(merge_sessions(self.old,self.new,self.root/'archive'),copied)
    def test_old_schedules_are_archived_but_not_activated(self):
        sid='b'*32;self.conversation(self.old,sid,'old request',schedule={'job':{'enabled':True,'prompt':'old order'}})
        merge_sessions(self.old,self.new,self.root/'archive')
        self.assertFalse(json.loads((self.new/'sessions/conversations'/(sid+'.schedule.json')).read_text())['job']['enabled'])
        self.assertTrue(json.loads((self.root/'archive'/(sid+'.schedule.json')).read_text())['job']['enabled'])
    def row(self,home,state='filled'):
        with h.database(home) as db:
            db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?)',('a'*24,'old-intent','main',state,1,2,json.dumps({'action':'order','coin':'BTC'}),'{}'))
            db.execute('INSERT INTO grants VALUES(?,?)',('main',json.dumps({'active':True})));db.commit()
    def test_historical_receipts_import_once_without_grants_or_sending(self):
        self.row(self.old);db_path=h.folder(self.old)/'operations.db'
        with patch('hyperliquid_signer.execute',side_effect=AssertionError('Signing forbidden')):
            first=merge_receipts(db_path,self.new);second=merge_receipts(db_path,self.new)
        self.assertEqual(first['actions'],1);self.assertEqual(second['actions'],0);self.assertEqual(first['orders_sent'],0)
        with h.database(self.new) as db:
            self.assertEqual(db.execute('SELECT state FROM actions').fetchone()[0],'filled');self.assertEqual(db.execute('SELECT count(*) FROM grants').fetchone()[0],0)
    def test_uncertain_source_effects_are_not_silently_imported(self):
        self.row(self.old,'uncertain')
        with self.assertRaisesRegex(ValueError,'unresolved'):merge_receipts(h.folder(self.old)/'operations.db',self.new)

class AlignedLeverageTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.fixture=LeverageFixture(temp.name);self.addCleanup(self.fixture.close)
        self.fixture.leverage=40;self.fixture.kind='isolated';self.fixture.cash='1000';authority.enable(self.fixture.home,'main',loss_limit=None)
        self.agent=self.fixture.agent;self.agent.hyperliquid_controller=None;self.agent.mode='auto'
        self.agent.messages=[{'role':'user','content':'Trova un segnale PUMP su Hyperliquid ed eseguilo con 100 USDC di nozionale e leva 5x.'}]
        self.agent.current_market_request={'venue':'hyperliquid','coin':'PUMP','execution_action':'order'}
        self.agent.financial_turn_started_at=time.time()-1;execution.bind_request(self.agent)
        self.agent.hyperliquid_analysis={('main','PUMP'):{'data_ready':True,'evidence_id':'synthetic-alignment','scope':h.fingerprint(h.profile(self.fixture.home,'main')),'as_of_ms':int(time.time()*1000)}}
        self.args={'profile':'main','order_kind':'limit','bracket_slippage_bps':20,'plan':json.dumps({'decision':'enter','reason':'Synthetic regression for the reported 40x vs 5x error','coin':'PUMP','side':'buy','size':'10000','entry':'0.01','take_profit':'0.011','stop_loss':'0.009','leverage':5})}
        self.fixture.exchange.bulk_orders.side_effect=lambda *a,**k:{'status':'ok','response':{'data':{'statuses':[{'resting':{'oid':7}},'waitingForFill','waitingForFill']}}}
    def test_reported_40x_to_5x_is_applied_verified_and_not_repeated(self):
        with h.database(self.fixture.home) as db:db.execute('DELETE FROM grants');db.commit()
        result=execution.execute(self.agent,self.fixture.home,self.args)
        self.assertEqual(result['receipt']['state'],'accepted');self.assertEqual(self.fixture.leverage,5)
        self.fixture.exchange.update_leverage.assert_called_once_with(5,'PUMP',False);self.fixture.exchange.bulk_orders.assert_called_once()
        self.assertEqual(result['receipt']['body']['expected_leverage'],5)
        again=execution.execute(self.agent,self.fixture.home,self.args)
        self.assertTrue(again['reused']);self.fixture.exchange.update_leverage.assert_called_once();self.fixture.exchange.bulk_orders.assert_called_once()
    def test_pre_migration_request_never_changes_leverage_or_creates_a_preview(self):
        from work_ledger import load,save
        ledger=load(self.agent.session);ledger['financial_request']['created']=0;save(self.agent.session,ledger)
        with patch.object(h,'preview',side_effect=AssertionError('Old request cannot create a preview')):
            result=execution.execute(self.agent,self.fixture.home,self.args)
        self.assertEqual(result['code'],'new_request_required');self.assertFalse(result['sent']);self.assertFalse(result['binding_created'])
        self.fixture.exchange.update_leverage.assert_not_called();self.fixture.exchange.bulk_orders.assert_not_called();self.fixture.secret.assert_not_called()
    def test_signal_with_explicit_notional_and_leverage_is_an_entry_request(self):
        from financial_completion import requirement
        self.assertEqual(requirement(self.agent.messages[0]['content'],{'venue':'hyperliquid'}),'order')
        self.assertEqual(requirement('Porta la leva a 5x',{'venue':'hyperliquid'}),'leverage')

if __name__=='__main__':unittest.main()
