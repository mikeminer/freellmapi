"""Durable MCC-style conditional entry lifecycle, independent of model uptime.

Only the existing broker may submit. Waiting/recovery never calls a model or
replays an entry. A threshold is defined on a fresh observed bid/ask midpoint.
"""
import copy,json,time
from decimal import Decimal
from trading_engine import TradingError,dec,number,fingerprint,validate_plan

INTERVALS={'1m':60,'5m':300,'15m':900,'1h':3600,'4h':14400}
TERMINAL={'done','expired','invalidated','cancelled','abandoned','paused'}


def public_status(store,now=None):
    now=time.time() if now is None else now
    with store.db() as db:rows=db.execute('SELECT * FROM entry_watches ORDER BY created DESC LIMIT 30').fetchall()
    watches=[]
    for row in rows:
        body=json.loads(row['body']);watch=body.get('watch',{});plan=watch.get('plan',{})
        watches.append({'id':row['id'],'coin':row['coin'],'state':row['state'],'side':plan.get('side'),'trigger':plan.get('trigger'),
            'take_profit':plan.get('take_profit'),'stop_loss':plan.get('stop_loss'),'expires_at':watch.get('expires_at'),
            'remaining_seconds':max(0,int(watch.get('expires_at',now)-now)),'last_observation':watch.get('last_quote'),
            'confirmation_state':watch.get('confirmation_state'),'read_failures':watch.get('read_failures',0),'result':body.get('result'),
            'operation_id':(body.get('receipt') or {}).get('operation_id')})
    return {'price_source':'fresh bid/ask midpoint','active':sum(w['state']=='watching' for w in watches),'watches':watches,
            'note':'Waiting is not an exchange order. No model calls while waiting. Expired/invalidated plans never execute; unknown sends pause without replay.'}


def validate_trigger(plan,snapshot,config):
    if not isinstance(plan,dict) or plan.get('decision')!='watch':raise TradingError('watch_decision_required')
    missing=sorted({'coin','side','size','entry','take_profit','stop_loss','leverage','reason','trigger'}-set(plan))
    if missing:raise TradingError('missing_watch_fields:'+','.join(missing))
    t=plan.get('trigger')
    if not isinstance(t,dict) or set(t)-{'kind','direction','level','confirmation','interval','ttl_seconds','retest_tolerance_bps'}:raise TradingError('invalid_entry_trigger_fields')
    if t.get('kind') not in ('breakout','pullback') or t.get('direction') not in ('above','below'):raise TradingError('invalid_entry_trigger')
    if t.get('confirmation') not in ('touch','close','retest'):raise TradingError('invalid_confirmation')
    if t['confirmation']=='retest' and t['kind']!='breakout':raise TradingError('retest_requires_breakout')
    expected='above' if (plan.get('side')=='buy')==(t['kind']=='breakout') else 'below'
    if t['direction']!=expected:raise TradingError('trigger_direction_side_mismatch')
    if dec(t.get('level'))<=0 or dec(t['level'])!=dec(plan.get('entry')):raise TradingError('trigger_level_must_equal_reference_entry')
    if type(t.get('ttl_seconds')) is not int or not 60<=t['ttl_seconds']<=config.get('max_watch_seconds',86400):raise TradingError('invalid_watch_ttl')
    if t['confirmation']=='close' and t.get('interval') not in INTERVALS:raise TradingError('close_interval_required')
    tolerance=t.get('retest_tolerance_bps',config['max_slippage_bps'])
    if type(tolerance) is not int or not 1<=tolerance<=config['max_slippage_bps']:raise TradingError('invalid_retest_tolerance')
    mid=dec(snapshot['mid']);level=dec(t['level'])
    if (mid>=level if t['direction']=='above' else mid<=level):raise TradingError('trigger_already_reached_do_not_chase')
    if (mid<=dec(plan['stop_loss']) if plan['side']=='buy' else mid>=dec(plan['stop_loss'])):raise TradingError('setup_already_invalidated')


class Monitor:
    def __init__(self,engine):self.e=engine;self.store=engine.store;self.config=engine.config;self.clock=engine.clock
    def scope(self):
        if hasattr(self.e.broker,'watch_scope'):return self.e.broker.watch_scope()
        return {'mode':self.config['mode'],'profile':self.config['profile'],'network':self.config['network']}
    def evidence(self,snapshot):
        analysis=snapshot.get('analysis') or {};now=self.clock()
        if (analysis.get('data_ready') is not True or analysis.get('coin')!=snapshot.get('coin')
                or analysis.get('profile')!=self.config['profile'] or analysis.get('network')!=self.config['network']
                or not -1<=now-analysis.get('as_of_ms',0)/1000<=45 or len(analysis.get('timeframes',{}))<2):
            raise TradingError('fresh_scoped_analyst_evidence_required')
        return analysis
    def arm(self,ident,plan,snapshot):
        from trade_plan import TradePlan,PlanError
        try:typed=TradePlan.parse(plan)
        except PlanError as exc:raise TradingError(str(exc)) from None
        plan=typed.to_dict()
        validate_trigger(plan,snapshot,self.config)
        self.evidence(snapshot)
        scope=self.scope();now=self.clock()
        account=self.e.broker.account(snapshot)
        validate_plan({**plan,'decision':'enter'},snapshot,account,self.config,self.store,now,allow_future=True)
        # Reason/TTL changes cannot disguise the same economic setup as new work.
        canonical={k:v for k,v in plan.items() if k not in ('reason','decision')};canonical=copy.deepcopy(canonical);canonical['trigger'].pop('ttl_seconds',None)
        setup_hash=fingerprint({'scope':scope,'policy':fingerprint(self.config),'setup':canonical})
        expires=now+plan['trigger']['ttl_seconds']
        if scope.get('expires') is not None:expires=min(expires,scope['expires'])
        watch={'plan':copy.deepcopy(plan),'armed_at':now,'expires_at':expires,'scope':scope,'next_check':now,
               'last_quote':{'mid':snapshot['mid'],'retrieved_at':snapshot['retrieved_at']},'confirmation_state':'waiting','read_failures':0}
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT id FROM entry_watches WHERE setup_hash=?',(setup_hash,)).fetchone()
            if old:raise TradingError('duplicate_setup_already_recorded:'+old['id'])
            if db.execute("SELECT id FROM entry_watches WHERE coin=? AND state='watching'",(plan['coin'],)).fetchone():raise TradingError('coin_already_watched')
            row=db.execute('SELECT body FROM cycles WHERE id=?',(ident,)).fetchone();body=json.loads(row[0]);body['watch']=watch;body['typed_plan']=typed.envelope()
            db.execute('INSERT INTO entry_watches VALUES(?,?,?,?,?,?,?)',(ident,setup_hash,plan['coin'],'watching',now,now,json.dumps(body)))
            db.execute("UPDATE cycles SET phase='watching',updated=?,body=? WHERE id=?",(now,json.dumps(body),ident))
        self.store.event('watch_armed',ident,{'coin':plan['coin'],'trigger':plan['trigger'],'expires_at':expires})
        self.e.hook('watching')
        return ident
    def end(self,row,state,reason):
        from secret_output import redact
        reason=redact(reason)
        self.store.phase(row['id'],state,watch=row['body'].get('watch'),result={'state':state,'reason':reason,'resent':False})
        self.store.event('watch_'+state,row['id'],{'reason':reason})
    def recover_wait(self,row):
        watch=row['body'].get('watch',{})
        if row['body'].get('policy_hash')!=fingerprint(self.config):self.end(row,'invalidated','configuration_changed');return
        if self.clock()>=watch.get('expires_at',0):self.end(row,'expired','watch_expired');return
        try:
            if watch.get('scope')!=self.scope():self.end(row,'invalidated','authority_changed')
        except Exception:
            self.store.set('paused','local_service_grant_required_or_expired')
    def condition(self,watch,quote):
        plan=watch['plan'];trigger=plan['trigger'];mid=dec(quote['mid']);level=dec(trigger['level']);above=trigger['direction']=='above'
        hit=mid>=level if above else mid<=level
        if trigger['confirmation']=='touch':return hit
        if trigger['confirmation']=='close':
            interval=trigger['interval'];bar=(quote.get('closed_candles') or {}).get(interval)
            if not isinstance(bar,dict):return False
            end=bar.get('T');now=self.clock()*1000
            if type(end) is not int or not watch['armed_at']*1000<end<now or now-end>(INTERVALS[interval]+5)*1000:return False
            close=dec(bar.get('c'));return (close>=level if above else close<=level) and hit
        if not watch.get('breakout_seen'):
            if hit:watch.update(breakout_seen=True,breakout_at=quote['retrieved_at'],confirmation_state='waiting_retest')
            return False
        tolerance=dec(trigger.get('retest_tolerance_bps',self.config['max_slippage_bps']))/10000
        if quote['retrieved_at']<=watch.get('breakout_at',0):return False
        if (mid<level*(1-tolerance) if above else mid>level*(1+tolerance)):raise TradingError('breakout_failed_before_retest')
        return abs(mid-level)/level<=tolerance
    def poll(self):
        with self.store.db() as db:
            rows=[dict(r,body=json.loads(r['body'])) for r in db.execute("SELECT * FROM cycles WHERE phase='watching' ORDER BY created")]
        if not rows:return None
        for row in rows:
            self.recover_wait(row)
            if self.store.get('paused'):return row['id']
            with self.store.db() as db:current=db.execute('SELECT phase FROM cycles WHERE id=?',(row['id'],)).fetchone()
            if current['phase']!='watching':continue
            watch=row['body']['watch'];now=self.clock()
            if now<watch.get('next_check',0):continue
            try:
                quote=self.e.quote_reader(watch['plan']['coin']);now=self.clock()
                if not isinstance(quote,dict) or quote.get('coin')!=watch['plan']['coin'] or not -1<=now-quote['retrieved_at']<=10:raise TradingError('stale_trigger_quote')
                bid,ask=dec(quote['bid']),dec(quote['ask']);mid=dec(quote['mid'])
                if bid<=0 or ask<bid or mid!=(bid+ask)/2 or (ask-bid)/mid*10000>self.config['max_spread_bps']:raise TradingError('invalid_trigger_book')
                watch['read_failures']=0;watch['next_check']=now+self.config['watch_poll_seconds']
                if quote['retrieved_at']<=watch.get('last_quote',{}).get('retrieved_at',0):
                    self.store.phase(row['id'],'watching',watch=watch);continue
                watch['last_quote']={k:quote[k] for k in ('mid','bid','ask','retrieved_at')}
                plan=watch['plan']
                if (mid<=dec(plan['stop_loss']) if plan['side']=='buy' else mid>=dec(plan['stop_loss'])):
                    self.end(row,'invalidated','invalidation_reached_before_entry');continue
                if not self.condition(watch,quote):self.store.phase(row['id'],'watching',watch=watch);continue
                if not self.store.transition(row['id'],'watching','triggering',watch=watch):continue
                self.e.hook('triggering')
                self.fire(row['id'],watch)
            except Exception as error:
                if str(error)=='loss_stop':self.store.set('paused','loss_stop')
                # If claim/dispatch started, use existing bounded recovery; never re-arm.
                with self.store.db() as db:current=db.execute('SELECT phase FROM cycles WHERE id=?',(row['id'],)).fetchone()
                if current['phase']!='watching':
                    from order_manager import OrderManager
                    unfinished=next((r for r in self.store.unfinished() if r['id']==row['id']),None)
                    if unfinished:
                        observed=OrderManager(self.e).reconcile(unfinished,error)
                        if observed.get('resolved') and not observed.get('receipt'):self.end(row,'invalidated',type(error).__name__+':'+str(error)[:250])
                else:
                    watch['read_failures']=watch.get('read_failures',0)+1;watch['next_check']=now+min(60,5*2**watch['read_failures'])
                    if isinstance(error,TradingError) and str(error)=='breakout_failed_before_retest':self.end(row,'invalidated',str(error))
                    elif watch['read_failures']>=3:
                        watch.update(read_failures=3,next_check=now+60,confirmation_state='waiting_data')
                        self.store.phase(row['id'],'watching',watch=watch)
                    else:self.store.phase(row['id'],'watching',watch=watch)
                self.store.event('watch_error',row['id'],{'type':type(error).__name__,'reason':str(error)[:250],'resent':False})
        return rows[0]['id']
    def fire(self,ident,watch):
        if (self.store.path.parent/'STOP').exists():raise TradingError('service_stopped')
        plan=watch['plan']
        snapshot=self.e.reader.for_watch(plan['coin'],plan['trigger']) if hasattr(self.e.reader,'for_watch') else self.e.reader(plan['coin'])
        now=self.clock()
        if now>=watch['expires_at'] or self.scope()!=watch['scope']:raise TradingError('watch_expired_or_authority_changed')
        if not self.condition(copy.deepcopy(watch),snapshot):raise TradingError('trigger_no_longer_confirmed')
        reference=dec(plan['entry']);actual=dec(snapshot['ask'] if plan['side']=='buy' else snapshot['bid'])
        if abs(actual-reference)/reference*10000>self.config['max_trigger_gap_bps']:raise TradingError('trigger_gap_do_not_chase')
        analysis=self.evidence(snapshot)
        regimes=[v.get('regime') for v in analysis.get('timeframes',{}).values()]
        opposite='down' if plan['side']=='buy' else 'up'
        if self.config['require_trend_alignment'] and (len(regimes)<2 or all(r==opposite for r in regimes)):raise TradingError('opposing_or_missing_timeframe_regime')
        account=self.e.broker.account(snapshot)
        def pre_send():
            if self.clock()>=watch['expires_at'] or self.scope()!=watch['scope']:raise TradingError('watch_expired_or_authority_changed')
        from order_manager import OrderManager
        OrderManager(self.e).dispatch(ident,{**plan,'decision':'enter','entry':number(actual)},snapshot,account,pre_send,
                                     {'trigger_snapshot':snapshot,'trigger_account':account})
