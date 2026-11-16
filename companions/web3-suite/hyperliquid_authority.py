"""Credential-only adapter: new requests authorize trading, never old work.

No coin/size/leverage budgets. Exchange permissions, balances, precision and
idempotent receipts remain enforced. Keys are only read by the signer process.
"""
import hashlib,json,time,uuid
from decimal import Decimal
from pathlib import Path

MODE='key_only'

def configured(p):return isinstance(p.get('execution_authority'),dict) and p['execution_authority'].get('mode')==MODE

def current(home,p):
    import hyperliquid_tools as h
    if not configured(p):return None
    value=dict(p['execution_authority'])
    value['active']=(value.get('scope')==h.fingerprint(p) and h.secret_path(home,p['name']).is_file()
                     and value.get('stop_epoch')==h.stop_epoch(home,p['name']))
    return value

def enable(home,name,loss_limit='preserve'):
    """Local connection/migration only. No key reads, orders or strategy start."""
    import hyperliquid_tools as h
    p=h.profile(home,name)
    if not p.get('signer') or not h.secret_path(home,name).is_file():h.problem('signer_missing','Collega prima la chiave Hyperliquid nell’adapter locale.')
    old=p.get('execution_authority') or {}
    if loss_limit=='preserve':loss_limit=old.get('loss_limit_usd')
    if loss_limit is not None:loss_limit=h.textnum(h.number(loss_limit,'stop perdita'))
    authority={'mode':MODE,'scope':h.fingerprint(p),'generation':uuid.uuid4().hex,'enabled_at':time.time(),
               'stop_epoch':h.stop_epoch(home,name),'loss_limit_usd':loss_limit,'baseline_equity':old.get('baseline_equity'),
               'market_scope':'all_listed_perpetuals','leverage_limit':'exchange','quantity_limit':'exchange'}
    data=h.config(home);data['profiles'][name]['execution_authority']=authority;h.save_config(home,data)
    # Persist the mode so already-running supervisors observe a config change.
    import trading_service as service
    config=service.load(home)
    config.update(profile=name,network=p['network'],execution_policy='exchange',market_scope='all_listed_perpetuals',plan_new=False,key_only_generation=authority['generation'])
    service.save(home,config)
    return {'adapter_mode':MODE,'profile':name,'market_scope':'all_listed_perpetuals','local_leverage_cap':None,'local_quantity_cap':None,
            'loss_limit_usd':loss_limit,'orders_sent':0,'old_work_resumed':False,'strategy_started':False}

def require_current(home,p):
    import hyperliquid_tools as h
    auth=current(home,p)
    if not auth or not auth['active']:h.problem('key_authority_inactive','Firma locale scollegata, scope cambiato o arresto richiesto: nessun invio.')
    return auth

def request_context(agent,home,p,body):
    """Native intent proof, separate from possession of a key."""
    import hyperliquid_tools as h
    auth=require_current(home,p)
    if h.config(home).get('default')!=p['name']:h.problem('account_scope_changed','Il profilo non è quello attualmente selezionato: nessun ordine su un altro account.')
    if body['action'] not in ('order','cancel','leverage','watch'):h.problem('owner_review_required','Il collegamento API copre trading, non movimenti di fondi.')
    if body.get('execution_generation')!=auth['generation']:h.problem('new_request_required','Anteprima precedente al nuovo adapter: nessun vecchio ordine viene riattivato. Prepara il piano della nuova richiesta.')
    cycle=getattr(agent,'key_only_cycle',None)
    if cycle:return {'source':'native_watch','id':cycle}
    from work_ledger import load
    from financial_completion import requirement
    request=load(agent.session).get('financial_request') or {}
    if request.get('proposal_pending'):h.problem('proposal_confirmation_required','La proposta non è confermata: nessun invio o modifica della leva.')
    if request.get('created',0)<auth['enabled_at']:h.problem('new_request_required','Serve una nuova richiesta successiva al collegamento della chiave; nessun replay della sessione precedente.')
    context=getattr(agent,'key_only_plan',None)
    if body['action'] in ('order','leverage') and context and context.get('request_id')==request.get('id'):return {'source':'typed_plan','id':request['id']}
    text=next((m['content'] for m in reversed(agent.messages) if m.get('role')=='user' and isinstance(m.get('content'),str)),'')
    required=requirement(text,{'venue':'hyperliquid'})
    confirmation=getattr(agent,'current_market_request',None) or {}
    if (not required and confirmation.get('confirmation_of') and request.get('confirmation_of')==confirmation['confirmation_of']
            and getattr(agent,'financial_request_id',None)==request.get('id')
            and request.get('confirmed_profile')==p['name'] and request.get('confirmed_scope')==h.fingerprint(p)):
        required='order'
    allowed={'order':('order',),'cancel':('cancel',),'leverage':('leverage',),'watch':('watch',)}
    if body['action'] not in allowed.get(required,()):h.problem('execution_not_requested','La richiesta corrente non autorizza questa operazione; analisi e login non avviano ordini.')
    return {'source':'user_request','id':request['id']}

def check_loss(home,p,auth,dex=''):
    """Independent previous user loss stop, if retained; never reset baseline."""
    import hyperliquid_tools as h
    limit=auth.get('loss_limit_usd')
    if limit is None:return None
    risk=h.risk_state(p,dex);equity=Decimal(risk['risk_equity_usd']);baseline=auth.get('baseline_equity')
    if baseline is None:
        from trading_runtime import singleton
        with singleton(h.folder(home)/(p['name']+'.authority.lock')):
            data=h.config(home);stored=data['profiles'][p['name']]['execution_authority']
            if stored['generation']!=auth['generation']:h.problem('key_authority_changed','Collegamento cambiato: nessun invio.')
            if stored.get('baseline_equity') is None:
                stored['baseline_equity']=h.textnum(equity);stored['baseline_observed_ms']=int(time.time()*1000);h.save_config(home,data)
            baseline=stored['baseline_equity']
    if Decimal(baseline)-equity>=Decimal(limit):h.problem('loss_stop','Stop perdita dell’account raggiunto. Nessun nuovo invio; non chiude posizioni.')
    return {'equity':h.textnum(equity),'baseline':str(baseline),'limit':str(limit)}

def capacity(home,p,body):
    """Use actual exchange-side asset capacity; no local notional/leverage cap."""
    import hyperliquid_tools as h
    auth=require_current(home,p)
    if body['action']=='order' and not body.get('reduce_only'):
        active=h.info(p,{'type':'activeAssetData','user':p['target'],'coin':body['coin']})
        h.checked_leverage(active,True)
        caps=active.get('maxTradeSzs');index=0 if body['side']=='buy' else 1
        if not isinstance(caps,list) or len(caps)!=2:h.problem('exchange_capacity_unverified','Capacità effettiva Hyperliquid non disponibile: nessun invio.')
        try:cap=Decimal(str(caps[index]));size=Decimal(body['size'])
        except Exception:h.problem('exchange_capacity_unverified','Capacità effettiva Hyperliquid non valida.')
        if not cap.is_finite() or cap<0:h.problem('exchange_capacity_unverified','Capacità effettiva Hyperliquid non finita/negativa.')
        if size>cap:h.problem('exchange_size_cap','Quantità oltre maxTradeSzs restituito da Hyperliquid, non da una policy locale.',exchange_max_size=str(cap),requested_size=str(size))
    check_loss(home,p,auth,body.get('dex',''))

def verify_lease(home,p,body,lease):
    import hyperliquid_tools as h
    auth=require_current(home,p)
    if h.config(home).get('default')!=p['name']:h.problem('account_scope_changed','Account selezionato cambiato prima della firma: nessun invio.')
    if lease.get('generation')!=auth['generation'] or body.get('execution_generation')!=auth['generation']:
        h.problem('key_authority_changed','Chiave/scope ricollegati dopo la preparazione: nessuna firma o replay.')
    if lease.get('source') not in ('user_request','typed_plan','native_watch') or not lease.get('request_id'):
        h.problem('execution_not_requested','Manca una nuova richiesta o un setup nativo corrente.')
    capacity(home,p,body)
    return auth

def leverage_once(agent,home,p,coin,value,on_prepared=lambda row:None):
    """Set the requested exchange-valid leverage once, then prove actual setting."""
    import hyperliquid_tools as h
    meta,_=h.market(p,coin)
    if type(value) is not int or not 1<=value<=int(meta['maxLeverage']):h.problem('leverage_invalid','Leva oltre quella ammessa da Hyperliquid per '+coin+'.')
    before=h.checked_leverage(h.info(p,{'type':'activeAssetData','user':p['target'],'coin':coin}),True)
    if before['value']==value:return {'verified':True,'observed':before,'changed':False}
    row=h.preview(agent,home,{'profile':p['name'],'action':'leverage','coin':coin,'leverage':value,'cross':before['type']=='cross'})['preview']
    on_prepared(row)
    result=h.submit(agent,home,row['id']);native=result.get('receipt') or {}
    if native.get('state')!='acknowledged':h.problem('leverage_update_unconfirmed','Invio leva non confermato: consulta la ricevuta esistente, nessun reinvio.',operation_id=row['id'])
    actual=h.checked_leverage(h.info(p,{'type':'activeAssetData','user':p['target'],'coin':coin}),True)
    if actual!={'value':value,'type':before['type']}:h.problem('leverage_not_applied','Hyperliquid non conferma la leva richiesta: nessuna entry o nuovo aggiornamento.',operation_id=row['id'])
    return {'verified':True,'observed':actual,'changed':True,'operation_id':row['id']}
