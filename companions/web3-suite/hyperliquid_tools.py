"""Official SDK integration: scoped profiles, exact previews, bounded automation and no blind replay."""
import hashlib,json,re,sqlite3,time,uuid,threading
from contextlib import contextmanager
from decimal import Decimal,InvalidOperation,ROUND_DOWN,ROUND_UP
from importlib.metadata import version,PackageNotFoundError
from pathlib import Path
from reliable_runtime import atomic_bytes

URLS={'mainnet':'https://api.hyperliquid.xyz','testnet':'https://api.hyperliquid-testnet.xyz'}
RESOURCES=['account','positions','orders','fills','vaults','vault','subaccounts','agents','markets','candles','order_status','asset','snapshot','risk']
ACTIONS=['order','cancel','leverage','vault_deposit','vault_withdraw']
AUTO_STRATEGY=('La strategia è gestita autonomamente da freellmapi. Analizza dati Hyperliquid aggiornati, candele chiuse, volatilità, liquidità, posizioni e ordini presenti. '
 'Scegli e adatta approccio, ingresso, quantità, take profit e stop loss entro i limiti autorizzati; usa HOLD se il segnale o i dati non bastano. '
 'Ogni nuova posizione richiede entry, TP e SL coerenti nello stesso gruppo; niente martingala per recuperare perdite. '
 'Rileggi ricevute e risultati reali; salva il piano con hyperliquid_strategy distinguendo ipotesi da risultati verificati. '
 'Non cambiare account, rete, limiti o credenziali. Esiti incerti o TP/SL parzialmente rifiutati richiedono arresto e riconciliazione, senza ordine sostitutivo.')
SETTING_LABELS={'max_order_usd':'Notional massimo per ordine USD','max_exposure_usd':'Esposizione lorda massima USD','max_loss_usd':'Arresto per calo equity USD','max_turnover_usd':'Budget cumulativo notional USD','max_leverage':'Leva massima','max_orders_hour':'Massimo nuovi ordini / ora','interval':'Intervallo di controllo','duration_minutes':'Durata autorizzazione'}
SPECS=[
 ('hyperliquid_preflight','Fast LOCAL execution readiness: unresolved receipts on the same master/network and local signer presence. No RPC/signing or permission changes. Use before analyzing a requested entry; show blockers and their existing IDs immediately, never replace an uncertain order.',{'profile':'string'},[]),
 ('hyperliquid_execute_plan','Preferred ONE-CALL execution for a complete immediate-entry plan: validates typed plan, reuses fresh native analysis, verifies actual leverage, prepares entry+TP+SL, obtains the existing native local approval/grant, submits ONCE via OMS and verifies receipt. No intermediate model roundtrip or duplicate ask_user confirmation. plan is JSON STRING {decision:enter,reason,coin,side:buy/sell,size:BASE decimal string,entry,take_profit,stop_loss,leverage:integer}; order_kind market/limit, explicit slippage_bps for market and bracket_slippage_bps required. Does not change leverage/limits, create authority, or infer consent from analysis. For future levels/H24 use hyperliquid_monitor. A repeated/changed plan in the same request cannot send again; consult the saved receipt. No keys.',{'profile':'string','plan':'string','order_kind':'string','slippage_bps':'integer','bracket_slippage_bps':'integer'},['plan','order_kind','bracket_slippage_bps']),
 ('hyperliquid_monitor','Persistent MCC-style conditional entry monitor, independent of chat/models. FIRST call action=preflight with coin to read saved mode, allowed markets, risk limits and blockers. A policy mismatch is non-retryable until the user resolves scope; never edit service config via shell, silently enable execution, or replace the watch with direct orders. action=preflight/status/watch/cancel/start. watch requires plan JSON STRING: {decision:watch,coin,side:buy/sell,size:base string,entry:future trigger string,take_profit,stop_loss,leverage:integer,reason,trigger:{kind:breakout/pullback,direction:above/below,level:SAME entry,confirmation:touch/close/retest,interval:1m/5m/15m/1h/4h for close,ttl_seconds:60..86400}}. Breakout buy above current mid, sell below; pullback buy below, sell above. Native service waits, invalidates/expires, revalidates then submits grouped entry/TP/SL ONCE. start reuses saved mode; Execution uses the SAME account permission and limits as chat; there is no separate service LIVE approval. Never creates/renews grants. cancel needs native watch id and only cancels WAITING intent, not exchange orders. Use for future trigger/H24 requests, not shell polling or a limit falsely described as a stop.',{'action':'string','plan':'string','id':'string','coin':'string'},['action']),
 ('hyperliquid_service','Always-on persistent monitor independent of chat/CMD. action=status (heartbeat, account permission and risk), start (monitoring, no second financial approval), stop (blocks new sends, monitoring stays active). Uses the SAME account permission as chat; no separate service LIVE approval. Account budget, expiry, scope and STOP remain enforced. No virtual trading.',{'action':'string'},['action']),
 ('hyperliquid_panel','Open the separate LOCAL Python wallet/settings panel for user login, mainnet/testnet/master/vault profiles and bounded auto trading. No model credential inputs. User enters keys only in this local GUI.',{},[]),
 ('hyperliquid_status','Inspect official SDK, saved account/vault profiles and actual automation grants. No keys returned. Hyperliquid vaults are trading accounts, not Obsidian memory.',{},[]),
 ('hyperliquid_strategy','Read/update the persistent model-managed strategy plan. action inspect/update; plan required for update. Proposals are unverified and cannot change grant/risk limits. Assess outcomes from actual fills/receipts; do not invent performance.',{'profile':'string','action':'string','plan':'string'},['action']),
 ('hyperliquid_analyze','Read-only Hyperliquid analyst evidence: two timeframes of CLOSED candles (120 each), EMA20/50, Wilder ATR14/RSI14, recent support/resistance, volume, live L2 spread/depth, funding/mark/oracle and actual account fee rates. Defaults 15m + 1h; allowed 5m/15m/1h/4h/1d increasing. No BUY/SELL prediction or permission to trade. Compare long, short and HOLD; no guaranteed TP or invented win probabilities. Use before new-risk preview; keep evidence_id across model handoffs.',{'profile':'string','coin':'string','interval':'string','higher_interval':'string'},['coin']),
 ('hyperliquid_scan','Read-only screening of ALL current perpetual markets across ALL Hyperliquid DEXs, saving full JSON evidence. Lists liquidity-ranked markets, 24h change, mark, funding, collateral and precision. No automatic long/short or candle analysis claimed for all symbols. Use hyperliquid_analyze on selected candidates. Default 20 rows, max 50; offset/scan_id pages the SAME saved snapshot without more API requests. Incomplete DEX reads are disclosed; no signing, orders or changes to risk limits.',{'profile':'string','limit':'integer','offset':'integer','scan_id':'string'},[]),
 ('hyperliquid_read','Read live Hyperliquid account balances, spot, positions, orders/fills, vaults/details, agents/subaccounts, markets/candles/order status. Uses actual master/vault address, never API signer address. Read-only. resource account/orders/fills/vaults/vault/subaccounts/agents/markets/candles/order_status; candles requires coin, interval; order_status requires cloid.',{'resource':'string','profile':'string','address':'string','coin':'string','dex':'string','interval':'string','limit':'integer','cloid':'string'},['resource']),
 ('hyperliquid_preview','Prepare a durable unsigned Hyperliquid action. Never sends it. action=order/cancel/leverage/vault_deposit/vault_withdraw. Every NEW-RISK order, manual or auto, REQUIRES take_profit, stop_loss and bracket_slippage_bps 1–1000 IN THIS preview; one normalTpsl group submits entry + opposite-side reduce-only TP/SL. Never submit a naked entry and add TP later. Analyze a current snapshot and closed candles: choose long buy (SL < entry < TP), short sell (TP < entry < SL), or HOLD if unjustified. Order: exact coin, buy/sell, BASE decimal-string size, limit/market, decimal-string price or explicit slippage_bps; tif Gtc/Alo/Ioc. Existing position reduce_only exits do not require another bracket. Cancel needs coin/oid; leverage needs coin/leverage/cross; vault transfer needs vault/amount. Ownership/precision validated. No keys in arguments.',{'profile':'string','action':'string','coin':'string','side':'string','size':'string','order_kind':'string','price':'string','slippage_bps':'integer','reduce_only':'boolean','tif':'string','oid':'integer','leverage':'integer','cross':'boolean','vault':'string','amount':'string','take_profit':'string','stop_loss':'string','bracket_slippage_bps':'integer'},['action']),
 ('hyperliquid_submit','Submit an existing unexpired LOCAL 24-hex preview/receipt ID once. Never pass a 0x client ID or exchange oid here: resolve it with hyperliquid_operations first. Interactive manual approval or a LOCAL, already-authorized auto grant for SAME profile/network/master/target/session/strategy and limits is required. /auto on alone grants no financial authority. Unknown outcome is UNCERTAIN: reconcile, never replace. No fund movements or credentials in chat.',{'id':'string'},['id']),
 ('hyperliquid_operations','Read HISTORICAL receipts. id is the local 24-hex receipt ID; a recorded 0x + 32-hex client ID is resolved read-only when unique, including TP/SL legs. Never guess IDs. Omit id to list valid local IDs. accepted/filled is historical execution, not proof of an open position. Reconciliation only reads exchange status; never submits or replaces an order.',{'id':'string','profile':'string'},[]),
 ('hyperliquid_select','Select an existing saved Hyperliquid profile by name (mainnet/testnet/account/vault). Does not grant trading rights or change an existing automation scope.',{'profile':'string'},['profile']),
]
SPECS.append(('hyperliquid_propose_plan',
 'Present a complete typed trade proposal with native choices: Conferma invio / Aggiorna analisi / Annulla, plus free text. Use when showing a trade preview or asking whether to execute. Same plan and sibling options as execute_plan. Nothing is sent unless the user explicitly selects Conferma invio; then the exact plan goes through the existing OMS once. Read-only/plan requests stay read-only. No keys.',
 {'profile':'string','plan':'string','order_kind':'string','slippage_bps':'integer','bracket_slippage_bps':'integer'},['plan','order_kind','bracket_slippage_bps']))
NAMES={s[0] for s in SPECS}
# HOLD is an explicit no-order decision in the same durable protocol.
for _index,(_name,_description,_properties,_required) in enumerate(SPECS):
    if _name=='hyperliquid_execute_plan':
        _properties=dict(_properties)
        _properties['plan']={'type':'string','description':'JSON string containing ONLY decision, reason, coin, side, size, entry, take_profit, stop_loss, leverage (optional trigger for watch). Never put execution options inside this JSON.'}
        _properties['order_kind']={'type':'string','enum':['market','limit'],'description':'Sibling of plan, OUTSIDE its JSON string. Required for enter.'}
        for _option in ('slippage_bps','bracket_slippage_bps'):_properties[_option]={'type':'integer','minimum':1,'maximum':1000,'description':'Sibling of plan, OUTSIDE its JSON string. An explicit execution option; do not invent a user limit.'}
        SPECS[_index]=(_name,'Preferred ONE-CALL execution for a complete immediate-entry plan: validate the typed plan, reuse fresh native analysis, prepare entry+TP+SL, submit ONCE through the native order manager and verify the receipt. In key_only mode the connected API signing key plus a NEW user request authorize trading without local coin/quantity/leverage/loss caps or a panel approval. Requested leverage is set once and verified against the exchange before entry. Legacy profiles retain their existing approval protocol. plan is a JSON STRING {decision:enter,reason,coin,side:buy/sell,size:BASE decimal string,entry,take_profit,stop_loss,leverage:positive integer}; order_kind market/limit, explicit slippage_bps for market and bracket_slippage_bps required. For HOLD use {decision:hold,reason}; no signing/sending. Analysis alone is not an execution request. Future triggers use hyperliquid_monitor. Repeated or changed plans in the SAME request cannot send again: consult the saved receipt. No keys in arguments.',_properties,['plan'])
    elif _name in ('hyperliquid_submit','hyperliquid_monitor','hyperliquid_service','hyperliquid_panel'):
        prefix={
            'hyperliquid_submit':'Submit an existing LOCAL 24-hex receipt ID ONCE. In key_only mode a connected API signing key plus a NEW user request/native setup is the trading permission; no separate panel approval, coin whitelist or local quantity/leverage/loss budget. Old previews without the current connection generation cannot be revived. Unknown outcomes are reconciled read-only, never replaced. Keys never enter tool arguments.',
            'hyperliquid_panel':'Open the LOCAL Python credential adapter: account/network/vault metadata and the Hyperliquid API signing key only. No financial-policy form or per-coin/quantity/leverage approval. Credentials remain local, never in chat. Connecting a key does not execute old orders or start an autonomous strategy.',
            'hyperliquid_service':'Persistent monitor: status/start/stop. In key_only mode it uses the connected key and explicitly registered setups, without separate financial grants or local coin/quantity/leverage/loss budgets. Start monitors; it does not replay old work or automatically create a new trading strategy. Exchange constraints and durable receipts still apply.',
            'hyperliquid_monitor':'Persistent conditional-entry monitor. action=preflight/status/watch/cancel/start. Key-only profiles allow all supported exchange-listed perpetuals, not just scan_coins; no extra financial authorization or local quantity/leverage/loss caps. watch plan is a JSON STRING {decision:watch,reason,coin,side:buy/sell,size:base decimal,entry,take_profit,stop_loss,leverage:positive integer,trigger:{kind:breakout/pullback,direction:above/below,level:SAME entry,confirmation:touch/close/retest,interval:1m/5m/15m/1h/4h for close,ttl_seconds:60..86400}}. Native service waits and revalidates, sets/verifies exchange-valid leverage and submits entry+TP+SL once. Preflight reports real blockers. cancel only cancels a waiting native watch ID; no exchange-order cancellation or replay.'
        }[_name]
        SPECS[_index]=(_name,prefix,_properties,_required)
SIGNER_LOCK=threading.RLock()

class HLProblem(ValueError):
    def __init__(self,code,message,**details):self.code=code;self.details=details;super().__init__(message)

def problem(code,message,**details):raise HLProblem(code,message,**details)
def address(value):
    if not isinstance(value,str) or not re.fullmatch(r'0x[0-9a-fA-F]{40}',value):problem('invalid_address','Indirizzo EVM pubblico richiesto: 0x + 40 cifre esadecimali.')
    return value
def number(value,label):
    try:out=Decimal(str(value))
    except (InvalidOperation,TypeError):problem('invalid_number',label+': usa un numero decimale finito.')
    if not out.is_finite() or out<=0 or out>Decimal('1e15'):problem('invalid_number',label+': numero positivo finito, massimo 1e15.')
    return out
def textnum(value):return format(Decimal(value).normalize(),'f')
def folder(home):
    from app_home import effective_home
    p=effective_home(home)/'hyperliquid';p.mkdir(parents=True,exist_ok=True);return p
def config(home):
    p=folder(home)/'profiles.json'
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {'default':None,'profiles':{}}
def save_config(home,data):atomic_bytes(folder(home)/'profiles.json',json.dumps(data,indent=2).encode('utf-8'))
def profile(home,name=None):
    data=config(home);name=name or data['default']
    if name not in data['profiles']:
        names=sorted(data['profiles'])
        message=('Profilo richiesto non trovato. Profili salvati: '+', '.join(names)+'. Consulta hyperliquid_status e usa un nome esatto; non aprire un nuovo login.' if names else 'Nessun profilo salvato. Collega un profilo con /hyperliquid login. Solo indirizzi pubblici; firma inserita localmente.')
        problem('profile_missing',message,available_profiles=names,default_profile=data.get('default'))
    p=data['profiles'][name]
    if p['network'] not in URLS:problem('network_invalid','Rete ammessa: mainnet o testnet.')
    address(p['account']);address(p.get('target') or p['account']);return dict(p,name=name)
def fingerprint(p):return hashlib.sha256(json.dumps({k:p.get(k) for k in ('name','network','account','target','signer','signer_kind')},sort_keys=True).encode()).hexdigest()
def configure(home,name,network,account,target=None,kind='account'):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,48}',name):problem('profile_name','Nome profilo: 1–48 lettere, numeri, _ o -.')
    if network not in URLS or kind not in ('account','vault','subaccount'):problem('profile_type','Rete/tipo profilo non valido.')
    account=address(account);target=address(target or account)
    if (kind=='account')!=(target.lower()==account.lower()):problem('target_scope','Account richiede target=master. Vault/subaccount richiede un indirizzo target distinto.')
    data=config(home);old=data['profiles'].get(name)
    if old and (old['network']!=network or old['account'].lower()!=account.lower() or old['target'].lower()!=target.lower()):problem('profile_exists','Profilo esistente con altro scope: crea un nome distinto, non riutilizzare la firma.')
    data['profiles'][name]={**(old or {}),'network':network,'account':account,'target':target,'kind':kind};data['default']=name;save_config(home,data)
    return profile(home,name)
def secret_path(home,name):return folder(home)/(name+'.dpapi')
def api(p):
    from hyperliquid.api import API
    from requests.adapters import HTTPAdapter
    import ssl,truststore
    class SystemTLS(HTTPAdapter):
        def init_poolmanager(self,*args,**kwargs):
            kwargs['ssl_context']=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT);return super().init_poolmanager(*args,**kwargs)
        def proxy_manager_for(self,proxy,**kwargs):
            kwargs['ssl_context']=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT);return super().proxy_manager_for(proxy,**kwargs)
    client=API(URLS[p['network']],timeout=10);client.session.mount('https://',SystemTLS(max_retries=0));return client
def info(p,body):
    try:
        client=api(p)
        try:return client.post('/info',body)
        finally:client.session.close()
    except Exception as exc:
        status=getattr(exc,'status_code',None)
        retryable=status is None or status in (408,429,500,502,503,504)
        problem('info_unavailable','Hyperliquid /info non disponibile ('+type(exc).__name__+'). Nessuna operazione inviata, non cambiare rete/account per aggirare l’errore.',http_status=status if isinstance(status,int) else None,retryable=retryable)

@contextmanager
def database(home,timeout=15):
    path=folder(home)/'operations.db';existing=path.exists()
    if existing:path=path.resolve(strict=True) # Keep WAL/SHM beside the actual MSIX-redirected database.
    for attempt in range(2):
        db=None
        try:
            db=sqlite3.connect(path.absolute().as_uri()+'?mode=rw',uri=True,timeout=timeout) if existing else sqlite3.connect(path,timeout=timeout)
            db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=FULL')
            break
        except sqlite3.OperationalError as error:
            if db is not None:db.close()
            if attempt==0 and (getattr(error,'sqlite_errorcode',None)==sqlite3.SQLITE_CANTOPEN or 'unable to open database file' in str(error)):
                time.sleep(.05);continue # Only retry opening; no transaction/action has begun.
            error.add_note('Hyperliquid archive: '+str(path));raise
    db.row_factory=sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS actions(id TEXT PRIMARY KEY, intent TEXT UNIQUE, profile TEXT, state TEXT, created REAL, updated REAL, body TEXT, response TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS grants(profile TEXT PRIMARY KEY, body TEXT)')
    db.commit()
    try:yield db
    finally:db.close()
def receipt(row):
    if not row:return None
    out=dict(row);out['body']=json.loads(out['body']);out['response']=json.loads(out['response']) if out.get('response') else None;return out
def current_grant(db,name):
    row=db.execute('SELECT body FROM grants WHERE profile=?',(name,)).fetchone();return json.loads(row[0]) if row else None
def stop_epoch(home,name):
    return '|'.join((folder(home)/item).read_text(encoding='ascii') if (folder(home)/item).is_file() else '' for item in ('STOP_ALL','STOP-'+name))
def status(home):
    try:sdk=version('hyperliquid-python-sdk')
    except PackageNotFoundError:sdk=None
    data=config(home)
    storage={'available':True}
    try:
        with database(home) as db: grants=[json.loads(r[0]) for r in db.execute('SELECT body FROM grants')]
    except sqlite3.Error as error:
        from background_poll import diagnostic
        diagnostic(home,'hyperliquid_status',error)
        grants=[];storage={'available':False,'error_type':type(error).__name__,'message':'Archivio operazioni non disponibile: stato autorizzazioni non verificato. Le letture /info restano indipendenti; nessun invio può aggirare l’archivio.'}
    from hyperliquid_authority import configured,current
    execution={n:({'mode':'key_only','connected':bool((current(home,{**p,'name':n}) or {}).get('active')),'market_scope':'all_listed_perpetuals','local_leverage_cap':None,'local_quantity_cap':None,'loss_limit_usd':p['execution_authority'].get('loss_limit_usd'),'exchange_permissions':'verified before signing','new_requests_only':True} if configured(p) else {'mode':'legacy'}) for n,p in data['profiles'].items()}
    grants=[g for g in grants if not configured(data['profiles'].get(g.get('profile'),{}))]
    return {'sdk':'hyperliquid-python-sdk','version':sdk,'sdk_ready':bool(sdk),'default':data['default'],'profiles':[{**p,'name':n,'signer_configured':secret_path(home,n).is_file()} for n,p in data['profiles'].items()],'execution':execution,
            'automation':grants,'automation_verified':storage['available'],'storage':storage,'scope':'Local signing only; no private key or seed is sent to a model. DPAPI is storage protection for this Windows user, not an OS sandbox.','writes':'key_only: connected signing key plus a new explicit request/native setup; no local financial-policy approval. Legacy profiles retain their approval protocol. Exchange permissions are checked before signing; mainnet has real funds.'}

def account_mode(p):
    mode=info(p,{'type':'userAbstraction','user':p['target']})
    if mode not in ('default','disabled','dexAbstraction','unifiedAccount','portfolioMargin'):
        problem('account_mode_unverified','Modalità del conto non verificata. Nessuna firma: non dedurre il margine dal solo saldo perps o spot.')
    return mode

def execution_mode(p):
    mode=account_mode(p)
    if mode not in ('default','disabled','unifiedAccount'):
        problem('unsupported_account_mode','Conto '+mode+': automazione supportata per Standard e Unified Account USDC. Portfolio Margin e DEX abstraction restano bloccati. Nessun trasferimento o cambio modalità automatico.')
    return mode

def balance_context(mode,perps,spot):
    unified=mode in ('unifiedAccount','portfolioMargin')
    return {'account_mode':mode,'balance_source':'spotClearinghouseState' if unified else 'clearinghouseState',
            'execution_supported':mode in ('default','disabled','unifiedAccount'),
            'perps_account_value':perps['marginSummary']['accountValue'],
            'spot_usdc':next((b.get('total') for b in spot.get('balances',[]) if b.get('coin')=='USDC'),None),
            'instruction':('Individual perp accountValue is not collateral. Use risk for conservative USDC collateral, all-DEX exposure and margin checks. Portfolio Margin is unsupported. No auto transfer or mode change.' if unified else 'Spot and perps balances are separate. Do not transfer funds without explicit authorization.')}

def risk_state(p,dex=''):
    import hyperliquid_risk as risk
    try:return risk.snapshot(p,info,dex)
    except risk.RiskError as exc:problem(exc.code,str(exc))

def check_capacity(risk,active,body,notional):
    import hyperliquid_risk as module
    try:return module.capacity(risk,active,body,notional)
    except module.RiskError as exc:problem(exc.code,str(exc),recovery_coin=body.get('coin'),recovery_profile=body.get('profile'),**exc.details)

def checked_leverage(asset,require_type=False):
    raw=asset.get('leverage') if isinstance(asset,dict) else None
    value=raw.get('value') if isinstance(raw,dict) else None
    try:lev=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError):lev=Decimal('NaN')
    kind=raw.get('type') if isinstance(raw,dict) else None
    if isinstance(value,bool) or not lev.is_finite() or lev<1 or lev!=lev.to_integral_value() or (require_type and kind not in ('cross','isolated')):
        problem('leverage_unverified','Leva effettiva non verificabile: servono un intero positivo e il tipo cross/isolated. Nessun nuovo ingresso.')
    return {'value':int(lev),'type':kind}

def leverage_within_limit(p,coin,limit):
    active=info(p,{'type':'activeAssetData','user':p['target'],'coin':coin})
    lev=checked_leverage(active)['value']
    if lev>limit:problem('existing_leverage_cap','Leva effettiva '+str(lev)+' oltre il limite '+str(limit)+'. Automazione sospesa; nessun nuovo ingresso né tentativo ripetuto.')
    return active

def check_flows(p,g):
    if g.get('account_mode')!='unifiedAccount':return
    start=g.get('baseline_since_ms',int(g['created']*1000))
    flows=info(p,{'type':'userNonFundingLedgerUpdates','user':p['target'],'startTime':start})
    if not isinstance(flows,list) or any(not isinstance(x,dict) or not isinstance(x.get('time'),(int,float)) for x in flows):problem('account_flows_unverified','Movimenti conto non verificabili: nessun rischio nuovo.')
    if any(x['time']>=start for x in flows):problem('account_flow_changed','Movimento di capitale/liquidazione durante o dopo la lettura iniziale: ferma e rivaluta il budget. Nessuna perdita mascherata da nuovi depositi; nessun trasferimento automatico.')

def manual_capacity(p,b,home=None):
    from hyperliquid_authority import configured,capacity
    if configured(p):return capacity(home,p,b)
    if b['action']=='order' and not b.get('reduce_only') and account_mode(p)=='unifiedAccount':
        risk=risk_state(p,b.get('dex',''));active=info(p,{'type':'activeAssetData','user':p['target'],'coin':b['coin']})
        check_capacity(risk,active,b,Decimal(b['notional_usd']))

def read(home,a):
    p=profile(home,a.get('profile'));resource=a['resource'];target=address(a.get('address') or p['target']);dex=a.get('dex','')
    if resource not in RESOURCES:problem('resource_invalid','Risorse: '+', '.join(RESOURCES))
    limit=max(1,min(200,int(a.get('limit',30))))
    if resource=='risk':data=risk_state(dict(p,target=target),dex)
    elif resource=='account':
        mode=account_mode(dict(p,target=target))
        data={'role':info(p,{'type':'userRole','user':target}),'perps':info(p,{'type':'clearinghouseState','user':target,'dex':dex}),'spot':info(p,{'type':'spotClearinghouseState','user':target})}
        data['balance_context']=balance_context(mode,data['perps'],data['spot'])
    elif resource=='snapshot':
        if not a.get('coin'):problem('coin_required','Snapshot di analisi richiede coin esatto.')
        coin=a['coin'];dex=coin.split(':')[0] if ':' in coin else ''
        data={'perps':info(p,{'type':'clearinghouseState','user':target,'dex':dex}),
              'orders':info(p,{'type':'frontendOpenOrders','user':target,'dex':dex}),
              'asset':info(p,{'type':'activeAssetData','user':target,'coin':coin}),
              'candles':read(home,{'profile':p['name'],'address':target,'resource':'candles','coin':coin,'interval':a.get('interval','15m'),'limit':limit})['data'],
              'market':read(home,{'profile':p['name'],'resource':'markets','coin':coin,'dex':dex,'limit':1})['data']}
        mode=account_mode(dict(p,target=target));spot=info(p,{'type':'spotClearinghouseState','user':target}) if mode in ('unifiedAccount','portfolioMargin') else {'balances':[]}
        data['balance_context']=balance_context(mode,data['perps'],spot)
        data['risk']=risk_state(dict(p,target=target),dex)
    elif resource=='positions':
        raw=info(p,{'type':'clearinghouseState','user':target,'dex':dex});data={'positions':raw['assetPositions'][:limit],'marginSummary':raw['marginSummary'],'time':raw.get('time')}
    elif resource=='markets':
        raw=info(p,{'type':'metaAndAssetCtxs','dex':dex});pairs=list(zip(raw[0]['universe'],raw[1]))
        if a.get('coin'):pairs=[(spec,ctx) for spec,ctx in pairs if spec['name']==a['coin']]
        data={'markets':[{'meta':spec,'context':ctx} for spec,ctx in pairs[:limit]],'total_matched':len(pairs)}
    elif resource=='vault':data=info(p,{'type':'vaultDetails','vaultAddress':target,'user':p['account']})
    elif resource=='asset':
        if not a.get('coin'):problem('coin_required','Per activeAssetData specifica coin.')
        data=info(p,{'type':'activeAssetData','user':target,'coin':a['coin']})
    elif resource=='order_status':
        cloid=a.get('cloid','')
        if not re.fullmatch(r'0x[0-9a-fA-F]{32}',cloid):problem('cloid_invalid','Client order ID richiesto: 0x + 32 cifre esadecimali.')
        data=info(p,{'type':'orderStatus','user':target,'oid':cloid})
    elif resource=='candles':
        coin=a.get('coin');interval=a.get('interval','15m');intervals={'1m':60,'3m':180,'5m':300,'15m':900,'30m':1800,'1h':3600,'2h':7200,'4h':14400,'8h':28800,'12h':43200,'1d':86400}
        if not coin or interval not in intervals:problem('candles_invalid','Coin e intervallo supportato richiesti: '+', '.join(intervals))
        now=int(time.time()*1000);data=info(p,{'type':'candleSnapshot','req':{'coin':coin,'interval':interval,'startTime':now-(limit+2)*intervals[interval]*1000,'endTime':now}})
        data=[bar for bar in data if int(bar['T'])<now][-limit:]
    else:
        types={'orders':'frontendOpenOrders','fills':'userFills','vaults':'userVaultEquities','subaccounts':'subAccounts','agents':'extraAgents'}
        body={'type':types[resource],'user':target}
        if resource=='orders':body['dex']=dex
        data=info(p,body)
        if isinstance(data,list):data=data[:limit]
    if resource=='vault' and isinstance(data,dict):
        data=dict(data)
        if 'followers' in data:data['followers_total']=len(data['followers']);data['followers']=data['followers'][:limit]
        for _,series in data.get('portfolio',[]):
            for key in ('accountValueHistory','pnlHistory'):
                if key in series:series[key]=series[key][-limit:]
    return {'profile':p['name'],'network':p['network'],'account':p['account'],'queried_address':target,'resource':resource,'retrieved_ms':int(time.time()*1000),'source':URLS[p['network']]+'/info','data':data}

def permissions(p,signer_required=False,home=None):
    target=p['target'];master=p['account'];proof={}
    role=info(p,{'type':'userRole','user':master})
    if role.get('role') not in ('user',):problem('master_not_user','L’indirizzo master deve essere un account utente Hyperliquid attivo, non l’indirizzo API agent o una vault.')
    if target.lower()!=master.lower():
        r=info(p,{'type':'userRole','user':target});proof['target_role']=r
        if r.get('role')=='vault':
            detail=info(p,{'type':'vaultDetails','vaultAddress':target,'user':master})
            if str(detail.get('leader','')).lower()!=master.lower():problem('not_vault_leader','Il master configurato non è il leader della vault: nessun trading autorizzato per questa vault.')
        elif r.get('role')=='subAccount':
            if str(r.get('data',{}).get('master','')).lower()!=master.lower():problem('not_subaccount_owner','Il subaccount appartiene a un altro master.')
        else:problem('target_not_managed','Target non riconosciuto come vault/subaccount gestito dal master.')
    if signer_required:
        execution_mode(p)
        if not p.get('signer') or not secret_path(home,p['name']).is_file():problem('signer_missing','Accesso in sola lettura. Usa /hyperliquid login per collegare la firma locale, senza chiavi in chat.')
        if p['signer'].lower()!=master.lower():
            r=info(p,{'type':'userRole','user':p['signer']})
            if r.get('role')!='agent' or str(r.get('data',{}).get('user','')).lower()!=master.lower():problem('agent_unauthorized','API agent non autorizzato per questo master sulla rete selezionata. Autorizzalo nel frontend Hyperliquid; non creo wallet sostitutivi.')
    return proof

def market(p,coin):
    if not coin or not re.fullmatch(r'[A-Za-z0-9_:@.\-/]{1,60}',coin):problem('coin_invalid','Usa il nome esatto del mercato Hyperliquid.')
    dex=coin.split(':')[0] if ':' in coin else ''
    meta=info(p,{'type':'meta','dex':dex});row=next((r for r in meta.get('universe',[]) if r['name']==coin),None)
    if not row or row.get('isDelisted'):problem('market_missing','Mercato perpetual non disponibile: '+coin+'. Consulta hyperliquid_read markets; non sostituire automaticamente il simbolo.')
    return row,dex

def checked_price(value,decimals):
    price=number(value,'prezzo');max_dec=max(0,6-decimals)
    if price!=price.to_integral() and (price!=price.quantize(Decimal(10)**-max_dec) or len(price.normalize().as_tuple().digits)>5):problem('price_precision','Prezzo: massimo 5 cifre significative e '+str(max_dec)+' decimali; prezzi interi ammessi.')
    return price

def trigger_limit(trigger,is_buy,bps,decimals):
    value=trigger*(1+Decimal(bps)/10000*(1 if is_buy else -1))
    tick=Decimal(10)**max(-max(0,6-decimals),value.adjusted()-4)
    result=value.quantize(tick,rounding=ROUND_DOWN if is_buy else ROUND_UP)
    if result<=0:problem('bracket_precision','Limite TP/SL arrotondato a zero: correggi prezzi/slippage; nessun invio.')
    return result

def normalize_action(p,a):
    action=a['action']
    if action not in ACTIONS:problem('action_invalid','Azioni: '+', '.join(ACTIONS))
    b={'action':action,'scope':fingerprint(p),'network':p['network'],'account':p['account'],'target':p['target'],'profile':p['name']}
    permissions(p)
    if action in ('order','cancel','leverage'):
        row,dex=market(p,a.get('coin'));b.update(coin=row['name'],dex=dex)
    if action=='order':
        if 'leverage' in a or 'cross' in a:
            problem('leverage_action_required','Un ordine non modifica la leva. Leggi prima asset/snapshot; una modifica leva richiede action=leverage separata, autorizzazione e verifica successiva. Nessun parametro leva viene ignorato.')
        if a.get('side') not in ('buy','sell') or a.get('order_kind') not in ('limit','market'):problem('order_invalid','Specifica side buy/sell e order_kind limit/market.')
        if type(a.get('reduce_only',False)) is not bool:problem('order_invalid','reduce_only deve essere booleano, non testo o numeri.')
        if not a.get('reduce_only',False) and (not a.get('take_profit') or not a.get('stop_loss')):
            problem('protection_required','Ogni nuova entrata richiede take_profit e stop_loss nello stesso preview, in modalità manuale e automatica. Analizza lo snapshot corrente, scegli long/short o HOLD e specifica anche bracket_slippage_bps 1–1000. Non inviare prima l’entry.',required_fields=['take_profit','stop_loss','bracket_slippage_bps'])
        sz=number(a.get('size'),'size');decimals=int(row['szDecimals'])
        errors=[]
        if sz!=sz.quantize(Decimal(10)**-decimals):errors.append(('size_precision','size ammette massimo '+str(decimals)+' decimali; nessun arrotondamento nascosto.'))
        if a['order_kind']=='market' and (type(a.get('slippage_bps')) is not int or not 1<=a['slippage_bps']<=1000):errors.append(('slippage_required','Market IOC richiede slippage_bps esplicito 1–1000.'))
        if a.get('take_profit') or a.get('stop_loss'):
            if not a.get('take_profit') or not a.get('stop_loss'):errors.append(('bracket_invalid','Fornisci entrambi take_profit e stop_loss.'))
            if type(a.get('bracket_slippage_bps')) is not int or not 1<=a['bracket_slippage_bps']<=1000:errors.append(('bracket_slippage','Specifica bracket_slippage_bps esplicito 1–1000.'))
        if errors:problem(errors[0][0] if len(errors)==1 else 'order_fields_invalid','Correggi insieme: '+'; '.join(code+': '+message for code,message in errors)+' Nessun ordine inviato.')
        mids=info(p,{'type':'allMids','dex':dex});mid=number(mids.get(b['coin']),'prezzo attuale')
        if a['order_kind']=='market':
            slip=a.get('slippage_bps')
            if not isinstance(slip,int) or not 1<=slip<=1000:problem('slippage_required','Market IOC richiede slippage_bps esplicito (1–1000).')
            price=mid*(1+Decimal(slip)/10000*(1 if a['side']=='buy' else -1))
            tick=Decimal(10)**max(-max(0,6-decimals),price.adjusted()-4)
            price=price.quantize(tick,rounding=ROUND_DOWN if a['side']=='buy' else ROUND_UP);tif='Ioc'
            if 'entry_reference' in a:
                reference=number(a['entry_reference'],'entry del piano')
                bound=reference*(1+Decimal(slip)/10000*(1 if a['side']=='buy' else -1))
                bound_tick=Decimal(10)**max(-max(0,6-decimals),bound.adjusted()-4)
                bound=bound.quantize(bound_tick,rounding=ROUND_DOWN if a['side']=='buy' else ROUND_UP)
                if (mid>bound if a['side']=='buy' else mid<bound):
                    problem('plan_price_moved','Prezzo corrente oltre lo slippage totale dell’entry del piano: nessun invio.',
                            requested_entry=textnum(reference),observed_mid=textnum(mid),entry_limit=textnum(bound),slippage_bps=slip)
                # Both constraints apply to the SAME signed limit. Applying a
                # full second allowance to the new mid rejected tiny moves.
                price=min(price,bound) if a['side']=='buy' else max(price,bound)
                b['entry_bound']={'reference':textnum(reference),'max_slippage_bps':slip,'limit':textnum(bound)}
            if 'trigger_reference' in a or 'trigger_max_gap_bps' in a:
                reference=number(a.get('trigger_reference'),'trigger reference');gap=a.get('trigger_max_gap_bps')
                if type(gap) is not int or not 1<=gap<=1000:problem('trigger_bound_invalid','Limite nativo gap del trigger non valido.')
                bound=reference*(1+Decimal(gap)/10000*(1 if a['side']=='buy' else -1))
                bound_tick=Decimal(10)**max(-max(0,6-decimals),bound.adjusted()-4)
                bound=bound.quantize(bound_tick,rounding=ROUND_DOWN if a['side']=='buy' else ROUND_UP)
                if (mid>bound if a['side']=='buy' else mid<bound):problem('trigger_gap','Prezzo oltre il limite del setup: nessun inseguimento del trigger.')
                price=min(price,bound) if a['side']=='buy' else max(price,bound)
                b['trigger_bound']={'reference':textnum(reference),'max_gap_bps':gap,'limit':textnum(bound)}
                if a.get('trigger_direction') not in ('above','below') or a.get('trigger_confirmation') not in ('touch','close','retest'):problem('trigger_bound_invalid','Direzione/conferma nativa del trigger mancante.')
                b['trigger_bound'].update(direction=a['trigger_direction'],confirmation=a['trigger_confirmation'],retest_tolerance_bps=a.get('trigger_retest_bps',gap))
            if a.get('tighten_to_bracket') is True and a.get('take_profit') and a.get('stop_loss') and not a.get('reduce_only',False):
                tp=checked_price(a['take_profit'],decimals);sl=checked_price(a['stop_loss'],decimals);original=price
                coherent=(sl<mid<tp if a['side']=='buy' else tp<mid<sl)
                if coherent:
                    tp_tick=Decimal(10)**max(-max(0,6-decimals),tp.adjusted()-4)
                    price=min(price,tp-tp_tick) if a['side']=='buy' else max(price,tp+tp_tick)
                    price=checked_price(price,decimals)
                    if (price<mid if a['side']=='buy' else price>mid):problem('bracket_too_narrow','Nessun prezzo IOC valido tra mid e TP: nessun invio.')
                    if price!=original:b['entry_slippage_tightened']={'requested_limit':textnum(original),'effective_limit':textnum(price),'reason':'Tighter IOC limit within requested maximum slippage; TP/SL unchanged.'}
        else:
            price=number(a.get('price'),'price');tif=a.get('tif','Gtc')
            if tif not in ('Gtc','Alo','Ioc'):problem('tif_invalid','TIF ammessi: Gtc, Alo, Ioc.')
            max_dec=max(0,6-decimals)
            if price!=price.to_integral() and (price!=price.quantize(Decimal(10)**-max_dec) or len(price.normalize().as_tuple().digits)>5):problem('price_precision','Prezzo non valido: massimo 5 cifre significative e '+str(max_dec)+' decimali (prezzi interi ammessi).')
        if not a.get('reduce_only',False) and sz*price<Decimal('10'):
            problem('minimum_notional','Nuova entrata perpetual sotto il minimo di 10 USD: valore '+textnum(sz*price)+'. Ricalcola size entro szDecimals e limiti autorizzati; se incompatibili usa HOLD. Nessun ordine inviato.')
        b.update(side=a['side'],size=textnum(sz),price=textnum(price),mid=textnum(mid),notional_usd=textnum(sz*max(price,mid)),limit_notional_usd=textnum(sz*price),order_kind=a['order_kind'],tif=tif,reduce_only=bool(a.get('reduce_only',False)),slippage_bps=a.get('slippage_bps'))
        if a.get('take_profit') or a.get('stop_loss'):
            if not a.get('take_profit') or not a.get('stop_loss') or b['reduce_only']:problem('bracket_invalid','TP e SL vanno forniti insieme per una nuova entrata, non su un ordine reduce-only.')
            tp=checked_price(a['take_profit'],decimals);sl=checked_price(a['stop_loss'],decimals)
            low=min(price,mid) if a['order_kind']=='market' else price
            high=max(price,mid) if a['order_kind']=='market' else price
            if not (sl<low and high<tp if a['side']=='buy' else tp<low and high<sl):problem('bracket_direction','Long richiede SL < '+textnum(low)+' e TP > '+textnum(high)+'; short richiede TP < '+textnum(low)+' e SL > '+textnum(high)+'. Per IOC considera anche il peggior prezzo consentito, non solo il mid.')
            bps=a.get('bracket_slippage_bps')
            if not isinstance(bps,int) or not 1<=bps<=1000:problem('bracket_slippage','Specifica bracket_slippage_bps (1–1000) per i limiti di esecuzione TP/SL.')
            b['bracket']={'take_profit':textnum(tp),'stop_loss':textnum(sl),'tp_limit':textnum(trigger_limit(tp,a['side']=='sell',bps,decimals)),
                          'sl_limit':textnum(trigger_limit(sl,a['side']=='sell',bps,decimals)),'slippage_bps':bps,'grouping':'normalTpsl'}
    elif action=='cancel':
        oid=a.get('oid')
        if not isinstance(oid,int) or oid<=0:problem('oid_invalid','ID ordine positivo richiesto; consulta orders.')
        orders=info(p,{'type':'openOrders','user':p['target'],'dex':b['dex']})
        if not any(o['oid']==oid and o['coin']==b['coin'] for o in orders):problem('order_not_open','Ordine non presente sul target/mercato. Leggi lo stato; non inventare ID o ripetere cancellazioni.')
        b['oid']=oid
    elif action=='leverage':
        lev=a.get('leverage')
        if not isinstance(lev,int) or not 1<=lev<=int(row['maxLeverage']):problem('leverage_invalid','Leva ammessa 1–'+str(row['maxLeverage']))
        b.update(leverage=lev,cross=bool(a.get('cross',True)))
    else:
        vault=address(a.get('vault'));amount=number(a.get('amount'),'USDC')
        if amount!=amount.quantize(Decimal('.000001')):problem('usd_precision','USDC ammette massimo 6 decimali.')
        detail=info(p,{'type':'vaultDetails','vaultAddress':vault,'user':p['account']})
        if not detail or detail.get('isClosed'):problem('vault_unavailable','Vault non disponibile o chiusa.')
        if action=='vault_deposit' and not detail.get('allowDeposits'):problem('deposits_closed','Vault non aperta ai depositi.')
        if action=='vault_withdraw' and amount>Decimal(str(detail.get('maxWithdrawable',0))):problem('withdraw_limit','Importo oltre maxWithdrawable: controlla followerState, lockup e disponibilità reali.')
        b.update(vault=vault,amount=textnum(amount),usd_units=int(amount*1000000))
    return b

def requested_coin(agent,body):
    request=getattr(agent,'current_market_request',None)
    coin=request.get('coin') if request else None
    if coin and body.get('action') in ('order','leverage') and body.get('coin')!=coin:
        problem('request_coin_mismatch','La richiesta attuale riguarda '+coin+', non '+str(body.get('coin'))+'. Nessuna anteprima/firma per la coppia storica; rileggi lo snapshot del coin richiesto.',requested_coin=coin,received_coin=body.get('coin'))


def planning_capacity(agent,p,body):
    """Catch invalid unsigned sizes using a matching native planning snapshot.

    Submission still performs independent fresh capacity/authority checks.
    """
    from hyperliquid_authority import configured
    if configured(p):return # Fresh exchange-side capacity is checked at submit/signing.
    if body.get('action')!='order' or body.get('reduce_only'):return
    calls={c['id']:c.get('function',{}) for m in agent.messages for c in m.get('tool_calls',[])}
    for message in reversed(agent.messages):
        if message.get('role')!='tool' or calls.get(message.get('tool_call_id'),{}).get('name')!='hyperliquid_read':continue
        try:result=json.loads(message.get('content',''))
        except (ValueError,TypeError):continue
        if result.get('resource')!='snapshot' or result.get('profile')!=p['name'] or result.get('network')!=p['network'] or str(result.get('queried_address','')).lower()!=p['target'].lower():continue
        data=result.get('data',{});asset=data.get('asset',{});risk=data.get('risk',{})
        if asset.get('coin')!=body.get('coin') or risk.get('account_mode')!='unifiedAccount':continue
        if not isinstance(result.get('retrieved_ms'),int) or time.time()*1000-result['retrieved_ms']>120000:
            problem('financial_snapshot_required','Snapshot di pianificazione scaduto: rileggi quello del coin richiesto prima di un’altra anteprima.',recovery_coin=body['coin'],recovery_profile=p['name'])
        try:check_capacity(risk,asset,body,Decimal(body['notional_usd']))
        except HLProblem as exc:
            cap=exc.details.get('max_notional_before_other_limits_usd')
            markets=data.get('market',{}).get('markets',[])
            decimals=next((x['meta'].get('szDecimals') for x in markets if x.get('meta',{}).get('name')==body['coin']),None)
            if cap is not None and type(decimals) is int and 0<=decimals<=18:
                price=Decimal(body['notional_usd'])/Decimal(body['size'])
                maximum=min(Decimal(cap)/price,*(Decimal(x) for x in asset['maxTradeSzs']))
                exc.details['mathematical_max_size']=textnum(maximum.quantize(Decimal(10)**-decimals,rounding=ROUND_DOWN))
                exc.details['max_size_hint']=textnum((maximum*Decimal('.99')).quantize(Decimal(10)**-decimals,rounding=ROUND_DOWN))
                exc.details['planning_headroom_bps']=100
                exc.details['hint_note']='Usa al massimo max_size_hint come base di un NUOVO piano: quantità arrotondata per difetto con 1% di margine contro variazioni di prezzo. Non è autorizzazione e non modifica ordini. Non arrotondare verso l’alto né riutilizzare una quantità a ridosso del massimo matematico. Submit e limiti economici sono ricontrollati.'
            raise
        return


def preview(agent,home,a):
    requested_coin(agent,a)
    p=profile(home,a.get('profile'))
    if a.get('action')=='order' and not getattr(agent,'hyperliquid_controller',None):
        prompt=next((m.get('content','') for m in reversed(agent.messages) if m.get('role')=='user' and isinstance(m.get('content'),str)),'')
        try:size=Decimal(a.get('size',''))
        except (InvalidOperation,TypeError):size=None
        amounts=[Decimal(m.group(1).replace(',','.')) for m in re.finditer(r'(?i)(?<![\w.])(\d+(?:[.,]\d+)?)\s*(?:USDC|USDT|USD|\$)\b',prompt)]
        if size is not None and size in amounts and str(a.get('coin') or '').upper() not in ('USDC','USDT','USD'):
            problem('size_unit_mismatch','size è in unità dell’asset ('+str(a.get('coin'))+'), non in USDC. Non usare la cifra del budget come quantità BTC/ETH. Chiedi prima se l’importo indicato è margine o nozionale totale; poi calcola la quantità con prezzo attuale e szDecimals.',
                    question='L’importo in USDC indica il margine da impegnare o il valore totale della posizione?',options=['Margine da impegnare','Valore totale della posizione'],size_unit='base_asset')
    b=normalize_action(p,a)
    from hyperliquid_authority import configured,require_current
    if configured(p):b['execution_generation']=require_current(home,p)['generation']
    execution_context=getattr(agent,'hyperliquid_execution_context',None)
    if execution_context and b['action']=='order':b['expected_leverage']=execution_context['leverage']
    planning_capacity(agent,p,b)
    from hyperliquid_protection import new_risk
    if new_risk(b) and getattr(agent,'financial_turn_started_at',None) is not None:
        observed=getattr(agent,'hyperliquid_analysis',{}).get((p['name'],b['coin'])) or {}
        if observed.get('scope')!=fingerprint(p) or not 0<=time.time()*1000-observed.get('as_of_ms',0)<=120000:
            problem('market_analysis_required','Prima di una nuova entrata usa hyperliquid_analyze: servono candele chiuse su due timeframe, volatilità, book, funding e costi. Valuta long, short e HOLD; niente TP sicuri.',recovery_tools=[{'name':'hyperliquid_analyze','arguments':{'profile':p['name'],'coin':b['coin']}}])
        if not observed.get('data_ready'):problem('market_analysis_unavailable','Dati analitici incompleti o non validi: HOLD, nessun nuovo ingresso. Non inventare indicatori e non ripetere lo stesso preview.',analysis_errors=observed.get('errors'),evidence_id=observed.get('evidence_id'))
        b['analysis_evidence']={k:observed[k] for k in ('evidence_id','as_of_ms','scope')}
    # Same user turn or scheduled cycle reuses the same intent even after model handoff.
    context=str(agent.session)+':'+str(sum(m.get('role')=='user' for m in agent.messages))
    if execution_context:context=execution_context['id']
    if getattr(agent,'hyperliquid_controller',None):
        if configured(p) and getattr(agent,'key_only_cycle',None):
            b['cycle']=agent.key_only_cycle;context=str(agent.session)+':'+agent.key_only_cycle
        else:
            with database(home) as db:g=current_grant(db,p['name'])
            if not g or not g.get('pending'):problem('cycle_missing','Ciclo locale non attivo.')
            b['cycle']=g['pending'];context=str(agent.session)+':'+g['pending']
    canonical={k:v for k,v in b.items() if k not in ('mid','notional_usd','limit_notional_usd') and not (execution_context and k=='analysis_evidence')}
    if b.get('order_kind')=='market':canonical.pop('price',None)
    intent=hashlib.sha256(json.dumps({'args':canonical,'scope':fingerprint(p),'context':context},sort_keys=True).encode()).hexdigest()
    ident=uuid.uuid4().hex[:24];b['cloid']='0x'+uuid.uuid4().hex
    if b.get('bracket'):b['bracket']['tp_cloid']='0x'+uuid.uuid4().hex;b['bracket']['sl_cloid']='0x'+uuid.uuid4().hex
    with database(home) as db:
        db.execute('INSERT OR IGNORE INTO actions VALUES(?,?,?,?,?,?,?,?)',(ident,intent,p['name'],'prepared',time.time(),time.time(),json.dumps(b),None));db.commit()
        r=receipt(db.execute('SELECT * FROM actions WHERE intent=?',(intent,)).fetchone())
        if r['state']=='prepared' and time.time()-r['created']>120:
            # This intent has never been authorized/sent. Refresh live values,
            # retain its ID/client IDs, and require a fresh review on submit.
            b['cloid']=r['body']['cloid']
            if b.get('bracket'):
                for key in ('tp_cloid','sl_cloid'):b['bracket'][key]=r['body']['bracket'][key]
            previous={'previous_created':r['created'],'preview_refreshes':(r.get('response') or {}).get('preview_refreshes',0)+1,'never_authorized':True}
            db.execute("UPDATE actions SET body=?,created=?,updated=?,response=? WHERE id=? AND state='prepared'",(json.dumps(b),time.time(),time.time(),json.dumps(previous),r['id']));db.commit()
            r=receipt(db.execute('SELECT * FROM actions WHERE id=?',(r['id'],)).fetchone())
    return {'preview':r,'expires_at':r['created']+120,'instruction':'Unsigned preview only. Submit requires local review or already configured auto grant. Repeated same intent returns existing receipt; uncertain actions must be reconciled, never replaced.'}

def guard_grant(p,g,b,session,db,exclude_id=None):
    from hyperliquid_protection import require_bracket
    require_bracket(b,client_ids=True)
    if not g or not g.get('active') or g['expires']<=time.time():problem('auto_inactive','Trading automatico non autorizzato o scaduto: usa il pannello locale.')
    storage_home=Path(db.execute('PRAGMA database_list').fetchone()[2]).parent.parent
    if g.get('stop_epoch')!=stop_epoch(storage_home,p['name']):problem('auto_stopped','Arresto locale richiesto: nessun nuovo invio automatico.')
    if g['scope']!=fingerprint(p) or (g.get('authority_scope')!='account' and g['session']!=str(Path(session).resolve())):problem('auto_scope','Autorizzazione riferita ad altro account/rete/target/sessione.')
    if b['action'] not in ('order','cancel','leverage'):problem('auto_fund_movement','Depositi e prelievi richiedono conferma manuale, fuori dal trading automatico.')
    if b.get('coin') not in g['coins']:problem('coin_not_allowed','Mercato fuori dalla lista autorizzata: '+str(b.get('coin')))
    risk=risk_state(p,b.get('dex',''))
    if g.get('account_mode',risk['account_mode'] if risk['account_mode']!='unifiedAccount' else None)!=risk['account_mode']:problem('account_mode_changed','Modalità conto cambiata: serve nuova autorizzazione sul rischio effettivo.')
    if risk['account_mode']=='unifiedAccount' and g.get('risk_version')!=risk['version']:problem('risk_policy_changed','Contabilità rischio aggiornata: serve una nuova autorizzazione, nessun riuso del vecchio budget.')
    check_flows(p,g)
    state=risk.get('state') or {'assetPositions':[{'position':x} for x in risk['positions']]}
    equity=Decimal(risk['risk_equity_usd'])
    if Decimal(g['baseline_equity'])-equity>=Decimal(g['max_loss_usd']):problem('loss_stop','Soglia di perdita/equity raggiunta. Trading automatico arrestato; nessuna chiusura o trasferimento implicito.')
    if b['action']=='leverage' and b['leverage']>g['max_leverage']:problem('leverage_cap','Leva oltre il limite autorizzato.')
    if b['action']=='leverage':
        proof=g.get('leverage_preflight',{}).get(b['coin'],{})
        if proof.get('cycle')==g.get('pending') and proof.get('state')=='verified':
            problem('leverage_preflight_locked','Leva già verificata dal programma per questo ciclo. Nessun altro aggiornamento: consulta la prova registrata.')
    if b['action']=='cancel' and g.get('strategy_mode')=='auto':
        orders=info(p,{'type':'frontendOpenOrders','user':p['target'],'dex':b.get('dex','')})
        selected=next((o for o in orders if o['oid']==b['oid']),None)
        positioned=any(x['position']['coin']==b['coin'] and Decimal(x['position']['szi'])!=0 for x in state['assetPositions'])
        if selected and selected.get('reduceOnly') and positioned:problem('protected_exit','Non annullare automaticamente un’uscita reduce-only con posizione aperta. Mantieni le protezioni o chiudi prima la posizione; per una modifica manuale ferma auto e rivedi l’azione.')
    if b['action']=='order':
        positions=[x['position'] for x in state['assetPositions']]
        if b['reduce_only']:
            pos=next((x for x in positions if x['coin']==b['coin']),None)
            if not pos or Decimal(pos['szi'])==0 or (Decimal(pos['szi'])>0)==(b['side']=='buy') or Decimal(b['size'])>abs(Decimal(pos['szi'])):problem('not_reducing','Reduce-only non coerente con la posizione reale: rileggi l’account.')
            return
        current_mid=number(info(p,{'type':'allMids','dex':b.get('dex','')}).get(b['coin']),'prezzo corrente per risk check')
        notional=max(Decimal(b['notional_usd']),Decimal(b['size'])*current_mid)
        if notional>Decimal(g['max_order_usd']):problem('order_cap','Notional oltre il limite per ordine.')
        active=leverage_within_limit(p,b['coin'],g['max_leverage'])
        check_capacity(risk,active,b,notional)
        orders=risk.get('orders') if risk['account_mode']=='unifiedAccount' else info(p,{'type':'frontendOpenOrders','user':p['target'],'dex':b.get('dex','')})
        gross=Decimal(risk['gross_exposure_usd']) if risk['account_mode']=='unifiedAccount' else sum((abs(Decimal(x['positionValue'])) for x in positions),Decimal(0))+sum((Decimal(x['sz'])*Decimal(x['limitPx']) for x in orders if not x.get('reduceOnly',False)),Decimal(0))
        if gross+notional>Decimal(g['max_exposure_usd']):problem('exposure_cap','Esposizione lorda (posizioni+ordini+nuovo ordine) oltre il limite.')
        rows=[receipt(r) for r in db.execute("SELECT * FROM actions WHERE profile=? AND state NOT IN ('prepared','blocked','rejected') AND updated>=?",(p['name'],g['created'])) if r['id']!=exclude_id]
        trades=[r for r in rows if r['body']['action']=='order' and not r['body'].get('reduce_only')]
        if sum(r['updated']>time.time()-3600 for r in trades)>=g['max_orders_hour']:problem('frequency_cap','Numero massimo di ordini per ora raggiunto.')
        turnover=sum((Decimal((r.get('response') or {}).get('notional_reserved',r['body']['notional_usd'])) for r in trades),Decimal(0))
        if turnover+notional>Decimal(g['max_turnover_usd']):problem('turnover_cap','Budget cumulativo autorizzato raggiunto.')
        return textnum(notional)

def submit(agent,home,ident):
    from order_manager import submit_native
    return submit_native(agent,home,ident)


def _submit_native(agent,home,ident):
    import os
    if os.getenv('FREELLMAPI_READ_ONLY')=='1':problem('read_only_signing_forbidden','Processo di sola lettura: firma e invio disabilitati prima di qualunque accesso alle chiavi.')
    if agent.mode=='plan' or getattr(agent,'depth',0):problem('coordinator_only','Firma disponibile solo nel coordinatore operativo.')
    with database(home) as db:
        row=receipt(db.execute('SELECT * FROM actions WHERE id=?',(ident,)).fetchone())
        if not row:
            if isinstance(ident,str) and re.fullmatch(r'0x[0-9a-fA-F]{32}',ident):
                problem('preview_id_namespace','Submit richiede l’ID locale della ricevuta, non il client ID exchange. Risolvi prima con operations; nessun invio eseguito.',recovery_tools=[{'name':'hyperliquid_operations','arguments':{'id':ident}}])
            problem('preview_missing','ID anteprima inesistente: consulta hyperliquid_operations.',recovery_tools=[{'name':'hyperliquid_operations','arguments':{}}])
        if row['state']!='prepared':return {'reused':True,'receipt':row,'instruction':'Nessun invio ripetuto; riconcilia gli esiti incerti tramite operations.'}
        p=profile(home,row['profile']);b=row['body'];g=current_grant(db,p['name'])
        requested_coin(agent,b)
        from hyperliquid_protection import require_bracket
        require_bracket(b,client_ids=True) # Includes old previews made before TP/SL became mandatory.
        if getattr(agent,'hyperliquid_controller',None) and p['name']!=agent.hyperliquid_controller:problem('auto_scope','Anteprima riferita a un profilo diverso dal ciclo autorizzato.')
        if b['scope']!=fingerprint(p):problem('profile_changed','Profilo cambiato dopo anteprima: nessun invio.')
        if time.time()-row['created']>120:problem('preview_expired','Anteprima scaduta. Rileggi dati e prepara una nuova anteprima; nessun ordine inviato.')
        unresolved=[receipt(x) for x in db.execute("SELECT * FROM actions WHERE state IN ('sending','uncertain','authorized','partial')")]
        if any(x['body']['network']==p['network'] and x['body']['account'].lower()==p['account'].lower() for x in unresolved):problem('outcome_unknown','Esito precedente incerto/in corso sullo stesso master/rete: riconcilia prima di altre azioni.')
        permissions(p,True,home)
        auth={'scope':fingerprint(p),'expires':time.time()+30,'review_hash':hashlib.sha256(json.dumps(b,sort_keys=True).encode()).hexdigest()}
        from hyperliquid_authority import configured,request_context,capacity,require_current
        if configured(p) and b['action'] in ('order','cancel','leverage'):
            origin=request_context(agent,home,p,b);capacity(home,p,b);authority=require_current(home,p)
            auth.update(mode='key_only',generation=authority['generation'],source=origin['source'],request_id=origin['id'],expires=time.time()+30)
        elif g and g.get('active') and not configured(p):
            guard_grant(p,g,b,agent.session,db);auth.update(mode='auto',session=str(Path(agent.session).resolve()),generation=g['generation'])
        else:
            manual_capacity(p,b,home)
            if not agent.interactive:problem('local_authorization_required','Nessun grant automatico attivo. Apri il pannello Hyperliquid o usa una sessione interattiva per approvare l’azione concreta.')
            from questions import ask
            answer=ask('Inviare questa operazione Hyperliquid?\n'+json.dumps(b,ensure_ascii=False,indent=2),['Confermo invio','Annulla'])
            if answer.get('answer')!='Confermo invio':return {'denied':True,'sent':False}
            auth['mode']='manual'
        changed=db.execute("UPDATE actions SET state='authorized',response=?,updated=? WHERE id=? AND state='prepared' AND body=?",(json.dumps({'authorization':auth}),time.time(),ident,row['body'] and json.dumps(b))).rowcount;db.commit()
        if not changed:problem('preview_changed','Anteprima modificata o già presa in carico: nessun invio duplicato.')
    import subprocess,sys
    try:
        child=subprocess.run([sys.executable,'-X','utf8',str(Path(__file__).with_name('hyperliquid_signer.py')),'--home',str(Path(home).resolve())],input=json.dumps({'id':ident}),text=True,capture_output=True,timeout=45,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if child.returncode or not child.stdout.strip():problem('signer_unavailable','Processo firma terminato: consulta operations; non ripetere l’invio.')
        result=json.loads(child.stdout)
    except (subprocess.TimeoutExpired,json.JSONDecodeError):
        with database(home) as db:db.execute("UPDATE actions SET state='uncertain',updated=? WHERE id=? AND state IN ('authorized','sending')",(time.time(),ident));db.commit()
        return {'state':'uncertain','id':ident,'instruction':'Signer timeout/interrotto: stato conservato. Riconcilia prima di ogni altra operazione; nessun retry automatico.'}
    if isinstance(result,dict) and result.get('sent') is False and result.get('code') in ('read_only_signing_forbidden',):
        # These guards return before keys, signing or /exchange. Preserve the
        # authorization evidence, but do not leave a definitely unsent intent pending.
        with database(home) as db:
            current=receipt(db.execute('SELECT * FROM actions WHERE id=?',(ident,)).fetchone())
            if current and current['state']=='authorized':
                response=dict(current.get('response') or {});response['pre_signing_rejection']=result
                db.execute("UPDATE actions SET state='blocked',response=?,updated=? WHERE id=? AND state='authorized'",(json.dumps(response),time.time(),ident));db.commit()
                result={**result,'receipt':receipt(db.execute('SELECT * FROM actions WHERE id=?',(ident,)).fetchone()),'resent':False}
    from hyperliquid_protection import finalize
    return finalize(home,result)

def resolve_operation(db,ident,name=None):
    """Resolve local receipt IDs or a UNIQUE recorded client ID, for reads only."""
    if name and not re.fullmatch(r'[A-Za-z0-9_-]{1,48}',name):problem('profile_name','Nome profilo non valido.')
    selected=' AND profile=?' if name else '';values=(ident,name) if name else (ident,)
    row=receipt(db.execute('SELECT * FROM actions WHERE id=?'+selected,values).fetchone())
    if row:return row,{'namespace':'operation_id','requested':ident,'operation_id':row['id']}
    matches=[]
    if isinstance(ident,str) and re.fullmatch(r'0x[0-9a-fA-F]{32}',ident):
        rows=db.execute('SELECT * FROM actions'+(' WHERE profile=?' if name else ''),(name,) if name else ()).fetchall()
        for raw in rows:
            candidate=receipt(raw);body=candidate['body'];bracket=body.get('bracket') or {}
            ids={'entry':body.get('cloid'),'tp':bracket.get('tp_cloid'),'sl':bracket.get('sl_cloid')}
            legs=[leg for leg,value in ids.items() if isinstance(value,str) and value.lower()==ident.lower()]
            if legs:matches.append((candidate,legs))
    needed=[{'name':'hyperliquid_operations','arguments':{'profile':name} if name else {}}]
    if len(matches)==1:
        row,legs=matches[0];return row,{'namespace':'client_order_id','requested':ident,'operation_id':row['id'],'legs':legs,'read_only':True}
    if len(matches)>1:problem('operation_id_ambiguous','Client ID presente in più ricevute: usa un ID locale esatto.',valid_operation_ids=[r['id'] for r,_ in matches],recovery_tools=needed)
    rows=db.execute('SELECT id,profile,state FROM actions '+('WHERE profile=? ' if name else '')+'ORDER BY created DESC LIMIT 8',(name,) if name else ()).fetchall()
    problem('operation_missing','ID non trovato nel registro locale. operations legge ricevute: usa id locale, oppure un client ID già registrato. Non usare un oid numerico come id locale.',valid_operations=[dict(r) for r in rows],recovery_tools=needed)


def operations(home,ident=None,name=None):
    with database(home) as db:
        if ident:
            row,lookup=resolve_operation(db,ident,name);ident=row['id']
            if row['state'] in ('uncertain','sending','authorized','partial'):
                p=profile(home,row['profile'])
                if row['body'].get('scope')!=fingerprint(p):problem('profile_changed','Scope cambiato: nessuna lettura exchange su un altro account o rete.')
            if row['state']=='uncertain' and row['body']['action']=='leverage':
                b=row['body'];p=profile(home,row['profile'])
                if b['scope']!=fingerprint(p):problem('profile_changed','Scope cambiato: nessuna riconciliazione su un altro profilo.')
                observed=checked_leverage(info(p,{'type':'activeAssetData','user':b['target'],'coin':b['coin']}),True)
                expected={'value':b['leverage'],'type':'cross' if b['cross'] else 'isolated'}
                result=dict(row.get('response') or {});result['leverage_verification']={'expected':expected,'observed':observed,'verified':observed==expected,'checked_ms':int(time.time()*1000),'reconciled':True,'evidence':'Current server setting, not proof of original transaction execution.'}
                db.execute('UPDATE actions SET state=?,response=?,updated=? WHERE id=?',('acknowledged' if observed==expected else 'uncertain',json.dumps(result),time.time(),ident));db.commit()
                return {'receipt':receipt(db.execute('SELECT * FROM actions WHERE id=?',(ident,)).fetchone()),'resent':False,'lookup':lookup,'instruction':'Sola lettura dello stato attuale; nessun reinvio o riavvio automatico del grant.'}
            if row['body'].get('bracket') and row['state'] in ('uncertain','sending','authorized','partial'):
                from hyperliquid_reconciliation import reconcile
                return {**reconcile(home,row,info),'lookup':lookup}
            if row['state'] in ('uncertain','sending','authorized') and row['body']['action']=='order':
                p=profile(home,row['profile']);observed=info(p,{'type':'orderStatus','user':row['body']['target'],'oid':row['body']['cloid']})
                if observed.get('status')=='order':
                    state=observed['order']['status'];db.execute('UPDATE actions SET state=?,response=?,updated=? WHERE id=?',(state,json.dumps({'reconciled':True,'observed':observed}),time.time(),ident));db.commit()
                    row=receipt(db.execute('SELECT * FROM actions WHERE id=?',(ident,)).fetchone())
                return {'receipt':row,'observed':observed,'resent':False,'lookup':lookup}
            return {'receipt':row,'resent':False,'lookup':lookup}
        rows=db.execute('SELECT * FROM actions '+('WHERE profile=? ' if name else '')+'ORDER BY created DESC LIMIT 30',(name,) if name else ()).fetchall()
        return {'operations':[receipt(r) for r in rows],'temporal_scope':'historical_receipts',
                'instruction':'accepted/filled is the persisted execution result, not current open exposure. A position may already be closed. Read snapshot/risk for live positions and frontendOpenOrders for live orders; do not replay historical actions.'}

def stop(home,name=None):
    if name and not re.fullmatch(r'[A-Za-z0-9_-]{1,48}',name):problem('profile_name','Nome profilo non valido.')
    # Out-of-band latch is durable even while a signing worker holds a DB write lock.
    atomic_bytes(folder(home)/('STOP-'+name if name else 'STOP_ALL'),uuid.uuid4().hex.encode('ascii'))
    persisted=True
    try:
        with database(home,timeout=.2) as db:
            rows=db.execute('SELECT profile,body FROM grants'+(' WHERE profile=?' if name else ''),(name,) if name else ()).fetchall()
            for n,raw in rows:
                g=json.loads(raw);g.update(active=False,stopped_reason='Local stop command');db.execute('UPDATE grants SET body=? WHERE profile=?',(json.dumps(g),n))
            db.commit()
    except sqlite3.OperationalError:persisted=False
    return {'automation_stopped':True,'db_updated':persisted,'note':'Nessun nuovo invio automatico. Richieste già in volo possono completarsi; ordini aperti e posizioni restano da gestire esplicitamente.'}

def validate_settings(settings):
    clean=dict(settings)
    for key,label in SETTING_LABELS.items():
        raw=str(settings.get(key,'')).strip()
        if not raw:problem('setting_required',label+': campo obbligatorio. Inserisci il tuo limite prima di autorizzare il trading.')
        if not re.fullmatch(r'\+?\d+(?:[.,]\d+)?',raw):problem('setting_format',label+': usa ad esempio 10,50 oppure 10.50, senza valuta o separatori delle migliaia.')
        val=number(raw.replace(',','.'),label)
        if key in ('max_leverage','max_orders_hour','interval','duration_minutes') and val!=val.to_integral():problem('setting_integer',label+': serve un numero intero.')
        clean[key]=textnum(val)
    if not str(settings.get('coins','')).strip():problem('coins_required','Inserisci almeno un mercato, ad esempio BTC.')
    mode=settings.get('strategy_mode') or ('manual' if str(settings.get('strategy','')).strip() else 'auto')
    if mode not in ('auto','manual'):problem('strategy_mode','Gestione strategia: auto oppure manual.')
    clean['strategy_mode']=mode
    if mode=='auto':clean['strategy']=AUTO_STRATEGY
    elif not 10<=len(str(settings.get('strategy','')).strip())<=4000:problem('strategy_required','In modalità personalizzata descrivi strategia e condizioni HOLD (10–4000 caratteri).')
    return clean

def arm(home,name,settings,session):
    """Called only by the LOCAL panel after displaying and confirming the entire grant."""
    settings=validate_settings(settings)
    p=profile(home,name);permissions(p,True,home)
    coins=list(dict.fromkeys(str(settings['coins']).replace(' ', '').split(',')))
    if not coins or any(not c for c in coins):problem('coins_required','Almeno un mercato esatto richiesto.')
    # Initial auto controller uses one perp DEX per grant, with shared risk totals.
    specs=[market(p,c) for c in coins];dexes={dex for _,dex in specs}
    if len(dexes)!=1:problem('dex_scope','Usa mercati dello stesso perp DEX per un grant; crea profili separati per altri DEX.')
    values={k:textnum(number(settings[k],k)) for k in ('max_order_usd','max_exposure_usd','max_loss_usd','max_turnover_usd')}
    if Decimal(values['max_order_usd'])>Decimal(values['max_exposure_usd']):problem('risk_limits','Il limite per ordine supera il limite esposizione.')
    numeric={k:int(settings[k]) for k in ('max_leverage','max_orders_hour','interval','duration_minutes')}
    if not 1<=numeric['max_leverage']<=100 or not 1<=numeric['max_orders_hour']<=60 or not 60<=numeric['interval']<=3600 or not 1<=numeric['duration_minutes']<=1440:problem('auto_limits','Leva1–100, ordini/ora1–60, intervallo60–3600s, durata1–1440min.')
    strategy=str(settings.get('strategy','')).strip()
    if not 10<=len(strategy)<=4000:problem('strategy_required','Descrivi una strategia concreta (10–4000 caratteri), comprese le condizioni per non operare.')
    dex=next(iter(dexes));risk=risk_state(p,dex);baseline=Decimal(risk['risk_equity_usd'])
    if baseline<=0:problem('no_equity','Account/target senza equity perps sulla rete selezionata. Nessun deposito o wallet creato automaticamente.')
    grant={**values,**numeric,'profile':name,'scope':fingerprint(p),'network':p['network'],'account':p['account'],'target':p['target'],
           'session':str(Path(session).resolve()),'generation':uuid.uuid4().hex,'created':time.time(),'expires':time.time()+numeric['duration_minutes']*60,'next_due':time.time()+1,
           'active':True,'controller':'account_service','authority_scope':'account','strategy':strategy,'strategy_mode':settings['strategy_mode'],'coins':coins,'dex':dex,'baseline_equity':textnum(baseline),'baseline_since_ms':risk.get('window_start_ms',int(time.time()*1000)),'account_mode':risk['account_mode'],'risk_version':risk['version'],'pending':None,'stop_epoch':stop_epoch(home,name)}
    with database(home) as db:
        unresolved=db.execute("SELECT id FROM actions WHERE profile=? AND state IN ('sending','uncertain','authorized','partial')",(name,)).fetchone()
        if unresolved:problem('outcome_unknown','Riconcilia prima l’operazione '+unresolved[0]+'. Nessun nuovo grant.')
        db.execute('INSERT OR REPLACE INTO grants VALUES(?,?)',(name,json.dumps(grant)));db.commit()
    import trading_service
    try:sharing=trading_service.sync_account_policy(home,grant)
    except Exception:sharing={'account_policy_shared':False,'code':'account_policy_sync_failed','orders_sent':0}
    return {'auto_authorized':True,'grant':grant,**sharing,'note':'Unica autorizzazione account, condivisa da chat e servizio persistente. Budget, scadenza e STOP restano verificati; nessun rinnovo automatico o ordine inviato dal salvataggio.'}

def poll(home,session):
    with database(home) as db:
        db.execute('BEGIN IMMEDIATE')
        for name,raw in db.execute('SELECT profile,body FROM grants').fetchall():
            from hyperliquid_authority import configured
            if configured(profile(home,name)):continue # Key connection never revives legacy scheduled grants.
            g=json.loads(raw)
            if g.get('controller')=='account_service':
                import trading_service
                if trading_service.worker_running(home,name):continue # Single planner when persistent worker owns this account.
            if not g.get('active') or g['session']!=str(Path(session).resolve()):continue
            if g['expires']<=time.time() or g.get('stop_epoch')!=stop_epoch(home,name):g.update(active=False,stopped_reason='Authorization expired or local stop')
            elif g.get('pending'):continue # never replay an interrupted trading cycle after restart
            elif g['next_due']<=time.time():
                g.update(pending=uuid.uuid4().hex,next_due=time.time()+g['interval'])
                db.execute('UPDATE grants SET body=? WHERE profile=?',(json.dumps(g),name));db.commit();return '/_hyperliquid '+name
            db.execute('UPDATE grants SET body=? WHERE profile=?',(json.dumps(g),name))
        db.commit()
    return None

def preflight_leverage(agent,home,name,output=lambda text:None):
    """Native, grant-scoped reduction before inference. Never raises leverage or retries a write."""
    if agent.mode=='plan' or getattr(agent,'depth',0):problem('coordinator_only','Preflight leva disponibile solo nel coordinatore operativo.')
    p=profile(home,name);bound=False
    def record(expected,coin,proof):
        with database(home) as db:
            db.execute('BEGIN IMMEDIATE');latest=current_grant(db,name)
            if not latest or not latest.get('active') or latest.get('generation')!=expected['generation'] or latest.get('pending')!=expected['pending'] or latest['expires']<=time.time() or latest.get('stop_epoch')!=stop_epoch(home,name):
                problem('auto_stopped','Grant revocato/cambiato durante la verifica leva; nessun nuovo ingresso.')
            latest.setdefault('leverage_preflight',{})[coin]=proof
            db.execute('UPDATE grants SET body=? WHERE profile=?',(json.dumps(latest),name));db.commit()
    try:
        with database(home) as db:g=current_grant(db,name)
        if not g or not g.get('active') or not g.get('pending') or g['expires']<=time.time():problem('auto_inactive','Preflight senza ciclo autorizzato: nessuna firma.')
        if g['scope']!=fingerprint(p) or g['session']!=str(Path(agent.session).resolve()) or getattr(agent,'hyperliquid_controller',None)!=name:
            problem('auto_scope','Preflight riferito ad altro account/rete/sessione.')
        bound=True
        if g.get('stop_epoch')!=stop_epoch(home,name):problem('auto_stopped','Arresto locale già richiesto.')
        proofs={}
        for coin in g['coins']:
            previous=g.get('leverage_preflight',{}).get(coin,{})
            if previous.get('cycle')==g['pending'] and previous.get('state')!='verified':
                problem('leverage_preflight_incomplete','Tentativo leva già registrato in questo ciclo. Consulta operations; nessun reinvio.')
            before=checked_leverage(info(p,{'type':'activeAssetData','user':p['target'],'coin':coin}),True)
            proof={'cycle':g['pending'],'state':'verified','before':before,'observed':before,'verified_ms':int(time.time()*1000),'operation_id':previous.get('operation_id') if previous.get('cycle')==g['pending'] else None}
            if before['value']>g['max_leverage']:
                if previous.get('cycle')==g['pending']:problem('leverage_changed','Leva cambiata dopo il preflight di questo ciclo. Nessun secondo invio.')
                output('Hyperliquid · '+coin+' · leva '+str(before['value'])+'× → '+str(g['max_leverage'])+'×: un solo invio, poi verifica sul server.')
                row=preview(agent,home,{'profile':name,'action':'leverage','coin':coin,'leverage':g['max_leverage'],'cross':before['type']=='cross'})['preview']
                proof.update(state='updating',expected={'value':g['max_leverage'],'type':before['type']},operation_id=row['id']);record(g,coin,proof)
                submit(agent,home,row['id'])
                with database(home) as db:observed=receipt(db.execute('SELECT * FROM actions WHERE id=?',(row['id'],)).fetchone())
                if not observed or observed['state']!='acknowledged':problem('leverage_preflight_failed','Invio leva non confermato. ID '+row['id']+': consulta operations; nessun ordine o reinvio.')
                # An SDK acknowledgement alone does not prove the setting was applied.
                after=None
                try:
                    after=checked_leverage(info(p,{'type':'activeAssetData','user':p['target'],'coin':coin}),True)
                    if after!=proof['expected']:problem('leverage_not_applied','Il server non conferma la leva richiesta; automazione sospesa senza ripetere l’invio.')
                finally:
                    verified=after==proof['expected']
                    verification={'expected':proof['expected'],'observed':after,'verified':verified,'checked_ms':int(time.time()*1000)}
                    with database(home) as db:
                        current=receipt(db.execute('SELECT * FROM actions WHERE id=?',(row['id'],)).fetchone())
                        result=dict(current.get('response') or {});result['leverage_verification']=verification
                        db.execute('UPDATE actions SET state=?,response=?,updated=? WHERE id=?',('acknowledged' if verified else 'uncertain',json.dumps(result),time.time(),row['id']));db.commit()
                proof.update(state='verified',observed=after,verified_ms=int(time.time()*1000))
            record(g,coin,proof);proofs[coin]=proof
            output('Hyperliquid · '+coin+' · leva verificata sul server: '+str(proof['observed']['value'])+'× ('+proof['observed']['type']+').')
        return proofs
    except BaseException:
        # Durable latch also blocks a signing worker if journal persistence is busy.
        if bound:stop(home,name)
        raise

def controller_prompt(home,name,session):
    p=profile(home,name)
    with database(home) as db:g=current_grant(db,name)
    if not g or not g.get('active') or not g.get('pending') or g['session']!=str(Path(session).resolve()) or g['scope']!=fingerprint(p) or g['expires']<=time.time():problem('controller_inactive','Controller non autorizzato per questa sessione/scope.')
    plan_path=folder(home)/(name+'.strategy.json')
    previous=json.loads(plan_path.read_text(encoding='utf-8')).get('current_plan') if plan_path.is_file() else None
    return ('Ciclo trading Hyperliquid autorizzato localmente. Usa SOLO gli strumenti hyperliquid_* disponibili. '
            'Leggi una sola read resource=snapshot con coin e limit=12: include rischio Unified, stato, ordini, leva, mercato e candele chiuse. Riusa questa evidenza; non rileggere account/candles/status senza un motivo concreto. '
            'Leggi dati correnti di account/ordini/candele prima di scegliere. Non usare chiavi, shell, web, skill o memoria Obsidian. '
            'Se dati insufficienti o strategia senza segnale, rispondi HOLD con motivo verificabile. Non inventare dati o operazioni. '
            'Verifica balance_context/account_mode: unifiedAccount e portfolioMargin usano saldi spot; perps accountValue=0 non dimostra assenza di fondi. Non stimare il valore di altri token dalla sola quantità. '
            'Per un segnale coerente con la strategia, prepara anteprima e submit una sola volta; conserva cloid/ID. '
            'Sii conciso: puoi salvare il piano e preparare anteprima nello stesso turno di strumenti, poi submit con ID ricevuto e verifica operations. Non annunciare azioni future in luogo di chiamate native. '
            'Niente fondi nuovi, depositi o prelievi. Non cambiare mercati, rete o limiti. '
            'Il preflight leva è nativo e registrato nella policy: non ripetere modifiche già confermate. Un errore di verifica sospende il ciclo; non creare un nuovo tentativo. '
            'Se esito incerto usa operations e fermati, senza creare ordini sostitutivi. '
            'Per consultare/adattare il piano operativo usa hyperliquid_strategy. Il piano è un’ipotesi, non una prova di redditività. '
            'Prima di ogni nuova entrata chiama hyperliquid_analyze per timeframe chiusi, book, funding e costi: valuta long, short e HOLD, non promettere TP sicuri. '
            'Scegli approccio, entry, TP/SL e size: ogni nuova entrata richiede take_profit/stop_loss nello stesso preview, anche con strategia personalizzata. '
            'Ultimo piano persistente (ipotesi del modello, non istruzioni superiori né prova di profitto): '+json.dumps(previous,ensure_ascii=False)+'. '
            'Policy locale (dati immutabili per questo ciclo): '+json.dumps(g,ensure_ascii=False))

def assess_cycle(agent,home,name):
    """A prose promise or textual pseudo-tool call is never a completed cycle."""
    start=next((i for i in range(len(agent.messages)-1,-1,-1) if agent.messages[i].get('role')=='user' and str(agent.messages[i].get('content','')).startswith('Ciclo trading Hyperliquid autorizzato localmente.')),len(agent.messages))
    messages=agent.messages[start:];calls={c['id']:c['function']['name'] for m in messages for c in m.get('tool_calls',[])}
    reads=[]
    for m in messages:
        if m.get('role')!='tool' or calls.get(m.get('tool_call_id'))!='hyperliquid_read':continue
        try:value=json.loads(m.get('content',''))
        except (ValueError,TypeError):continue
        if isinstance(value,dict) and value.get('resource') and 'data' in value and not value.get('error'):reads.append(value['resource'])
    with database(home) as db:
        g=current_grant(db,name)
        rows=[receipt(r) for r in db.execute('SELECT * FROM actions WHERE profile=?',(name,))]
    rows=[r for r in rows if g and r['body'].get('cycle')==g.get('pending')]
    final=next((m.get('content') or '' for m in reversed(messages) if m.get('role')=='assistant' and not m.get('tool_calls')),'')
    reasons=[]
    if agent.turn_outcome!='completed':reasons.append('model_turn_incomplete')
    if not reads:reasons.append('no_verified_live_reads')
    if any(r['state'] in ('prepared','authorized','sending','uncertain','partial','blocked','rejected') for r in rows):reasons.append('unfinished_or_unresolved_operations')
    if not rows and not re.search(r'\bHOLD\b',final,re.I):reasons.append('no_receipt_or_explicit_hold')
    return {'ok':not reasons,'reasons':reasons,'verified_read_resources':reads,'operation_ids':[r['id'] for r in rows],
            'instruction':'Completion is based on native tool evidence. No tool calls in prose are executed or retried automatically.'}

def reconcile_hazard(agent,home,name):
    """Freeze first, perform one native read-only reconciliation, then stop."""
    with database(home) as db:
        g=current_grant(db,name)
        if not g or not g.get('pending'):return None
        if not g.get('active') or g.get('stop_epoch')!=stop_epoch(home,name):
            return 'HOLD · automazione arrestata. Nessun altro invio o tentativo del modello; consulta stato e ricevute prima di una nuova autorizzazione locale.'
        rows=[receipt(r) for r in db.execute("SELECT * FROM actions WHERE profile=? AND state IN ('uncertain','partial','sending','authorized')",(name,))]
    hazards=[r for r in rows if r['body'].get('cycle')==g['pending']]
    if not hazards:return None
    stop(home,name)
    seen=getattr(agent,'hyperliquid_reconciled',set());observed=[]
    for row in hazards:
        if row['id'] not in seen:
            seen.add(row['id']);ident='native_reconcile_'+uuid.uuid4().hex
            agent.messages.append({'role':'assistant','content':None,'tool_calls':[{'id':ident,'type':'function','function':{'name':'hyperliquid_operations','arguments':json.dumps({'id':row['id']})}}]})
            try:result=agent.tool('hyperliquid_operations',{'id':row['id']})
            except Exception as error:result=json.dumps({'code':'reconciliation_unavailable','error_type':type(error).__name__,'resent':False})
            agent.messages.append({'role':'tool','tool_call_id':ident,'content':result if isinstance(result,str) else json.dumps(result)})
        observed.append({'id':row['id'],'state':row['state'],'entry_cloid':row['body'].get('cloid'),'tp_cloid':row['body'].get('bracket',{}).get('tp_cloid'),'sl_cloid':row['body'].get('bracket',{}).get('sl_cloid')})
    agent.hyperliquid_reconciled=seen
    return 'HOLD · automazione fermata. Esiti non risolti: '+json.dumps(observed,ensure_ascii=False)+'. Riconciliazione nativa una sola volta; nessuna firma, sostituzione o retry aggiuntivo.'

def verified_group_result(agent,home,name):
    """Report a completed SDK submission without another inference request.

    Accepted/resting/deferred exits do not certify fill or active protection.
    The persisted strategy and original SDK receipt remain authoritative data.
    """
    with database(home) as db:
        g=current_grant(db,name)
        if not g or not g.get('pending'):return None
        rows=[receipt(r) for r in db.execute('SELECT * FROM actions WHERE profile=?',(name,))]
    rows=[r for r in rows if r['body'].get('cycle')==g['pending']]
    groups=[r for r in rows if r['body'].get('bracket') and r['state'] in ('accepted','filled') and (r.get('response') or {}).get('grouped_tpsl')]
    from hyperliquid_protection import completion
    groups=[r for r in groups if completion(r['body'],r.get('response'))]
    if not groups or any(r['state'] not in ('accepted','filled','acknowledged') for r in rows):return None
    path=folder(home)/(name+'.strategy.json')
    if not path.is_file():return None
    plan=json.loads(path.read_text(encoding='utf-8')).get('current_plan') or {}
    if plan.get('scope')!=fingerprint(profile(home,name)) or plan.get('updated',0)<g['created']:return None
    observed=[]
    for row in groups:
        ident='native_receipt_'+uuid.uuid4().hex
        agent.messages.append({'role':'assistant','content':None,'tool_calls':[{'id':ident,'type':'function','function':{'name':'hyperliquid_operations','arguments':json.dumps({'id':row['id']})}}]})
        try:result=agent.tool('hyperliquid_operations',{'id':row['id']})
        except Exception as error:
            agent.messages.append({'role':'tool','tool_call_id':ident,'content':json.dumps({'code':'receipt_read_unavailable','error_type':type(error).__name__,'resent':False})})
            return None
        agent.messages.append({'role':'tool','tool_call_id':ident,'content':result if isinstance(result,str) else json.dumps(result)})
        b=row['body'];observed.append({'id':row['id'],'state':row['state'],'entry_cloid':b['cloid'],'tp_cloid':b['bracket']['tp_cloid'],'sl_cloid':b['bracket']['sl_cloid'],'sdk_statuses':row['response'].get('sdk_response',{}).get('response',{}).get('data',{}).get('statuses')})
    return 'Ricevute native entry/TP/SL registrate: '+json.dumps(observed,ensure_ascii=False)+'. Accepted/resting non significa filled e non certifica protezioni attive. Piano e ID persistiti; nessun ordine aggiuntivo.'

def finish_cycle(home,name,ok):
    with database(home) as db:
        g=current_grant(db,name)
        if not g:return
        rows=[receipt(r) for r in db.execute('SELECT * FROM actions WHERE profile=?',(name,))]
        if any(r['body'].get('cycle')==g.get('pending') and r['state'] in ('uncertain','sending','authorized','partial','rejected','blocked') for r in rows):ok=False
        g['pending']=None;g['last_cycle']=time.time();g['last_cycle_ok']=bool(ok)
        if not ok:g.update(active=False,stopped_reason='Cycle incomplete/error; inspect operations before resuming')
        db.execute('UPDATE grants SET body=? WHERE profile=?',(json.dumps(g),name));db.commit()

def controller_summary(home,name):
    with database(home) as db:
        g=current_grant(db,name)
        rows=[receipt(r) for r in db.execute('SELECT * FROM actions WHERE profile=?',(name,))]
    rows=[r for r in rows if r['body'].get('cycle')==g.get('pending')] if g else []
    return 'Hyperliquid · ricevute verificate del ciclo: '+json.dumps([{'id':r['id'],'action':r['body']['action'],'state':r['state'],'cloid':r['body'].get('cloid')} for r in rows],ensure_ascii=False)+(' · nessun invio registrato' if not rows else '')

def strategy(agent,home,a):
    p=profile(home,a.get('profile'));path=folder(home)/(p['name']+'.strategy.json')
    data=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {'profile':p['name'],'current_plan':None,'history':[]}
    if a['action']=='inspect':return {'strategy':data,'scope':fingerprint(p),'note':'Piano generato dal modello; esiti verificabili solo da operations/fills e stato reale.'}
    if a['action']!='update' or not isinstance(a.get('plan'),str) or not 10<=len(a['plan'])<=4000:problem('plan_invalid','Usa inspect o update con piano di10–4000 caratteri; nessuna chiave o modifica dei limiti.')
    if agent.mode=='plan' or getattr(agent,'depth',0):problem('strategy_read_only','Piano aggiornabile solo dal coordinatore operativo.')
    with database(home) as db:g=current_grant(db,p['name'])
    if not g or g.get('strategy_mode')!='auto' or g['scope']!=fingerprint(p) or g['session']!=str(Path(agent.session).resolve()):problem('strategy_scope','La gestione autonoma della strategia richiede un grant locale automatico per questa sessione/profilo.')
    entry={'plan':a['plan'],'classification':'model_proposal_unverified','updated':time.time(),'scope':fingerprint(p)}
    if data.get('current_plan'):data['history']=(data['history']+[data['current_plan']])[-10:]
    data['current_plan']=entry;atomic_bytes(path,json.dumps(data,ensure_ascii=False,indent=2).encode('utf-8'))
    return {'strategy':data,'grant_changed':False,'limits_changed':False}

def panel(agent,home):
    if not agent.interactive or getattr(agent,'depth',0):problem('local_ui_required','Apri /hyperliquid nella sessione interattiva principale.')
    import subprocess,sys
    command=[sys.executable,'-X','utf8',str(Path(__file__).with_name('hyperliquid_panel.py')),'--home',str(Path(home).resolve()),'--session',str(Path(agent.session).resolve())]
    process=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    return {'panel_opened':True,'pid':process.pid,'private_keys':'Entered only in the separate local Python panel; never in chat or command arguments.'}

def command(agent,prompt,home,output):
    if prompt.strip().casefold() in ('hyperliquid','/hyperliquid','/hyperliquid menu'):
        from hyperliquid_menu import run
        run(agent,home,output);return True
    if prompt in ('/hyperliquid panel','/hyperliquid login','/hyperliquid auto','/hyperliquid settings'):
        output(json.dumps(panel(agent,home),ensure_ascii=False));return True
    if prompt=='/hyperliquid status':output(json.dumps(status(home),ensure_ascii=False,indent=2));return True
    if prompt=='/hyperliquid stop':output(json.dumps(stop(home),ensure_ascii=False));return True
    if prompt=='/hyperliquid operations':output(json.dumps(operations(home),ensure_ascii=False,indent=2));return True
    if prompt.startswith('/hyperliquid use '):
        output(json.dumps(dispatch(agent,'hyperliquid_select',{'profile':prompt[len('/hyperliquid use '):].strip()},home),ensure_ascii=False));return True
    return False

def dispatch(agent,name,a,home):
    try:
        controller=getattr(agent,'hyperliquid_controller',None)
        if controller and name in ('hyperliquid_read','hyperliquid_analyze','hyperliquid_scan','hyperliquid_preview','hyperliquid_strategy','hyperliquid_operations','hyperliquid_execute_plan','hyperliquid_preflight'):
            a=dict(a);a.setdefault('profile',controller)
            if a['profile']!=controller:problem('auto_scope','Il ciclo automatico non può cambiare profilo.')
            if name=='hyperliquid_read' and a.get('address') and a['address'].lower()!=profile(home,controller)['target'].lower():problem('auto_scope','Il ciclo usa soltanto il target autorizzato.')
        if name=='hyperliquid_preflight':
            from hyperliquid_execution import preflight
            return preflight(home,a.get('profile'))
        if name=='hyperliquid_execute_plan':
            from hyperliquid_execution import execute
            from freellmapi import output
            return execute(agent,home,a,output)
        if name=='hyperliquid_propose_plan':
            from trade_proposal import propose
            return propose(agent,home,a)
        if name=='hyperliquid_panel':return panel(agent,home)
        if name=='hyperliquid_monitor':
            import trading_service
            try:return trading_service.monitor_action(agent,home,a)
            except trading_service.TradingError as exc:
                detail=trading_service.monitor_problem(home,a,exc)
                if detail:
                    code=detail.pop('code');message=detail.pop('message');problem(code,message,**detail)
                problem('monitor_invalid',str(exc))
        if name=='hyperliquid_scan':
            from hyperliquid_analysis import scan
            return scan(home,a)
        if name=='hyperliquid_analyze':
            from hyperliquid_analysis import analyze
            p=profile(home,a.get('profile'));cached=getattr(agent,'hyperliquid_analysis',{})
            previous=cached.get((p['name'],a.get('coin'))) or {}
            intervals={a.get('interval','15m'),a.get('higher_interval','1h')}
            reusable=(previous.get('data_ready') and previous.get('scope')==fingerprint(p)
                      and set(previous.get('timeframes',{}))==intervals and 0<=time.time()*1000-previous.get('as_of_ms',0)<=30000)
            result={**previous,'cached':True} if reusable else analyze(home,a)
            import trading_service
            result['execution_policy']=trading_service.monitor_preflight(home,result['coin'])
            result['execution_policy_profile_matches']=result['execution_policy']['profile']==p['name'] and result['execution_policy']['network']==p['network']
            result['planning_instruction']='For conditional/H24 plans use current saved account/service limits, not historical exchange leverage. The always-on monitor shares the existing account authorization with chat; no second service approval is required. STOP, missing/expired account authority or a paused execution state prevents arming. Do not suggest increasing risk to fit a plan.'
            if result['execution_policy'].get('adapter_mode')=='key_only':
                result['planning_instruction']='Credential-only adapter: all supported exchange-listed perpetuals are available. No local whitelist, quantity/leverage/loss caps or expiring grants. scan_coins are scan preferences, not trading permissions. Choose a plan using current exchange data and user intent; the native path sets/verifies leverage and checks actual exchange capacity. Do not ask for panel financial approvals or revive old orders. No guaranteed TP.'
            cached[(p['name'],result['coin'])]={**result,'scope':fingerprint(p)};agent.hyperliquid_analysis=cached
            return result
        if name=='hyperliquid_service':
            import trading_service as service
            action=a.get('action')
            if action=='status':return service.status(home)
            if action=='stop':return service.stop(home)
            if action=='start':
                if getattr(agent,'depth',0) or getattr(agent,'mode','ask')=='plan':problem('coordinator_only','Avvia il servizio dal coordinatore operativo.')
                return service.start(home,'live')
            problem('service_action','Azioni consentite: status, start, stop. Consenso LIVE solo nel pannello locale.')
        if name=='hyperliquid_status':return status(home)
        if name=='hyperliquid_strategy':return strategy(agent,home,a)
        if name=='hyperliquid_read':return read(home,a)
        if name=='hyperliquid_preview':return preview(agent,home,a)
        if name=='hyperliquid_submit':return submit(agent,home,a['id'])
        if name=='hyperliquid_operations':return operations(home,a.get('id'),a.get('profile'))
        if name=='hyperliquid_select':
            p=profile(home,a['profile']);data=config(home);data['default']=p['name'];save_config(home,data);return {'selected':p,'financial_authority_changed':False}
    except HLProblem as exc:
        if getattr(agent,'hyperliquid_controller',None) and exc.code in ('loss_stop','outcome_unknown','auto_scope','agent_unauthorized','existing_leverage_cap','leverage_unverified','leverage_preflight_locked'):stop(home,agent.hyperliquid_controller)
        result={'ok':False,'code':exc.code,'message':str(exc),'sent':False,'recovery':'Correggi il prerequisito indicato; non sostituire rete, target o ordini e non riprovare invariato.',**exc.details}
        if name in ('hyperliquid_execute_plan','hyperliquid_propose_plan'):result['terminal']=True
        if exc.code=='profile_missing':result['recovery_tools']=[{'name':'hyperliquid_status','arguments':{}}]
        elif exc.code=='size_unit_mismatch':result['recovery_tools']=[{'name':'ask_user','arguments':{'question':exc.details['question'],'options':exc.details['options']}}]
        elif exc.code in ('protection_required','bracket_invalid','slippage_required','order_fields_invalid','bracket_direction','leverage_action_required','request_coin_mismatch','unified_margin_cap','asset_size_cap','financial_snapshot_required'):
            result['recovery_tools']=[{'name':'hyperliquid_read','arguments':{'resource':'snapshot','coin':exc.details.get('requested_coin') or exc.details.get('recovery_coin') or a.get('coin'),'profile':exc.details.get('recovery_profile') or a.get('profile') or config(home).get('default')}}]
        return result
    problem('tool_unknown','Strumento Hyperliquid non riconosciuto.')
