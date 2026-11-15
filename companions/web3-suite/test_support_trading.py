"""Test-only fake exchange; never imported by application modules."""
import json,time
from decimal import Decimal
from trading_engine import dec,number

class FakeBroker:
    """A separate virtual ledger: no SDK signer, key store or exchange endpoint."""
    def __init__(self,store,config):
        self.store=store;self.config=config;config['mode']='live'
        with store.db() as db:db.execute('CREATE TABLE IF NOT EXISTS fixture_fills(intent TEXT PRIMARY KEY,time REAL,body TEXT)')
        if store.get('fixture_account') is None:store.set('fixture_account',{'cash':number(config.get('initial_equity','1000')),'baseline':number(config.get('initial_equity','1000')),'positions':[],'turnover':'0','entry_times':[]})
    def account(self,snapshot):
        a=self.store.get('fixture_account');equity=dec(a['cash']);margin=Decimal(0)
        for p in a['positions']:
            if p['coin']==snapshot['coin']:p['mark']=snapshot['mid']
            sign=1 if p['side']=='buy' else -1
            equity+=dec(p['size'])*(dec(p['mark'])-dec(p['entry']))*sign;margin+=dec(p['size'])*dec(p['entry'])/p['leverage']
        return {**a,'equity':number(equity),'available':number(max(Decimal(0),equity-margin)),'mode':'live'}
    def execute(self,intent,plan):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE');old=db.execute('SELECT body FROM fixture_fills WHERE intent=?',(intent,)).fetchone()
            if old:return json.loads(old[0])
            a=json.loads(db.execute("SELECT value FROM state WHERE key='fixture_account'").fetchone()[0])
            # Simulated taker fee and adverse fill; never interpreted as live PnL.
            entry=dec(plan['entry'])*(Decimal('1.0005') if plan['side']=='buy' else Decimal('.9995'))
            fee=dec(plan['size'])*entry*Decimal('.0005');a['cash']=number(dec(a['cash'])-fee)
            a['positions'].append({**plan,'entry':number(entry),'mark':number(entry),'intent':intent})
            a['turnover']=number(dec(a['turnover'])+dec(plan['notional']));a['entry_times']=[t for t in a['entry_times'] if t>time.time()-3600]+[time.time()]
            receipt={'state':'mock_filled','intent':intent,'plan':plan,'fill_price':number(entry),'fee':number(fee),'real_order_sent':False}
            db.execute('INSERT INTO fixture_fills VALUES(?,?,?)',(intent,time.time(),json.dumps(receipt)))
            db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',('fixture_account',json.dumps(a)))
            return receipt
    def recover(self,row):
        with self.store.db() as db:r=db.execute('SELECT body FROM fixture_fills WHERE intent=?',(row['id'],)).fetchone()
        return {'resolved':True,'receipt':json.loads(r[0]) if r else None,'abandoned':not bool(r),'resent':False}
    def mark(self,snapshot):
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE');a=json.loads(db.execute("SELECT value FROM state WHERE key='fixture_account'").fetchone()[0]);remaining=[]
            for p in a['positions']:
                if p['coin']!=snapshot['coin']:remaining.append(p);continue
                mid=dec(snapshot['mid']);p['mark']=number(mid)
                tp=mid>=dec(p['take_profit']) if p['side']=='buy' else mid<=dec(p['take_profit'])
                sl=mid<=dec(p['stop_loss']) if p['side']=='buy' else mid>=dec(p['stop_loss'])
                if not (tp or sl):remaining.append(p);continue
                exit_price=mid*(Decimal('.9995') if p['side']=='buy' else Decimal('1.0005'))
                pnl=dec(p['size'])*(exit_price-dec(p['entry']))*(1 if p['side']=='buy' else -1);fee=dec(p['size'])*exit_price*Decimal('.0005')
                a['cash']=number(dec(a['cash'])+pnl-fee)
                db.execute('INSERT INTO events(time,kind,cycle,body) VALUES(?,?,?,?)',(time.time(),'mock_exit',p['intent'],json.dumps({'coin':p['coin'],'reason':'SL' if sl else 'TP','pnl_after_exit_fee':number(pnl-fee)})))
            a['positions']=remaining;db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',('fixture_account',json.dumps(a)))
