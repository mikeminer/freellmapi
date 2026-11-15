"""Always-on per-user monitor with shared account authority; no virtual trading mode."""
import json,os,subprocess,sys,time
from pathlib import Path
from trading_engine import Store,policy,TradingError
from reliable_runtime import atomic_bytes

DEFAULT={'version':3,'mode':'live','network':'mainnet','profile':'mainnet-master','coins':['BTC'],
    'max_order_usd':'20','max_exposure_usd':'20','max_loss_usd':'10','max_turnover_usd':'200','max_leverage':1,
    'max_orders_hour':3,'max_positions':1,'interval':60,'max_spread_bps':50,'max_slippage_bps':20,
    'entry_mode':'conditional','watch_poll_seconds':1,'max_watch_seconds':86400,'max_trigger_gap_bps':20,'require_trend_alignment':True,
    'playbook':'Analyze closed candles on multiple timeframes, trend, volatility, spread, fees and funding. Plan future breakout/pullback levels with confirmation, expiry, TP/SL and opposing scenario; wait for the native monitor. Otherwise HOLD. Never promise a safe TP or profit.'}

def effective_home(home):
    # Resolve existing config through MSIX redirection before launching outside
    # the desktop app. Never copy/create a second credential database.
    from app_home import runtime_home as canonical_home
    return canonical_home(home)
def folder(home):p=effective_home(home)/'trading-service';p.mkdir(parents=True,exist_ok=True);return p.resolve()
def load(home):
    p=folder(home)/'config.json'
    raw=json.loads(p.read_text(encoding='utf-8')) if p.exists() else dict(DEFAULT)
    if raw.get('mode') in ('paper','disabled'):raw={**raw,'mode':'live'}
    raw.pop('initial_equity',None)
    value=policy(raw)
    import hyperliquid_tools as h
    from hyperliquid_authority import configured
    try:selected=h.profile(home,value['profile'])
    except h.HLProblem:selected={}
    if configured(selected):
        value.update(execution_policy='exchange',market_scope='all_listed_perpetuals',plan_new=False,
                     key_only_generation=selected['execution_authority']['generation'],loss_limit_enabled=selected['execution_authority'].get('loss_limit_usd') is not None)
    return value
def save(home,config):
    atomic_bytes(folder(home)/'config.json',json.dumps(policy(config),indent=2).encode())

def effective_policy(config):
    value=dict(config)
    if config.get('execution_policy')=='exchange':
        for key in ('coins','max_order_usd','max_exposure_usd','max_loss_usd','max_turnover_usd','max_leverage','max_orders_hour','max_positions'):value.pop(key,None)
        value.update(scan_coins=config['coins'],market_scope='all_listed_perpetuals',quantity_limit='exchange',leverage_limit='exchange',loss_limit_usd=None)
    return value

def status(home):
    directory=folder(home);config=load(home);path=directory/(config['mode']+'.sqlite')
    from trading_strategies import CATALOG
    source=config.get('strategy_source','ai')
    public_policy=effective_policy(config)
    state={'mode':config['mode'],'network':config['network'],'coins':config['coins'],'enabled':not (directory/'STOP').exists(),
        'authority_source':'shared_account','separate_service_authorization':False,'new_entries_stopped':(directory/'STOP').exists(),
        'strategy':{'kind':source,**CATALOG[source]},'pipeline':{'plan_schema':1,'order_manager':'unified','signer':'isolated_local_process'},
        'autostart':(directory/'autostart.json').exists(),'real_orders_allowed':False,
        'policy':None if config.get('execution_policy')=='exchange' else public_policy,
        'effective_policy':public_policy,'adapter_mode':'key_only' if config.get('execution_policy')=='exchange' else 'legacy'}
    if not path.exists():
        if config.get('execution_policy')=='exchange' and state['enabled']:
            import hyperliquid_tools as h
            from hyperliquid_authority import current
            state['real_orders_allowed']=bool((current(home,h.profile(home,config['profile'])) or {}).get('active'))
        return {**state,'state':'not_started'}
    store=Store(path);heartbeat=store.get('heartbeat',{});age=time.time()-heartbeat.get('at',0)
    from entry_monitor import public_status
    with store.db() as db:
        dispatches=[]
        for row in db.execute('SELECT * FROM order_dispatches ORDER BY created DESC LIMIT 12'):
            body=json.loads(row['body']);dispatches.append({'id':row['id'],'state':row['state'],'plan_hash':row['plan_hash'],'source':body.get('proposal_source'),
                'operation_id':(body.get('receipt') or {}).get('operation_id') or body.get('prepared')})
    alive=worker_running(home,config['profile'])
    return {**state,'state':'running' if alive and age<150 else 'unresponsive' if alive else 'stopped_or_stale',
        'monitor_running':alive,'heartbeat_stale':age>=20,'execution_paused':bool(store.get('paused')) or not state['enabled'],
        'heartbeat_age_seconds':int(age) if heartbeat else None,'heartbeat':heartbeat,'paused_reason':store.get('paused'),
        'progress':store.get('progress'),'next_due':store.get('next_due'),'failures':store.get('failures',0),'supervisor':store.get('supervisor'),
        'recent_cycles':store.recent(4),
        'last_inference':store.get('last_inference'),'real_orders_allowed':live_authorized(home,store,config),
        'entry_monitor':public_status(store),'entry_mode':config.get('entry_mode','conditional'),
        'order_manager':{'dispatches':dispatches,'recovery':'read_only_no_replay'},
        'note':('Connected key, account scope and exchange permissions are checked before every send; only new requests or native setups execute.' if config.get('execution_policy')=='exchange' else 'Live authorization is checked again before every send; /auto on does not authorize finance.')}

def live_authorized(home,store,config):
    if config['mode']!='live':return False
    if (folder(home)/'STOP').exists() or store.get('paused'):return False
    try:
        from trading_runtime import LiveBroker
        LiveBroker(home,store,config).grant();return True
    except Exception:return False

def worker_running(home,profile=None):
    from trading_runtime import singleton
    if profile is not None and load(home)['profile']!=profile:return False
    try:
        with singleton(folder(home)/'worker.lock'):return False
    except OSError:return True

def start(home,mode='live'):
    from trading_runtime import singleton
    if mode!='live':raise TradingError('paper_trading_removed_use_live_monitor')
    home=effective_home(home);directory=folder(home)
    try:
        with singleton(directory/'supervisor.lock'):pass
    except OSError:return {'already_running':True,'status':status(home)}
    config=load(home)
    if config['mode']!='live':raise TradingError('invalid_runtime_mode')
    # Starting the monitor neither manufactures authority nor clears an entry STOP.
    (directory/'SHUTDOWN').unlink(missing_ok=True)
    metadata=directory/'autostart.json'
    if metadata.exists() and json.loads(metadata.read_text()).get('method')=='scheduled_task':
        result=subprocess.run(['schtasks','/Run','/TN','freellmapi-TradingService'],capture_output=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if result.returncode:raise TradingError('scheduled_task_start_failed')
        return {'started':True,'via':'scheduled_task','mode':mode,'real_orders_enabled_by_start':False}
    log=(directory/'supervisor.log').open('a',encoding='utf-8')
    python=Path(sys.executable)
    if os.name=='nt' and python.with_name('pythonw.exe').exists():python=python.with_name('pythonw.exe')
    child=subprocess.Popen([str(python),str(Path(__file__).with_name('trading_runtime.py')),'--home',str(Path(home).resolve())],
        stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0));log.close()
    return {'started':True,'supervisor_pid':child.pid,'mode':mode,'real_orders_enabled_by_start':False}

def stop(home):
    directory=folder(home);atomic_bytes(directory/'STOP',str(time.time()).encode())
    config=load(home)
    if config['mode']=='live':
        import hyperliquid_tools as h
        # Out-of-band stop epoch blocks an already-started signer too.
        h.stop(home,config['profile'])
    return {'stopping':True,'new_orders_stopped':True,'positions_closed':False,'orders_cancelled':False}

def install(home):
    if os.name!='nt':raise TradingError('Windows per-user startup requires Windows')
    home=effective_home(home);directory=folder(home);python=Path(sys.executable).with_name('pythonw.exe')
    if not python.exists():python=Path(sys.executable)
    command=subprocess.list2cmdline([str(python),str(Path(__file__).with_name('trading_runtime.py')),'--home',str(Path(home).resolve())])
    quote=lambda x:"'"+str(x).replace("'","''")+"'"
    arguments=subprocess.list2cmdline([str(Path(__file__).with_name('trading_runtime.py')),'--home',str(Path(home).resolve())])
    script="$ErrorActionPreference='Stop'; $u=[Security.Principal.WindowsIdentity]::GetCurrent().Name; "+\
        '$a=New-ScheduledTaskAction -Execute '+quote(python)+' -Argument '+quote(arguments)+'; '+\
        '$t=New-ScheduledTaskTrigger -AtLogOn -User $u; '+\
        '$w=New-ScheduledTaskTrigger -Once -At (Get-Date).AddSeconds(10) -RepetitionInterval (New-TimeSpan -Minutes 1); '+\
        '$p=New-ScheduledTaskPrincipal -UserId $u -LogonType Interactive -RunLevel Limited; '+\
        '$s=New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; '+\
        "Register-ScheduledTask -TaskName 'freellmapi-TradingService' -Action $a -Trigger @($t,$w) -Principal $p -Settings $s -Force | Out-Null"
    try:
        result=subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',script],capture_output=True,timeout=25,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        method='scheduled_task' if result.returncode==0 else 'user_run_key'
    except (OSError,subprocess.TimeoutExpired):method='user_run_key'
    import winreg
    if method=='user_run_key':
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run') as key:winreg.SetValueEx(key,'freellmapiTradingService',0,winreg.REG_SZ,command)
    else:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run',0,winreg.KEY_SET_VALUE) as key:winreg.DeleteValue(key,'freellmapiTradingService')
        except FileNotFoundError:pass
    metadata={'command':command,'method':method,'trigger':'Windows user sign-in + 1 minute watchdog' if method=='scheduled_task' else 'Windows user sign-in','runs_as':'current user','survives_cmd_close':True,'supervisor_os_restart':method=='scheduled_task'}
    atomic_bytes(directory/'autostart.json',json.dumps(metadata).encode())
    return {'installed':True,**metadata,'requires_pc_awake':True,'mode':load(home)['mode']}

def uninstall(home):
    stop(home)
    atomic_bytes(folder(home)/'SHUTDOWN',b'explicit_uninstall')
    if os.name=='nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Run',0,winreg.KEY_SET_VALUE) as key:winreg.DeleteValue(key,'freellmapiTradingService')
        except FileNotFoundError:pass
        subprocess.run(['schtasks','/Delete','/TN','freellmapi-TradingService','/F'],capture_output=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    (folder(home)/'autostart.json').unlink(missing_ok=True)
    return {'uninstalled':True,'state_and_history_preserved':True}

def resume(home):
    config=load(home);store=Store(folder(home)/(config['mode']+'.sqlite'));reason=store.get('paused')
    if reason in ('loss_stop','uncertain_execution','execution_requires_reconciliation','local_service_grant_required_or_expired','grant_service_scope_mismatch','service_policy_exceeds_grant','unprotected_position_requires_review'):raise TradingError('review_required_'+reason)
    store.set('paused',None)
    if reason=='crash_circuit_open':atomic_bytes(folder(home)/'supervisor-restarts.json',b'[]')
    return start(home,config['mode'])


def monitor_preflight(home,coin=None):
    """Read saved scope/authority only: no market reads, arming or worker start."""
    if coin is not None and (not isinstance(coin,str) or not 1<=len(coin)<=40 or any(c.isspace() for c in coin)):
        raise TradingError('watch_coin_required')
    state=status(home);key_only=state.get('adapter_mode')=='key_only'
    config=load(home) if key_only else state['policy']
    allowed=(True if key_only else coin in config['coins']) if coin is not None else None
    blockers=[]
    if config['mode']!='live':blockers.append('service_disabled_authorization_required')
    if allowed is False:blockers.append('watch_coin_outside_service_policy')
    if config.get('entry_mode')!='conditional':blockers.append('conditional_service_policy_required')
    if state.get('paused_reason'):blockers.append('review_paused_service_before_arming')
    if config['mode']=='live' and not state.get('real_orders_allowed'):blockers.append('account_authority_missing_or_expired')
    return {'requested_coin':coin,'allowed_coins':['*'] if key_only else list(config['coins']),'market_scope':'all_listed_perpetuals' if key_only else 'legacy_list','adapter_mode':'key_only' if key_only else 'legacy','scan_coins':list(config['coins']),'coin_allowed':allowed,'mode':config['mode'],
        'network':config['network'],'profile':config['profile'],'entry_mode':config.get('entry_mode'),
        'service_state':state['state'],'enabled':state['enabled'],'paused_reason':state.get('paused_reason'),
        'real_orders_allowed':state['real_orders_allowed'],'blockers':blockers,'scope_ready':coin is not None and not blockers,
        'risk_limits':({'quantity':'exchange','leverage':'exchange','loss_limit_usd':None} if key_only else {k:config[k] for k in ('max_order_usd','max_exposure_usd','max_loss_usd','max_turnover_usd','max_leverage')}),
        'service_will_start_on_valid_watch':config['mode']=='live' and not blockers and not state['enabled'],'armed':False,'orders_submitted':0,
        'scope':'persistent_conditional_service','require_trend_alignment':config.get('require_trend_alignment',True),
        'instruction':('Read-only preflight. key_only uses the connected key and NEW native setups, without local financial caps, coin whitelist, expiry or another approval. scan_coins are preferences. Respect actual exchange constraints, account scope, explicit STOP and unresolved receipts.' if key_only else 'Preflight is read-only. The always-on monitor shares the account authorization and economic limits; there is no separate service LIVE approval. Respect STOP, account scope and expiry. Never infer authority from exchange leverage or create/renew financial authority.')}

def sync_account_policy(home,grant):
    """Called after the single existing ACCOUNT consent, never by a model."""
    config=load(home)
    config.update(mode='live',profile=grant['profile'],network=grant['network'],coins=list(grant['coins']),playbook=grant['strategy'],
        **{key:grant[key] for key in ('max_order_usd','max_exposure_usd','max_loss_usd','max_turnover_usd','max_leverage','max_orders_hour','interval')})
    save(home,config)
    (folder(home)/'STOP').unlink(missing_ok=True)
    return {'account_policy_shared':True,'separate_service_authorization':False,'service_started':False}


def monitor_problem(home,args,error):
    """Actionable native policy failures, preserving the original reason code."""
    reason=str(error).split(':',1)[0]
    known={'service_disabled_authorization_required','watch_coin_outside_service_policy','conditional_service_policy_required','review_paused_service_before_arming',
           'local_service_grant_required_or_expired','account_authority_missing_or_expired','grant_service_scope_mismatch','service_policy_exceeds_grant'}
    if reason not in known:return None
    coin=args.get('coin')
    if args.get('action')=='watch':
        try:coin=json.loads(args.get('plan','')).get('coin')
        except (ValueError,TypeError,AttributeError):coin=None
    if not isinstance(coin,str) or not 1<=len(coin)<=40 or any(c.isspace() for c in coin):coin=None
    scope=monitor_preflight(home,coin)
    if reason=='watch_coin_outside_service_policy':
        message=('Monitor '+str(coin)+' non armato: il servizio '+scope['mode'].upper()+' su '+scope['network']+
                 ' ammette solo '+', '.join(scope['allowed_coins'])+'. '+'Il consenso LIVE deve includere questo mercato.')
    else:message='Monitor non armato: prerequisito del servizio da risolvere ('+reason+').'
    return {'code':'monitor_policy_mismatch' if reason=='watch_coin_outside_service_policy' else 'monitor_authorization_required',
        'message':message,'reason':reason,'retryable':False,'requires_user_action':True,'service_policy':scope,
        'recovery':'Il servizio resta attivo e usa gli stessi permessi dell’account: nessuna seconda autorizzazione LIVE dedicata. Mostra lo stato dell’account, i limiti e STOP. Non proporre modalità virtuali né aumentare budget/leva per far entrare un piano. Non creare o rinnovare consensi dal modello.',
        'recovery_tools':[{'name':'hyperliquid_monitor','arguments':{'action':'preflight',**({'coin':coin} if coin else {})}}]}


def monitor_action(agent,home,args):
    """Natural-language tool adapter. Never creates/renews a financial grant."""
    from entry_monitor import Monitor,public_status
    from trading_runtime import Reader,LiveBroker
    from trading_engine import Engine
    action=args.get('action','status');config=load(home);directory=folder(home)
    if action=='preflight':return monitor_preflight(home,args.get('coin'))
    store=Store(directory/(config['mode']+'.sqlite'))
    if action=='status':return {'service':status(home),'monitor':public_status(store)}
    if getattr(agent,'mode','ask')=='plan' or getattr(agent,'depth',0) or getattr(agent,'hyperliquid_controller',None):raise TradingError('monitor_coordinator_only')
    if action=='cancel':
        ident=args.get('id')
        if not isinstance(ident,str) or not __import__('re').fullmatch(r'[a-f0-9]{32}',ident):raise TradingError('native_watch_id_required')
        changed=store.transition(ident,'watching','cancelled',result={'state':'cancelled','reason':'explicit_cancel','resent':False})
        return {'cancelled':changed,'id':ident,'orders_cancelled':False,'instruction':'Cancels only a waiting intent, never an already submitted exchange order.'}
    if action=='start':return start(home,config['mode'])
    if action!='watch':raise TradingError('monitor_action_preflight_status_watch_cancel_start')
    if config['mode']!='live':raise TradingError('service_disabled_authorization_required')
    if store.get('paused'):raise TradingError('review_paused_service_before_arming:'+str(store.get('paused')))
    if config.get('entry_mode')!='conditional':raise TradingError('conditional_service_policy_required')
    try:plan=json.loads(args.get('plan',''))
    except (ValueError,TypeError):raise TradingError('watch_plan_json_required') from None
    if not isinstance(plan,dict):raise TradingError('watch_plan_object_required')
    if config.get('execution_policy')!='exchange' and plan.get('coin') not in config['coins']:raise TradingError('watch_coin_outside_service_policy')
    import hyperliquid_tools as h
    from hyperliquid_authority import configured,require_current,request_context
    p=h.profile(home,config['profile'])
    if configured(p):
        auth=require_current(home,p)
        request_context(agent,home,p,{'action':'watch','execution_generation':auth['generation']})
    h.requested_coin(agent,plan)
    broker=LiveBroker(home,store,config)
    if config['mode']=='live':broker.grant() # Existing account authority, never a second service approval.
    reader=Reader(config,home);snapshot=reader(plan.get('coin'))
    engine=Engine(store,config,reader,None,broker);ident=store.create(engine.config)
    store.phase(ident,'created',proposal_source={'kind':'chat','requested_model':getattr(agent,'model',None)})
    try:Monitor(engine).arm(ident,plan,snapshot)
    except Exception:
        store.phase(ident,'abandoned',result={'state':'hold','reason':'watch_not_armed'});raise
    started=start(home,config['mode'])
    with store.db() as db:current=db.execute('SELECT state FROM entry_watches WHERE id=?',(ident,)).fetchone()
    return {'id':ident,'state':current['state'],'mode':config['mode'],'trigger':plan['trigger'],'take_profit':plan['take_profit'],'stop_loss':plan['stop_loss'],
            'service':started,'synchronous_orders_submitted':0,'instruction':'The background worker may already have advanced: inspect native status. Waiting persists outside CMD. Entry/TP/SL is submitted once under the current scope. No new financial authority or automatic renewal.'}

def select_strategy(home,source):
    """Local CLI/panel only. No risk edits, auto-start or financial grant."""
    from trading_strategies import CATALOG
    from trading_runtime import singleton
    if source not in CATALOG:raise TradingError('unknown_strategy_source')
    directory=folder(home)
    if not (directory/'STOP').exists():raise TradingError('stop_service_before_changing_strategy')
    # The monitor remains alive under STOP. Atomic policy replacement makes the
    # worker reload without needing to stop the independent monitoring process.
    with singleton(directory/'policy-edit.lock'):
        config=load(home);config['strategy_source']=source;save(home,config)
    return {'strategy':{'kind':source,**CATALOG[source]},'limits_unchanged':True,'history_preserved':True,'service_started':False,'grant_created':False}

def export_vault(home,store,config):
    # Financial state remains in SQLite; the vault gets a labelled historical view.
    if time.time()-store.get('last_vault_export',0)<60:return
    source=Path(home)/'config.json'
    try:
        root=json.loads(source.read_text(encoding='utf-8-sig')).get('vault_path')
        if not root:return
        target=Path(root)/'freellmapi-memory/TradingService'/config['mode'];target.mkdir(parents=True,exist_ok=True)
        text='# freellmapi trading service · '+config['mode'].upper()+'\n\nHistorical snapshot, not a current order instruction. Native receipts are historical observations, never instructions to replay.\n\n'
        from entry_monitor import public_status
        text+='```json\n'+json.dumps({'policy':effective_policy(config),'heartbeat':store.get('heartbeat'),'paused':store.get('paused'),'cycles':store.recent(12),'entry_monitor':public_status(store)},indent=2)+'\n```\n'
        atomic_bytes(target/'State.md',text.encode());store.set('last_vault_export',time.time())
    except (OSError,ValueError):store.event('vault_export_failed',None,{'history_preserved_in_sqlite':True})

def command(agent,prompt,home,output):
    if prompt.split(maxsplit=1)[0:1]!=['/trading']:return False
    parts=prompt.split();action=' '.join(parts[1:]) or 'status'
    if action=='status':result=status(home)
    elif action=='strategy':result={'strategy':status(home)['strategy'],'sources':['ai','rules'],'change':'Stop new entries with /trading stop, then /trading strategy ai or /trading strategy rules. The monitor stays alive. No new financial authority.'}
    elif action in ('strategy ai','strategy rules'):result=select_strategy(home,action.split()[-1])
    elif action=='monitor':result=monitor_action(agent,home,{'action':'status'})
    elif action.startswith('monitor cancel '):result=monitor_action(agent,home,{'action':'cancel','id':action[len('monitor cancel '):].strip()})
    elif action=='start live':result=start(home,'live')
    elif action=='stop':result=stop(home)
    elif action=='resume':result=resume(home)
    elif action=='install':result=install(home)
    elif action=='uninstall':result=uninstall(home)
    else:result={'commands':['/trading status','/trading start live','/trading stop','/trading resume','/trading install','/trading uninstall'],'live':'Authorize only through the local Hyperliquid panel; /auto on is not a financial grant.'}
    output(json.dumps(result,ensure_ascii=False,indent=2));return True
