"""Old uncertain protection / manually closed positions; no writes to exchange."""
import copy,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import hyperliquid_tools as h
from hyperliquid_reconciliation import reconcile
from hyperliquid_protection import completion


class ReconciliationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.home=Path(temp.name)
        self.p=h.configure(self.home,'main','mainnet','0x'+'1'*40)
        self.body={'action':'order','network':'mainnet','account':self.p['account'],'target':self.p['target'],'scope':h.fingerprint(self.p),
                   'coin':'ETH','side':'buy','size':'0.01','price':'2000','cloid':'0x'+'2'*32,
                   'bracket':{'tp_cloid':'0x'+'3'*32,'sl_cloid':'0x'+'4'*32,'take_profit':'2100','stop_loss':'1900'}}
        self.response={'grouped_tpsl':True,'sdk_response':{'status':'ok','response':{'data':{'statuses':[{'filled':{'oid':123,'totalSz':'0.01','avgPx':'2000'}},'waitingForTrigger','waitingForTrigger']}}},'protection':{'state':'unverified'}}
        self.ident='a'*24;stamp=time.time()-60
        with h.database(self.home) as db:
            db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?)',(self.ident,'intent','main','partial',stamp,stamp,json.dumps(self.body),json.dumps(self.response)));db.commit()
        self.fills=[{'coin':'ETH','oid':123,'side':'B','sz':'0.01','startPosition':'0','dir':'Open Long','time':int(stamp*1000)+1000,'tid':1,'hash':'0x'+'5'*64},
                    {'coin':'ETH','oid':456,'side':'A','sz':'0.01','startPosition':'0.01','dir':'Close Long','time':int(stamp*1000)+2000,'tid':2,'hash':'0x'+'6'*64}]
        self.positions=[];self.orders=[];self.reads=[]
    def reader(self,p,args):
        self.assertEqual(p['target'],self.p['target']);self.assertEqual(p['network'],'mainnet');self.reads.append(args)
        if args['type']=='orderStatus':return {'status':'unknownOid'}
        if args['type']=='userFillsByTime':return self.fills
        if args['type']=='clearinghouseState':return {'assetPositions':self.positions}
        if args['type']=='frontendOpenOrders':return self.orders
        raise AssertionError('Unexpected read')
    def run_reconcile(self):
        with patch.object(h,'info',side_effect=self.reader),patch.object(h,'submit',side_effect=AssertionError('No send')),patch.object(h,'stop',side_effect=AssertionError('No STOP side effect')):
            return h.operations(self.home,self.ident)
    def test_exact_entry_and_contiguous_closing_fills_release_historical_partial(self):
        result=self.run_reconcile();row=result['receipt']
        self.assertEqual(row['state'],'closed');self.assertFalse(result['resent']);self.assertFalse(completion(self.body,row['response']))
        self.assertTrue(row['response']['protection']['closure']['flat_position_verified'])
        self.assertEqual(row['response']['protection_history'][0]['state'],'unverified')
        self.assertTrue(any(r.get('oid')==123 for r in self.reads));self.assertLessEqual(len(self.reads),7)
    def test_flat_account_and_unknown_oids_alone_never_resolve_receipt(self):
        self.fills=[];self.assertEqual(self.run_reconcile()['receipt']['state'],'partial')
    def test_unrelated_entry_oid_or_missing_fills_do_not_clear_blocker(self):
        self.fills[0]['oid']=999;self.assertEqual(self.run_reconcile()['receipt']['state'],'partial')
    def test_existing_position_or_residual_exit_order_prevents_closure(self):
        self.positions=[{'position':{'coin':'ETH','szi':'0.01'}}]
        self.assertEqual(self.run_reconcile()['receipt']['state'],'partial')
        self.positions=[];self.orders=[{'coin':'ETH','oid':999}]
        self.assertEqual(self.run_reconcile()['receipt']['state'],'partial')
    def test_added_exposure_gap_or_flip_in_history_is_ambiguous(self):
        original=copy.deepcopy(self.fills)
        for change in ({'startPosition':'0.02'},{'sz':'0.02'},{'dir':'Long > Short'},{'side':'B'}):
            self.fills=copy.deepcopy(original);self.fills[-1].update(change)
            self.assertEqual(self.run_reconcile()['receipt']['state'],'partial')
    def test_preexisting_position_is_not_attributed_to_this_receipt(self):
        self.fills[0]['startPosition']='0.1';self.assertEqual(self.run_reconcile()['receipt']['state'],'partial')
    def test_manual_addition_followed_by_complete_aggregate_close_is_proved(self):
        extra=self.fills[0]|{'oid':789,'sz':'0.02','startPosition':'0.01','tid':2,'time':self.fills[0]['time']+1}
        self.fills[-1].update(startPosition='0.03',sz='0.03',tid=3)
        self.fills.insert(1,extra)
        result=self.run_reconcile();self.assertEqual(result['receipt']['state'],'closed')
        proof=result['receipt']['response']['protection']['closure']
        self.assertTrue(proof['aggregate_cycle']);self.assertEqual(proof['additional_entry_oids'],[789])
    def test_duplicate_or_truncated_fill_history_stays_unresolved(self):
        self.fills.append(copy.deepcopy(self.fills[0]));self.assertEqual(self.run_reconcile()['receipt']['state'],'partial')
        self.fills=[copy.deepcopy(self.fills[0]) for _ in range(2000)]
        self.assertEqual(self.run_reconcile()['receipt']['state'],'partial')
    def test_scope_change_never_queries_another_account(self):
        data=h.config(self.home);data['profiles']['main']['signer']='0x'+'7'*40;h.save_config(self.home,data)
        with self.assertRaises(h.HLProblem):self.run_reconcile()
        self.assertEqual(self.reads,[])
    def test_short_and_multiple_partial_closing_fills(self):
        self.body['side']='sell'
        self.fills[0].update(side='A',dir='Open Short')
        self.fills[1].update(side='B',dir='Close Short',startPosition='-0.01',sz='0.004')
        self.fills.append(self.fills[1]|{'startPosition':'-0.006','sz':'0.006','tid':3,'time':self.fills[1]['time']+1})
        with h.database(self.home) as db:db.execute('UPDATE actions SET body=? WHERE id=?',(json.dumps(self.body),self.ident));db.commit()
        self.assertEqual(self.run_reconcile()['receipt']['state'],'closed')


if __name__=='__main__':unittest.main()
