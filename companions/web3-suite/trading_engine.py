"""Durable trading state machine. Models propose; deterministic code controls risk."""
import hashlib,json,math,sqlite3,time,uuid
from contextlib import contextmanager
from decimal import Decimal,InvalidOperation
from pathlib import Path


class TradingError(ValueError):pass
def dec(x):
    if isinstance(x,bool):raise TradingError('boolean_is_not_money')
    try:d=Decimal(str(x))
    except (InvalidOperation,TypeError):raise TradingError('invalid_decimal') from None
    if not d.is_finite():raise TradingError('nonfinite_decimal')
    return d
def number(x):return format(dec(x).normalize(),'f')
def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def policy(value):
    value=dict(value)
    if value.get('strategy_source','ai') not in ('ai','rules'):raise TradingError('unknown_strategy_source')
    value.setdefault('entry_mode','conditional')
    value.setdefault('watch_poll_seconds',1)
    value.setdefault('max_watch_seconds',86400)
    value.setdefault('max_trigger_gap_bps',value.get('max_slippage_bps',20))
    value.setdefault('require_trend_alignment',True)
    if value['entry_mode'] not in ('conditional','immediate'):raise TradingError('invalid_entry_mode')
    for key,low,high in [('watch_poll_seconds',1,60),('max_watch_seconds',60,86400),('max_trigger_gap_bps',1,1000)]:
        if type(value[key]) is not int or not low<=value[key]<=high:raise TradingError('invalid_'+key)
    if type(value['require_trend_alignment']) is not bool:raise TradingError('invalid_trend_alignment')
    if value.get('mode') not in ('disabled','live'):raise TradingError('invalid_mode')
    if value.get('network') not in ('mainnet','testnet'):raise TradingError('invalid_network')
    if not isinstance(value.get('profile'),str) or not __import__('re').fullmatch(r'[A-Za-z0-9_-]{1,48}',value['profile']):raise TradingError('invalid_profile')
    if not isinstance(value.get('coins'),list) or not 1<=len(value['coins'])<=8 or any(not isinstance(c,str) or not c or len(c)>40 for c in value['coins']):raise TradingError('exact_coins_required')
    if len(set(value['coins']))!=len(value['coins']):raise TradingError('duplicate_coins')
    for key in ('max_order_usd','max_exposure_usd','max_loss_usd','max_turnover_usd'):
        if not 0<dec(value[key])<=dec('1e12'):raise TradingError('invalid_limit_'+key)
    for key,low,high in [('max_leverage',1,100),('max_orders_hour',1,60),('max_positions',1,8),('interval',60,3600),('max_spread_bps',1,1000),('max_slippage_bps',1,1000)]:
        if type(value.get(key)) is not int or not low<=value[key]<=high:raise TradingError('invalid_'+key)
    if dec(value['max_order_usd'])>dec(value['max_exposure_usd']):raise TradingError('order_exceeds_exposure')
    if not isinstance(value.get('playbook'),str) or not 10<=len(value['playbook'])<=4000:raise TradingError('playbook_required')
    return value


class Store:
    def __init__(self,path):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.db() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE IF NOT EXISTS cycles(id TEXT PRIMARY KEY,phase TEXT,created REAL,updated REAL,body TEXT);
            CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,time REAL,kind TEXT,cycle TEXT,body TEXT);
            CREATE TABLE IF NOT EXISTS entry_watches(id TEXT PRIMARY KEY,setup_hash TEXT UNIQUE,coin TEXT,state TEXT,created REAL,updated REAL,body TEXT);
            CREATE TABLE IF NOT EXISTS order_dispatches(id TEXT PRIMARY KEY,plan_hash TEXT,state TEXT,created REAL,updated REAL,body TEXT);''')
    @contextmanager
    def db(self):
        db=sqlite3.connect(self.path,timeout=5);db.row_factory=sqlite3.Row
        try:
            db.execute('PRAGMA synchronous=FULL')
            with db:yield db
        finally:db.close()
    def get(self,key,default=None):
        with self.db() as db:r=db.execute('SELECT value FROM state WHERE key=?',(key,)).fetchone()
        return json.loads(r[0]) if r else default
    def set(self,key,value):
        with self.db() as db:db.execute('INSERT OR REPLACE INTO state VALUES(?,?)',(key,json.dumps(value)))
    def event(self,kind,cycle,value):
        from secret_output import redact
        with self.db() as db:db.execute('INSERT INTO events(time,kind,cycle,body) VALUES(?,?,?,?)',(time.time(),kind,cycle,redact(json.dumps(value,default=str))))
    def create(self,config):
        ident=uuid.uuid4().hex;body={'policy_hash':fingerprint(config),'mode':config['mode']}
        with self.db() as db:db.execute('INSERT INTO cycles VALUES(?,?,?,?,?)',(ident,'created',time.time(),time.time(),json.dumps(body)))
        return ident
    def phase(self,ident,phase,**value):
        with self.db() as db:
            row=db.execute('SELECT body FROM cycles WHERE id=?',(ident,)).fetchone();body=json.loads(row[0]);body.update(value)
            db.execute('UPDATE cycles SET phase=?,updated=?,body=? WHERE id=?',(phase,time.time(),json.dumps(body,default=str),ident))
            db.execute('UPDATE entry_watches SET state=?,updated=?,body=? WHERE id=?',(phase,time.time(),json.dumps(body,default=str),ident))
            db.execute('UPDATE order_dispatches SET state=?,updated=?,body=? WHERE id=?',(phase,time.time(),json.dumps(body,default=str),ident))
    def transition(self,ident,expected,phase,**value):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT body FROM cycles WHERE id=? AND phase=?',(ident,expected)).fetchone()
            if not row:return False
            body=json.loads(row[0]);body.update(value)
            db.execute('UPDATE cycles SET phase=?,updated=?,body=? WHERE id=? AND phase=?',(phase,time.time(),json.dumps(body),ident,expected))
            db.execute('UPDATE entry_watches SET state=?,updated=?,body=? WHERE id=?',(phase,time.time(),json.dumps(body),ident))
            return True
    def unfinished(self):
        with self.db() as db:rows=db.execute("SELECT * FROM cycles WHERE phase NOT IN ('done','abandoned','paused','expired','invalidated','cancelled') ORDER BY created").fetchall()
        return [dict(r,body=json.loads(r['body'])) for r in rows]
    def recent(self,limit=8):
        with self.db() as db:rows=db.execute('SELECT id,phase,body FROM cycles ORDER BY created DESC LIMIT ?',(limit,)).fetchall()
        return [{'id':r['id'],'phase':r['phase'],'data':json.loads(r['body'])} for r in reversed(rows)]


def validate_plan(plan,snapshot,account,config,store,now=None,allow_future=False):
    exchange_only=config.get('execution_policy')=='exchange'
    if config.get('mode')!='live':raise TradingError('service_disabled_authorization_required')
    now=time.time() if now is None else now
    if not isinstance(plan,dict) or set(plan)-{'decision','coin','side','size','entry','take_profit','stop_loss','leverage','reason','trigger'}:raise TradingError('invalid_plan_fields')
    if not isinstance(plan.get('reason'),str) or not 1<=len(plan['reason'])<=1200:raise TradingError('reason_required')
    if plan.get('decision')=='hold':return {'decision':'hold','reason':plan['reason']}
    if plan.get('decision')=='enter' and config.get('entry_mode','conditional')=='conditional' and not plan.get('trigger'):raise TradingError('conditional_entry_required')
    if plan.get('decision')!='enter' or plan.get('coin')!=snapshot.get('coin') or (not exchange_only and plan['coin'] not in config['coins']):raise TradingError('wrong_instrument_or_decision')
    if plan.get('side') not in ('buy','sell'):raise TradingError('invalid_side')
    if type(plan.get('leverage')) is not int or plan['leverage']<1 or (not exchange_only and plan['leverage']>config['max_leverage']):raise TradingError('leverage_cap')
    if plan['leverage']>snapshot.get('max_leverage',config['max_leverage']):raise TradingError('exchange_leverage_cap')
    if not 0<=now-snapshot['retrieved_at']<=45:raise TradingError('stale_market_data')
    bid,ask=dec(snapshot['bid']),dec(snapshot['ask']);mid=(bid+ask)/2
    if bid<=0 or ask<bid or (ask-bid)/mid*10000>config['max_spread_bps']:raise TradingError('spread_or_book_invalid')
    size,entry,tp,sl=[dec(plan.get(k)) for k in ('size','entry','take_profit','stop_loss')]
    precision=snapshot['sz_decimals']
    if type(precision) is not int or not 0<=precision<=12:raise TradingError('invalid_market_precision')
    if size<=0 or size!=size.quantize(Decimal(10)**-precision) or min(entry,tp,sl)<=0:raise TradingError('invalid_size_or_price')
    if not allow_future and abs(entry-mid)/mid*10000>config['max_slippage_bps']:raise TradingError('entry_too_far_from_market')
    if not (sl<entry<tp if plan['side']=='buy' else tp<entry<sl):raise TradingError('invalid_bracket')
    equity=dec(account['equity']);baseline=dec(account['baseline'])
    if (not exchange_only or config.get('loss_limit_enabled',False)) and baseline-equity>=dec(config['max_loss_usd']):raise TradingError('loss_stop')
    positions=account['positions']
    occupied={p['coin'] for p in positions}|set(account.get('pending_entry_coins',[]))
    if plan['coin'] in occupied or (not exchange_only and len(occupied)>=config['max_positions']):raise TradingError('position_cap')
    worst=max(entry,ask if plan['side']=='buy' else bid);notional=size*worst
    exposure=sum((abs(dec(p['size']))*dec(p['mark']) for p in positions),Decimal(0))+dec(account.get('pending_notional','0'))
    if not exchange_only:
        if notional>dec(config['max_order_usd']) or exposure+notional>dec(config['max_exposure_usd']):raise TradingError('notional_cap')
        if dec(account['turnover'])+notional>dec(config['max_turnover_usd']):raise TradingError('turnover_cap')
        if len([t for t in account['entry_times'] if t>now-3600])>=config['max_orders_hour']:raise TradingError('frequency_cap')
        if notional/plan['leverage']+notional*Decimal('.002')>dec(account['available']):raise TradingError('margin_cap')
    return {**plan,'size':number(size),'entry':number(entry),'take_profit':number(tp),'stop_loss':number(sl),'notional':number(notional)}


class Engine:
    def __init__(self,store,config,reader,planner,broker,clock=time.time,phase_hook=lambda phase:None,quote_reader=None):
        self.store=store;self.config=policy(config);self.reader=reader;self.planner=planner;self.broker=broker;self.clock=clock;self.hook=phase_hook;self.quote_reader=quote_reader or reader
    def phase(self,ident,name,**data):self.store.phase(ident,name,**data);self.hook(name)
    def recover(self):
        from entry_monitor import Monitor
        from order_manager import OrderManager
        monitor=Monitor(self)
        for row in self.store.unfinished():
            if row['phase']=='watching':
                monitor.recover_wait(row);continue
            observation=OrderManager(self).reconcile(row)
            if not observation.get('resolved'):
                self.store.set('paused','uncertain_execution');return False
        return not self.store.get('paused')
    def tick(self):
        if self.store.get('paused') or (self.store.path.parent/'STOP').exists():return None
        from entry_monitor import Monitor
        waiting=Monitor(self).poll()
        if self.config.get('plan_new') is False:return waiting
        with self.store.db() as db:watched={r[0] for r in db.execute("SELECT coin FROM entry_watches WHERE state='watching'")}
        available=[c for c in self.config['coins'] if c not in watched]
        if not available or self.clock()<self.store.get('next_due',0):return waiting
        ident=self.store.create(self.config);self.store.set('progress',{'cycle':ident,'phase':'reading','at':self.clock()})
        # Deadline is journaled before slow I/O; the process supervisor enforces it.
        try:
            coin=available[self.store.get('coin_cursor',0)%len(available)]
            snapshot=self.reader(coin)
            if not 0<=self.clock()-snapshot['retrieved_at']<=45:raise TradingError('stale_market_data')
            if hasattr(self.broker,'mark'):self.broker.mark(snapshot)
            account=self.broker.account(snapshot)
            if (self.config.get('execution_policy')!='exchange' or self.config.get('loss_limit_enabled',False)) and dec(account['baseline'])-dec(account['equity'])>=dec(self.config['max_loss_usd']):
                self.store.set('paused','loss_stop');raise TradingError('loss_stop')
            self.phase(ident,'observed',snapshot=snapshot,account=account)
            self.store.set('progress',{'cycle':ident,'phase':'planning','at':self.clock()})
            proposed=self.planner(snapshot,account,self.config,self.store.recent())
            telemetry=getattr(self.planner,'telemetry',None)
            if isinstance(telemetry,dict) and telemetry:self.store.set('last_inference',telemetry)
            from trade_plan import TradePlan,PlanError
            try:typed=TradePlan.parse(proposed)
            except PlanError as exc:raise TradingError(str(exc)) from None
            proposed=typed.to_dict()
            self.phase(ident,'planned',proposal=proposed,typed_plan=typed.envelope(),inference=telemetry if isinstance(telemetry,dict) else None,
                       proposal_source=telemetry.get('strategy',{'kind':'injected'}) if isinstance(telemetry,dict) else {'kind':'injected'})
            if isinstance(proposed,dict) and proposed.get('decision')=='watch':
                Monitor(self).arm(ident,proposed,snapshot)
                self.store.set('failures',0);self.store.set('next_due',self.clock()+self.config['interval'])
                return ident
            plan=validate_plan(proposed,snapshot,account,self.config,self.store,self.clock())
            if plan['decision']=='hold':
                self.phase(ident,'done',result={'state':'hold','reason':plan['reason']})
            else:
                if self.config.get('entry_mode')=='conditional':raise TradingError('conditional_entry_required')
                from order_manager import OrderManager
                OrderManager(self).dispatch(ident,typed,snapshot,account)
            self.store.set('failures',0);self.store.set('next_due',self.clock()+self.config['interval'])
        except Exception as error:
            from secret_output import redact
            if str(error).split(':',1)[0] in ('local_service_grant_required_or_expired','grant_service_scope_mismatch','service_policy_exceeds_grant','unprotected_position_requires_review'):
                self.store.set('paused',str(error).split(':',1)[0])
            failures=self.store.get('failures',0)+1;self.store.set('failures',failures)
            delay=min(900,self.config['interval']*2**min(failures,4));self.store.set('next_due',self.clock()+delay)
            self.store.event('cycle_error',ident,{'type':type(error).__name__,'reason':redact(str(error))[:800],'retry_after_seconds':delay})
            # If the journal passed the external-effect boundary, reconcile once.
            unfinished=next((r for r in self.store.unfinished() if r['id']==ident),None)
            if unfinished:
                from order_manager import OrderManager
                OrderManager(self).reconcile(unfinished,error)
        finally:
            self.store.set('coin_cursor',self.store.get('coin_cursor',0)+1)
            self.store.set('progress',{'cycle':ident,'phase':'idle','at':self.clock()})
        return ident
