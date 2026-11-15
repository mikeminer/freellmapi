"""Reproduce naked entries, rejected TP legs and partial fills without real funds."""
import copy,json,time,unittest
from types import SimpleNamespace
from unittest.mock import patch,Mock
import hyperliquid_tools as h,hyperliquid_signer as signer
import hyperliquid_protection as protection
import test_hyperliquid as fixtures
from financial_completion import assess,render


class ProtectionTests(unittest.TestCase):
    setUp=fixtures.HyperliquidTests.setUp
    response=fixtures.HyperliquidTests.response
    key=fixtures.HyperliquidTests.key
    settings=fixtures.HyperliquidTests.settings
    arm=fixtures.HyperliquidTests.arm
    order=fixtures.HyperliquidTests.order

    def sdk(self,entry=None,tp='waitingForFill',sl='waitingForFill'):
        return {'status':'ok','response':{'type':'order','data':{'statuses':[entry or {'resting':{'oid':101}},tp,sl]}}}

    def stored(self,row,state='accepted',raw=None):
        response={'sent_once':True,'grouped_tpsl':True,'sdk_response':raw or self.sdk()}
        with h.database(self.home) as db:
            db.execute('UPDATE actions SET state=?,response=?,updated=? WHERE id=?',(state,json.dumps(response),time.time(),row['id']));db.commit()
            return h.receipt(db.execute('SELECT * FROM actions WHERE id=?',(row['id'],)).fetchone())

    def exchange_read(self,body,states,corrupt=None):
        ids={body['cloid']:'entry',body['bracket']['tp_cloid']:'tp',body['bracket']['sl_cloid']:'sl'}
        def read(p,args):
            self.assertEqual(args['type'],'orderStatus');self.assertEqual(args['user'],body['target'])
            leg=ids[args['oid']];state=states[leg]
            if state=='unknown':return {'status':'unknownOid'}
            order={'coin':body['coin'],'side':('B' if body['side']=='buy' else 'A') if leg=='entry' else ('A' if body['side']=='buy' else 'B'),
                   'sz':body['size'],'origSz':body['size'],'reduceOnly':leg!='entry','cloid':args['oid']}
            if leg!='entry':order['triggerPx']=body['bracket']['take_profit' if leg=='tp' else 'stop_loss']
            if corrupt and leg==corrupt[0]:order.update(corrupt[1])
            return {'status':'order','order':{'status':state,'order':order}}
        return read

    def test_manual_and_auto_naked_entry_fail_before_receipt_or_signer(self):
        args={'action':'order','coin':'BTC','side':'buy','size':'.001','price':'68000','order_kind':'limit'}
        with patch('hl_credentials.get') as key,patch('subprocess.run') as worker:
            for mode in ('ask','auto'):
                self.a.mode=mode
                for exits in ({},{'take_profit':'69000'},{'stop_loss':'67000'}):
                    result=h.dispatch(self.a,'hyperliquid_preview',args|exits,self.home)
                    self.assertEqual(result['code'],'protection_required');self.assertFalse(result['sent'])
                    self.assertEqual(result['recovery_tools'][0]['name'],'hyperliquid_read')
        self.assertFalse((self.home/'hyperliquid/operations.db').exists());key.assert_not_called();worker.assert_not_called()

    def test_old_manual_preview_cannot_bypass_new_policy_at_submit_or_worker(self):
        self.key();row=self.order();body=copy.deepcopy(row['body']);body.pop('bracket')
        with h.database(self.home) as db:db.execute('UPDATE actions SET body=? WHERE id=?',(json.dumps(body),row['id']));db.commit()
        self.a.interactive=True
        with patch('questions.ask') as ask,patch('subprocess.run') as worker:
            result=h.dispatch(self.a,'hyperliquid_submit',{'id':row['id']},self.home)
        self.assertEqual(result['code'],'protection_required');ask.assert_not_called();worker.assert_not_called()
        with h.database(self.home) as db:db.execute("UPDATE actions SET state='authorized' WHERE id=?",(row['id'],));db.commit()
        with patch('hl_credentials.get') as key,patch('hyperliquid.exchange.Exchange') as exchange:
            result=signer.execute(self.home,row['id'])
        self.assertEqual(result['code'],'protection_required');self.assertFalse(result['sent']);key.assert_not_called();exchange.assert_not_called()

    def test_long_and_short_build_same_size_opposite_reduce_only_exits(self):
        for side,tp,sl in (('buy','69000','67000'),('sell','67000','69000')):
            row=self.order(side=side,take_profit=tp,stop_loss=sl);body=row['body'];protection.require_bracket(body,True)
            requests=signer.order_requests(body)
            self.assertEqual(len(requests),3);self.assertEqual(len({r['cloid'].to_raw() for r in requests}),3)
            for r in requests[1:]:
                self.assertEqual(r['sz'],requests[0]['sz']);self.assertNotEqual(r['is_buy'],requests[0]['is_buy']);self.assertTrue(r['reduce_only'])
            for bad in ({'take_profit':'68000'},{'stop_loss':'68000'}):
                with self.assertRaises(h.HLProblem):h.preview(self.a,self.home,{'action':'order','coin':'BTC','size':'.001','price':'68000','order_kind':'limit','side':side,'take_profit':tp,'stop_loss':sl,'bracket_slippage_bps':30}|bad)

    def test_reduce_only_close_still_allowed_and_truthy_text_cannot_bypass(self):
        self.assertNotIn('bracket',self.order(reduce_only=True)['body'])
        with self.assertRaises(h.HLProblem):self.order(reduce_only='false')

    def test_parent_acceptance_missing_tp_or_generic_success_is_not_group_success(self):
        body=self.order()['body']
        for raw in (self.sdk(tp={'error':'invalid trigger'}),self.sdk(tp='success'),self.sdk(tp={'resting':{}})):
            self.assertFalse(protection.sdk_evidence(body,{'sdk_response':raw,'grouped_tpsl':True})['accepted'])
        raw=self.sdk();raw['response']['data']['statuses']=raw['response']['data']['statuses'][:1]
        self.assertEqual(signer.response_state(raw,'order',True,body),'partial')
        for malformed in (None,{}, {'sdk_response':{'response':'bad'}},{'sdk_response':{'status':'ok','response':{'data':{'statuses':None}}}}):
            self.assertFalse(protection.sdk_evidence(body,malformed)['accepted'])

    def test_pending_entry_cannot_hide_an_observed_wrong_tp(self):
        row=self.order();saved=self.stored(row)
        reader=self.exchange_read(row['body'],{'entry':'open','tp':'open','sl':'unknown'},('tp',{'triggerPx':'1'}))
        with patch.object(h,'info',side_effect=reader),patch.object(h,'stop'):
            result=protection.finalize(self.home,{'receipt':saved})
        self.assertEqual(result['protection']['state'],'unverified');self.assertEqual(result['receipt']['state'],'partial')

    def test_ioc_partial_fill_is_not_reported_as_fully_protected(self):
        row=self.order();raw=self.sdk(entry={'filled':{'oid':1,'totalSz':'.0005','avgPx':'68000'}},tp='waitingForTrigger',sl='waitingForTrigger')
        self.assertEqual(signer.response_state(raw,'order',True,row['body']),'partial')
        saved=self.stored(row,'partial',raw)
        with patch.object(h,'info') as read,patch.object(h,'stop') as stop:
            result=protection.finalize(self.home,{'receipt':saved})
        self.assertEqual(result['protection']['state'],'partial_entry');read.assert_not_called();stop.assert_called_once()

    def test_full_entry_verifies_both_live_exits_once_without_polling(self):
        for side,tp,sl in (('buy','69000','67000'),('sell','67000','69000')):
            row=self.order(side=side,take_profit=tp,stop_loss=sl)
            raw=self.sdk(entry={'filled':{'oid':1,'totalSz':'.001','avgPx':'68000'}},tp='waitingForTrigger',sl='waitingForTrigger')
            saved=self.stored(row,'filled',raw)
            reader=self.exchange_read(row['body'],{'entry':'filled','tp':'open','sl':'open'})
            with patch.object(h,'info',side_effect=reader) as read,patch.object(h,'stop') as stop,patch('subprocess.run') as worker:
                result=protection.finalize(self.home,{'receipt':saved});protection.finalize(self.home,result)
            self.assertEqual(result['protection']['state'],'active');self.assertEqual(read.call_count,3);stop.assert_not_called();worker.assert_not_called()
            self.assertTrue(protection.completion(row['body'],result['receipt']['response']))

    def test_pending_child_is_never_described_as_active_protection(self):
        row=self.order();saved=self.stored(row)
        reader=self.exchange_read(row['body'],{'entry':'open','tp':'unknown','sl':'unknown'})
        with patch.object(h,'info',side_effect=reader) as read,patch.object(h,'stop') as stop:
            result=protection.finalize(self.home,{'receipt':saved})
        self.assertEqual(result['protection']['state'],'pending_entry');self.assertEqual(read.call_count,3);stop.assert_not_called()
        request={'coin':'BTC','execution_action':'order'};self.a.financial_turn_started_at=time.time()-60
        evidence=[{'tool':'hyperliquid_submit','arguments':{'id':row['id']},'result':result}]
        text=render(assess(self.a,request,evidence,self.home));self.assertIn('differiti',text);self.assertIn('69000',text)

    def test_missing_wrong_side_wrong_size_or_wrong_trigger_halts_without_new_orders(self):
        self.a.hyperliquid_analysis={('main','BTC'):{'scope':h.fingerprint(self.p),'as_of_ms':int(time.time()*1000),'data_ready':True,'evidence_id':'synthetic-analysis'}}
        for number,corrupt in enumerate((None,('tp',{'side':'B'}),('tp',{'origSz':'.0005'}),('sl',{'triggerPx':'66000'}))):
            self.a.messages.append({'role':'user','content':'scenario '+str(number)})
            row=self.order();saved=self.stored(row,'filled',self.sdk(entry={'filled':{'oid':1,'totalSz':'.001'}},tp='waitingForTrigger',sl='waitingForTrigger'))
            states={'entry':'filled','tp':'unknown' if corrupt is None else 'open','sl':'open'}
            with patch.object(h,'info',side_effect=self.exchange_read(row['body'],states,corrupt)) as read,patch.object(h,'stop') as stop,patch('subprocess.run') as worker:
                result=protection.finalize(self.home,{'receipt':saved});again=h.submit(self.a,self.home,row['id'])
            self.assertEqual(result['receipt']['state'],'partial');self.assertTrue(again['reused']);self.assertEqual(read.call_count,3);worker.assert_not_called();stop.assert_called_once()
            self.a.financial_turn_started_at=time.time()-60
            verdict=assess(self.a,{'coin':'BTC','execution_action':'order'},[{'tool':'hyperliquid_submit','arguments':{'id':row['id']},'result':result}],self.home)
            self.assertFalse(verdict['verified']);self.assertTrue(verdict['halt'])

    def test_exchange_read_failure_preserves_original_ids_and_context_no_reentry(self):
        row=self.order();saved=self.stored(row);before=copy.deepcopy(self.a.messages)
        with patch.object(h,'info',side_effect=TimeoutError('fixture')) as read,patch.object(h,'stop'),patch('hl_credentials.get') as key:
            result=protection.finalize(self.home,{'receipt':saved})
        self.assertEqual(read.call_count,6);self.assertEqual(result['receipt']['body'],row['body']);self.assertEqual(self.a.messages,before)
        self.assertEqual(result['receipt']['state'],'partial');key.assert_not_called()


if __name__=='__main__':unittest.main()
