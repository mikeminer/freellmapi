"""Persistent Pump live coordinator, disabled until local authorization. Native exits continue while inference runs."""
import json,os,subprocess,sys,time,uuid,threading
from decimal import Decimal
from pathlib import Path
import pump_local as p
import solana_local as sol

class Scout:
    """Public event subprocess; bounded rows, explicit gaps, no holder inference."""
    def __init__(self,home,cfg):self.home=Path(home);self.cfg=cfg;self.stop=threading.Event();self.child=None
    def start(self):self.thread=threading.Thread(target=self.run,daemon=True,name='pump-public-scout');self.thread.start();return self
    def run(self):
        failures=0
        while not self.stop.is_set() and failures<3:
            try:
                profile=p.profile(self.home,self.cfg['profile']);env={k:v for k,v in os.environ.items() if k.upper() in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP','USERPROFILE','LOCALAPPDATA','NODE_EXTRA_CA_CERTS')};env['NODE_USE_SYSTEM_CA']='1'
                self.child=subprocess.Popen([__import__('shutil').which('node'),str(p.SDK/'scout.mjs')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                self.child.stdin.write(json.dumps({'rpc_url':profile['rpc_url'],'network':profile['network'],'mints':self.cfg['mints']})+'\n');self.child.stdin.flush();batch=[];last=time.monotonic();events=0
                for line in self.child.stdout:
                    if self.stop.is_set():break
                    if len(line)>10000:continue
                    item=json.loads(line);kind=item.get('kind');now=time.time()
                    if kind in ('gap','heartbeat'):
                        p.set_state(self.home,'scout',{'at':now,'kind':kind,'events_seen':events,'socket_open':item.get('socket_open'),
                            'coverage':'observed stream only, gaps possible','reason':item.get('reason')})
                    else:batch.append(item);events+=1
                    if batch and (time.monotonic()-last>=1 or len(batch)>=100):
                        with p.db(self.home) as d:
                            for row in batch:
                                d.execute('INSERT OR IGNORE INTO market_events VALUES(?,?,?,?)',(row['id'],row['mint'],row['observed_ms']/1000,json.dumps(row)))
                                if row['kind']=='CreateEvent':d.execute('INSERT OR IGNORE INTO candidates VALUES(?,?,?)',(row['mint'],now,json.dumps(row)))
                            d.execute('DELETE FROM market_events WHERE id NOT IN (SELECT id FROM market_events ORDER BY time DESC LIMIT 5000)')
                            d.execute('DELETE FROM candidates WHERE created<?',(now-900,))
                        batch=[];last=time.monotonic()
                failures+=1
            except Exception:failures+=1;p.set_state(self.home,'scout',{'at':time.time(),'kind':'gap','reason':'subscription_unavailable','failures':failures})
            finally:
                if self.child and self.child.poll() is None:self.child.terminate();self.child.wait(timeout=5)
            self.stop.wait(min(60,5*failures))
    def close(self):
        self.stop.set()
        if self.child and self.child.poll() is None:self.child.terminate()

DEFAULT={'mode':'disabled','profile':None,'mints':[],'allow_discovery':False,
    'trade_sol':'0.005','take_profit_pct':'50','stop_loss_pct':'25','interval':60,'route':'auto:reliable',
    'max_positions':3,'slippage_bps':100}
def load(home):
    path=p.directory(home)/'service.json'
    value=json.loads(path.read_text()) if path.exists() else {**DEFAULT,'profile':p.profile(home)['name']}
    if value.get('mode')=='paper':value['mode']='disabled'
    return {k:v for k,v in value.items() if not k.startswith('paper_') and k!='max_paper_total_sol'}
def validate(cfg):
    if cfg.get('mode') not in ('disabled','live'):p.fail('service_mode_required')
    p.amount(cfg['trade_sol'])
    p.integer(cfg['interval'],60,3600);p.integer(cfg['max_positions'],1,8);p.integer(cfg['slippage_bps'],1,500)
    for mint in cfg['mints']:sol.pubkey(mint)
    if not isinstance(cfg['route'],str) or not cfg['route'] or len(cfg['route'])>200:p.fail('model_route_required')
    for k in ('take_profit_pct','stop_loss_pct'):
        n=Decimal(str(cfg[k]))
        if not n.is_finite() or n<=0 or n>(99 if k=='stop_loss_pct' else 1000):p.fail('invalid_protection_percent')
    return cfg
def configure_local(home,cfg):
    from trading_runtime import singleton
    cfg=validate(dict(cfg));p.profile(home,cfg['profile'])
    if cfg['mode']=='live':
        # A stored configuration never manufactures authority.
        state=p.status(home,cfg['profile'])
        if state['profile']!=cfg['profile'] or not state['grant_active']:p.fail('local_pump_grant_required_or_expired')
    with singleton(p.directory(home)/'supervisor.lock'),singleton(p.directory(home)/'worker.lock'):
        p.atomic(p.directory(home)/'service.json',cfg)
    return {'configured':True,'mode':cfg['mode'],'started':False,'grant_created':False}

def authorize_config_local(home,cfg,settings):
    """Exact local GUI consent only; config failure rolls back grant/adoption."""
    from trading_runtime import singleton
    from reliable_runtime import atomic_bytes
    cfg=validate(dict(cfg))
    if cfg['mode']!='live':p.fail('explicit_live_config_required')
    p.limits(settings);path=p.directory(home)/'service.json'
    with singleton(p.directory(home)/'supervisor.lock'),singleton(p.directory(home)/'worker.lock'),singleton(p.directory(home)/'execution.lock'):
        previous=path.read_bytes() if path.exists() else None
        try:
            result=p.authorize_local(home,cfg['profile'],settings,persist_config=lambda:p.atomic(path,cfg))
        except Exception:
            if previous is None:path.unlink(missing_ok=True)
            else:atomic_bytes(path,previous)
            raise
        # Only this fresh local consent clears the entry stop.
        for marker in ('STOP','ENTRY_STOP'):(p.directory(home)/marker).unlink(missing_ok=True)
    return {**result,'configured':True,'started':False}

def resume_entries_local(home):
    from trading_runtime import singleton
    cfg=load(home)
    with singleton(p.directory(home)/'execution.lock'):
        if (p.directory(home)/'STOP').exists():p.fail('all_actions_stopped_reauthorize_locally')
        if cfg['mode']=='live':
            public=p.profile(home,cfg['profile'])
            with p.db(home) as d:
                r=d.execute('SELECT body FROM grants WHERE profile=?',(cfg['profile'],)).fetchone();g=json.loads(r[0]) if r else None
                if not g or not g['active'] or g['expires']<=time.time() or g['scope']!=p.scope(public):p.fail('local_pump_grant_required_or_expired')
                g['entry_stop']=False;d.execute('UPDATE grants SET body=? WHERE profile=?',(json.dumps(g),cfg['profile']))
        (p.directory(home)/'ENTRY_STOP').unlink(missing_ok=True)
    return {'new_entries_stopped':False,'limits_changed':False,'grant_created':False,'sent':False}

def lock_held(path):
    from trading_runtime import singleton
    try:
        with singleton(path):return False
    except OSError:return True
def service_status(home):
    cfg=load(home);hb=p.get_state(home,'service',{})
    age=time.time()-hb.get('heartbeat',0)
    with p.db(home) as d:
        positions=[json.loads(x[0]) for x in d.execute("SELECT body FROM positions WHERE state='open' AND mode=? AND profile=?",(cfg['mode'],cfg['profile']))]
        events=[{'at':x['time'],'kind':x['kind'],'data':json.loads(x['body'])} for x in d.execute('SELECT * FROM events ORDER BY id DESC LIMIT 12')]
    worker_alive=lock_held(p.directory(home)/'worker.lock');supervisor_alive=lock_held(p.directory(home)/'supervisor.lock')
    state='running' if worker_alive and age<20 else 'unresponsive' if worker_alive else 'recovering' if supervisor_alive else 'not_started' if not hb else 'stopped'
    return {'config':cfg,'heartbeat_age_seconds':int(age) if hb else None,'state':state,'worker_lock_held':worker_alive,'supervisor_lock_held':supervisor_alive,
        'activity':hb,'positions':positions,
        'events':events,'new_entries_stopped':(p.directory(home)/'ENTRY_STOP').exists(),
        'scout':p.get_state(home,'scout',{'kind':'not_started'}),
        'roles':{'scout':'public confirmed WS events, bounded queue and RPC samples','analyst':'one model process, read only','curve':'official curve SDK',
            'amm':'official PumpSwap SDK','risk':'native wallet/grant/fee checks','orders':'single native executor',
            'protection':'client-side TP/SL loop','recovery':'persistent signature reconciliation; no replay'},
        'note':'Roles are pipeline components, not eight independent funded models. No high-frequency throughput or guaranteed profitability claimed.'}

def start(home):
    from trading_runtime import singleton
    cfg=load(home)
    try:
        with singleton(p.directory(home)/'supervisor.lock'):pass
    except OSError:return {'already_running':True,'status':service_status(home)}
    if cfg['mode']!='live':p.fail('local_pump_grant_required_or_service_disabled')
    if cfg['mode']=='live' and not p.status(home,cfg['profile'])['grant_active']:p.fail('local_pump_grant_required_or_expired')
    if not cfg['mints'] and not cfg['allow_discovery']:p.fail('configure_watchlist_or_discovery_locally_first')
    if p.get_state(home,'paused'):p.fail('service_paused_requires_local_review')
    (p.directory(home)/'STOP').unlink(missing_ok=True)
    env=dict(os.environ)
    with (p.directory(home)/'service.log').open('a',encoding='utf-8') as log:
        child=subprocess.Popen([sys.executable,'-X','utf8',str(Path(__file__).resolve()),'--home',str(Path(home).resolve())],stdin=subprocess.DEVNULL,stdout=log,stderr=log,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return {'started':True,'pid':child.pid,'mode':cfg['mode'],'grant_created':False,'real_orders_submitted_synchronously':0}
def command(home,args):
    action=args.get('action','status')
    if action=='status':return service_status(home)
    if action=='start':return start(home)
    if action=='stop_entries':return p.stop(home)
    if action=='stop':return p.stop(home,True)
    p.fail('service_action_status_start_stop_entries_stop')

class Engine:
    def __init__(self,home,cfg,clock=time.time,planner=None):
        self.home=Path(home);self.cfg=validate(dict(cfg));self.clock=clock;self.pending=None;self.result=None;self.planner=planner or self.infer
        self.busy=False;self.next_plan=p.get_state(home,'next_plan',0);self.last_exit=0;self.mint_index=0
        if cfg['mode']!='live':p.fail('service_disabled_requires_local_live_configuration')
    def phase(self,phase,**data):p.set_state(self.home,'service',{'state':'running','phase':phase,'heartbeat':self.clock(),**data})
    def positions(self):
        with p.db(self.home) as d:return [json.loads(x[0]) for x in d.execute("SELECT body FROM positions WHERE state='open' AND mode=? AND profile=?",(self.cfg['mode'],self.cfg['profile']))]
    def infer(self,evidence):
        base=Path(sys.base_prefix)/('python.exe' if os.name=='nt' else 'bin/python3');env=dict(os.environ);env['FREELLMAPI_READ_ONLY']='1'
        with p.db(self.home) as d:history=[{'id':r['id'],'kind':r['kind'],'data':json.loads(r['body'])} for r in d.execute('SELECT * FROM events ORDER BY id DESC LIMIT 4')]
        request={'home':str(self.home),'evidence':evidence,'policy':self.cfg,'history':history,'route':self.cfg['route']}
        result=subprocess.run([str(base),'-X','utf8',str(Path(__file__).with_name('pump_planner.py'))],input=json.dumps(request),capture_output=True,text=True,encoding='utf-8',timeout=40,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        value=json.loads(result.stdout)
        if value.get('error'):p.fail('planner_unavailable')
        return value
    def launch_plan(self,mint):
        self.busy=True;self.result=None;self.pending=mint
        def work():
            try:
                evidence=p.analyze(self.home,{'profile':self.cfg['profile'],'mint':mint})
                if not evidence['data_ready']:value={'plan':{'decision':'hold','reason':', '.join(evidence['blockers'])},'model':None}
                else:
                    evidence['backtest']=p.backtest(self.home,{'mint':mint});value=self.planner(evidence)
                self.result={'id':uuid.uuid4().hex,'mint':mint,'value':value,'created':self.clock()}
            except Exception as e:self.result={'mint':mint,'error':type(e).__name__,'created':self.clock()}
            finally:self.busy=False
        threading.Thread(target=work,daemon=True,name='pump-readonly-analyst').start()
    def quote_body(self,mint,side,quantity=None):
        args={'profile':self.cfg['profile'],'mint':mint,'side':side,'slippage_bps':self.cfg['slippage_bps']}
        if side=='buy':args['amount_sol']=self.cfg['trade_sol']
        else:
            snapshot=p.inspect(self.home,{'profile':self.cfg['profile'],'mint':mint});args['token_amount']=str(Decimal(quantity)/(10**snapshot['decimals']))
        quoted=p.quote(self.home,args)
        _,body=p.load_quote(self.home,quoted['quote_id']);body.update(take_profit_pct=self.cfg['take_profit_pct'],stop_loss_pct=self.cfg['stop_loss_pct'])
        body['maximum_debit_lamports']=(int(body['max_input']) if side=='buy' else 0)+body['fee_lamports']+body['rent_buffer_lamports'];return body,quoted['quote_id']
    def prepare_execute(self,ident):
        prepared=p.prepare(self.home,{'quote_id':ident,'take_profit_pct':self.cfg['take_profit_pct'],'stop_loss_pct':self.cfg['stop_loss_pct']})
        return p.execute(self.home,prepared['id'])
    def protect(self):
        for position in self.positions():
            try:
                body,ident=self.quote_body(position['mint'],'sell',position['quantity'])
                proceeds=Decimal(body['expected_output'])-body['fee_lamports'];cost=Decimal(position['cost_lamports'])
                hit=proceeds>=cost*(1+Decimal(position['take_profit_pct'])/100) or proceeds<=cost*(1-Decimal(position['stop_loss_pct'])/100)
                if not hit:continue
                self.phase('protecting',mint=position['mint'])
                self.prepare_execute(ident)
            except Exception as e:p.event(self.home,'protection_wait',{'mint':position['mint'],'reason':str(e) if isinstance(e,p.PumpError) else type(e).__name__,'resent':False})
    def recover(self):
        # Sending/uncertain/submitted operations are ONLY read, never replayed.
        if self.cfg['mode']!='live':return
        with sol.database(self.home) as d:rows=d.execute("SELECT id,body FROM operations WHERE state IN ('sending','uncertain','submitted')").fetchall()
        for row in rows:
            body=json.loads(row['body'])
            if body.get('protocol')!='pumpfun' or body.get('profile')!=self.cfg['profile']:continue
            try:p.reconcile(self.home,row['id'])
            except Exception:p.event(self.home,'reconciliation_wait',{'id':row['id'],'resent':False})
    def tick(self):
        self.phase('monitoring',analysis_in_background=self.busy);now=self.clock()
        if now-self.last_exit>=15:self.recover();self.protect();self.last_exit=now
        stopped=(p.directory(self.home)/'ENTRY_STOP').exists()
        if self.result is not None:
            result=self.result;self.result=None;p.event(self.home,'analysis',result)
            plan=result.get('value',{}).get('plan',{})
            if not stopped and plan.get('decision')=='buy' and now-result['created']<30 and not any(x['mint']==result['mint'] for x in self.positions()):
                try:
                    body,ident=self.quote_body(result['mint'],'buy')
                    self.prepare_execute(ident)
                except Exception as e:p.event(self.home,'entry_blocked',{'mint':result['mint'],'reason':str(e) if isinstance(e,p.PumpError) else type(e).__name__,'resent':False})
        if stopped or self.busy or now<self.next_plan:return
        self.next_plan=now+self.cfg['interval'];p.set_state(self.home,'next_plan',self.next_plan)
        mints=list(self.cfg['mints'])
        if self.cfg['allow_discovery']:
            with p.db(self.home) as d:known=d.execute('SELECT mint FROM candidates WHERE created>? ORDER BY created DESC LIMIT 30',(now-900,)).fetchall()
            mints.extend(x[0] for x in known)
            try:
                if not known:mints.extend(x['mint'] for x in p.discover(self.home,{'profile':self.cfg['profile'],'limit':10})['coins'])
            except Exception:p.event(self.home,'scout_wait',{'reason':'RPC unavailable','resent':False})
        mints=list(dict.fromkeys(mints));owned={x['mint'] for x in self.positions()};mints=[m for m in mints if m not in owned]
        if mints:self.launch_plan(mints[self.mint_index%len(mints)]);self.mint_index+=1

def worker(home):
    from trading_runtime import singleton,keep_awake
    with singleton(p.directory(home)/'worker.lock'),keep_awake():
        cfg=load(home);engine=Engine(home,cfg);engine.recover();scout=Scout(home,cfg).start() if cfg['allow_discovery'] or cfg['mints'] else None
        if cfg['mode']!='live':return
        while not (p.directory(home)/'STOP').exists():
            try:engine.tick()
            except Exception as e:p.event(home,'cycle_wait',{'reason':type(e).__name__,'resent':False})
            time.sleep(3)
        p.set_state(home,'service',{'state':'stopped','heartbeat':time.time()})
        if scout:scout.close()
def supervise(home):
    from trading_runtime import singleton
    with singleton(p.directory(home)/'supervisor.lock'):
        failures=p.get_state(home,'crashes',[]);failures=[x for x in failures if time.time()-x<3600]
        while not (p.directory(home)/'STOP').exists():
            if len(failures)>=3:p.set_state(home,'paused','crash_circuit_open');return
            child=subprocess.Popen([sys.executable,'-X','utf8',str(Path(__file__).resolve()),'--home',str(home),'--worker'],stdin=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            while child.poll() is None:
                if (p.directory(home)/'STOP').exists():
                    try:child.wait(timeout=25)
                    except subprocess.TimeoutExpired:child.terminate();child.wait(timeout=8)
                    return
                time.sleep(2)
            if child.returncode==0:return
            failures.append(time.time());p.set_state(home,'crashes',failures);time.sleep(min(30,5*len(failures)))
if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--home',required=True);parser.add_argument('--worker',action='store_true');a=parser.parse_args()
    worker(Path(a.home)) if a.worker else supervise(Path(a.home))
