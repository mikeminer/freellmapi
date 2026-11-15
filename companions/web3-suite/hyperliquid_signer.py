"""Local signing worker. Input: public operation ID. Keys never cross its stdout/IPC."""
import argparse,json,logging,os,sys,time,hashlib
from contextlib import contextmanager
from pathlib import Path
import hyperliquid_tools as hl

def connect(home,name,key,kind):
    """Only called by the local GUI, never a model tool."""
    from eth_account import Account
    from hl_credentials import put
    p=hl.profile(home,name)
    if not isinstance(key,str) or not hl.re.fullmatch(r'(?:0x)?[0-9a-fA-F]{64}',key):hl.problem('key_invalid','Firma: chiave EVM di 32 byte richiesta. Non usare seed e non inserirla in chat.')
    try:wallet=Account.from_key(key)
    except Exception:hl.problem('key_invalid','Chiave non valida; nessun dato segreto registrato nei log.')
    owner=wallet.address.lower()==p['account'].lower()
    if kind not in ('master','agent') or owner!=(kind=='master'):hl.problem('signer_kind','La chiave non corrisponde al tipo master/API agent selezionato.')
    hl.permissions(p)
    if not owner:
        role=hl.info(p,{'type':'userRole','user':wallet.address})
        if role.get('role')!='agent' or str(role.get('data',{}).get('user','')).lower()!=p['account'].lower():hl.problem('agent_unauthorized','API agent non autorizzato per questo account/rete. Autorizzalo prima nel frontend ufficiale.')
    put(hl.secret_path(home,name),key)
    data=hl.config(home);data['profiles'][name].update(signer=wallet.address,signer_kind=kind);hl.save_config(home,data)
    hl.stop(home,name)
    from hyperliquid_authority import enable
    mode=enable(home,name,loss_limit=None)
    return {'connected':True,'signer':wallet.address,'kind':kind,'network':p['network'],'secret_returned':False,**mode}

@contextmanager
def account_lock(home,p):
    # All profiles/signers for one master+network serialize in this application.
    name=hashlib.sha256((p['network']+p['account'].lower()).encode()).hexdigest()[:24]
    with (hl.folder(home)/('lock-'+name)).open('a+b') as f:
        if not f.tell():f.write(b'0');f.flush()
        f.seek(0)
        if os.name=='nt':
            import msvcrt
            try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:hl.problem('signer_busy','Altra operazione in corso per questo master/rete. Consulta operations; nessun invio concorrente.')
            try:yield
            finally:f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            try:yield
            finally:fcntl.flock(f,fcntl.LOCK_UN)

def response_state(raw,action,grouped=False,body=None):
    if not isinstance(raw,dict) or raw.get('status')!='ok':return 'rejected'
    statuses=raw.get('response',{}).get('data',{}).get('statuses',[])
    has_errors=any(isinstance(x,dict) and 'error' in x for x in statuses)
    has_success=any(isinstance(x,dict) and ('filled' in x or 'resting' in x) for x in statuses)
    if has_errors:return 'partial' if has_success else 'rejected'
    if grouped:
        valid=lambda x:(isinstance(x,dict) and ('filled' in x or 'resting' in x)) or x in ('waitingForFill','waitingForTrigger')
        if len(statuses)!=3 or not all(valid(x) for x in statuses):return 'partial' if has_success else 'uncertain'
        if body:
            from hyperliquid_protection import sdk_evidence
            if not sdk_evidence(body,{'sdk_response':raw,'grouped_tpsl':True})['accepted']:return 'partial' if has_success else 'uncertain'
    if action=='order':
        if any(isinstance(x,dict) and 'filled' in x for x in statuses):return 'filled'
        if any(isinstance(x,dict) and 'resting' in x for x in statuses):return 'accepted'
        return 'uncertain'
    return 'acknowledged'

def order_requests(body):
    from hyperliquid.utils.types import Cloid
    b=body
    parent={'coin':b['coin'],'is_buy':b['side']=='buy','sz':float(b['size']),'limit_px':float(b['price']),
            'order_type':{'limit':{'tif':b['tif']}},'reduce_only':b['reduce_only'],'cloid':Cloid.from_str(b['cloid'])}
    result=[parent]
    bracket=b.get('bracket')
    if bracket:
        for leg,price,limit,key in [('tp','take_profit','tp_limit','tp_cloid'),('sl','stop_loss','sl_limit','sl_cloid')]:
            result.append({'coin':b['coin'],'is_buy':not parent['is_buy'],'sz':parent['sz'],'limit_px':float(bracket[limit]),
                'order_type':{'trigger':{'isMarket':True,'triggerPx':float(bracket[price]),'tpsl':leg}},'reduce_only':True,'cloid':Cloid.from_str(bracket[key])})
    return result

def execute(home,ident):
    if os.getenv('FREELLMAPI_READ_ONLY')=='1':return {'ok':False,'code':'read_only_signing_forbidden','sent':False}
    with hl.database(home) as db:
        row=hl.receipt(db.execute('SELECT * FROM actions WHERE id=?',(ident,)).fetchone())
    if not row:hl.problem('operation_missing','ID operazione inesistente.')
    if row['state']!='authorized':return {'receipt':row,'sent':False,'replayed':False}
    p=hl.profile(home,row['profile'])
    with account_lock(home,p),hl.database(home) as db:
        db.execute('BEGIN IMMEDIATE');row=hl.receipt(db.execute('SELECT * FROM actions WHERE id=?',(ident,)).fetchone())
        if row['state']!='authorized':return {'receipt':row,'sent':False,'replayed':False}
        b=row['body'];auth=(row.get('response') or {}).get('authorization',{});reserved=b.get('notional_usd','0')
        attempted=False
        try:
            from hyperliquid_protection import require_bracket
            require_bracket(b,client_ids=True) # Before any key access, including manual authorizations.
            other=[hl.receipt(r) for r in db.execute("SELECT * FROM actions WHERE id<>? AND state IN ('sending','uncertain','partial')",(ident,))]
            if any(r['body']['network']==p['network'] and r['body']['account'].lower()==p['account'].lower() for r in other):hl.problem('outcome_unknown','Un’altra operazione sullo stesso master/rete ha esito non accertato.')
            if auth.get('scope')!=hl.fingerprint(p) or b['scope']!=hl.fingerprint(p) or auth.get('expires',0)<time.time():hl.problem('authorization_expired','Autorizzazione scaduta o scope cambiato: nessuna firma.')
            if auth.get('review_hash')!=hashlib.sha256(json.dumps(b,sort_keys=True).encode()).hexdigest():hl.problem('intent_changed','Operazione cambiata dopo autorizzazione: nessuna firma.')
            from hyperliquid_authority import configured
            if configured(p) and b['action'] in ('order','cancel','leverage') and auth.get('mode')!='key_only':
                hl.problem('new_request_required','Adapter aggiornato: le vecchie autorizzazioni non riattivano ordini o strategie.')
            if auth.get('mode')=='auto':
                g=hl.current_grant(db,p['name'])
                if not g or g['generation']!=auth.get('generation'):hl.problem('grant_changed','Grant cambiato/revocato: nessuna firma.')
                reserved=hl.guard_grant(p,g,b,auth['session'],db,exclude_id=ident) or reserved
            elif auth.get('mode')=='key_only':
                from hyperliquid_authority import verify_lease
                verify_lease(home,p,b,auth)
            elif auth.get('mode')=='manual':hl.manual_capacity(p,b,home)
            else:hl.problem('authorization_missing','Autorizzazione locale mancante.')
            if b.get('expected_leverage') is not None:
                actual=hl.checked_leverage(hl.info(p,{'type':'activeAssetData','user':b['target'],'coin':b['coin']}),True)
                if actual['value']!=b['expected_leverage']:hl.problem('leverage_changed','Leva cambiata rispetto al piano approvato: nessun ingresso o aggiornamento implicito.')
            hl.permissions(p,True,home)
            if b['action'].startswith('vault_') and p.get('signer_kind')!='master':hl.problem('owner_signature_required','Deposito/prelievo vault richiede la firma master, non API agent. Nessun ripiego o nuova chiave.')
        except hl.HLProblem as exc:
            db.execute("UPDATE actions SET state='blocked',response=?,updated=? WHERE id=?",(json.dumps({'code':exc.code,'message':str(exc)}),time.time(),ident));db.commit()
            if auth.get('mode')=='auto' and exc.code in ('existing_leverage_cap','leverage_unverified'):
                hl.stop(home,p['name'])
            return {'ok':False,'code':exc.code,'message':str(exc),'sent':False}
        # Durable sending state BEFORE decrypting or signing. A crash never replays it.
        db.execute("UPDATE actions SET state='sending',updated=? WHERE id=?",(time.time(),ident));db.commit()
        try:
            from hl_credentials import get
            from eth_account import Account
            from hyperliquid.exchange import Exchange
            from hyperliquid.utils.types import Cloid
            secret=get(hl.secret_path(home,p['name']))
            try:wallet=Account.from_key(secret)
            finally:secret=None
            if wallet.address.lower()!=p['signer'].lower():hl.problem('signer_changed','Chiave locale diversa dal signer configurato.')
            vault=p['target'] if p['target'].lower()!=p['account'].lower() and not b['action'].startswith('vault_') else None
            dexes=['']+([b['dex']] if b.get('dex') else [])
            exchange=Exchange(wallet,hl.URLS[p['network']],account_address=p['account'],vault_address=vault,perp_dexs=dexes,timeout=10)
            exchange.expires_after=int(time.time()*1000)+15000
            active_asset=None
            if auth.get('mode')=='key_only':
                from hyperliquid_authority import verify_lease
                verify_lease(home,hl.profile(home,p['name']),b,auth)
            if auth.get('mode')=='auto':
                latest=hl.current_grant(db,p['name'])
                if not latest or not latest.get('active') or latest['generation']!=auth['generation'] or latest['expires']<=time.time() or latest.get('stop_epoch')!=hl.stop_epoch(home,p['name']):hl.problem('auto_stopped','Autorizzazione revocata prima dell’invio.')
                if b['action']=='order' and not b['reduce_only']:
                    # SDK initialization may perform network I/O. Recheck immediately
                    # before signing/posting, never use only the earlier preview.
                    active_asset=hl.leverage_within_limit(p,b['coin'],latest['max_leverage'])
                    if latest.get('stop_epoch')!=hl.stop_epoch(home,p['name']):hl.problem('auto_stopped','Arresto richiesto durante la verifica leva.')
            if b.get('expected_leverage') is not None:
                actual=hl.checked_leverage(active_asset or hl.info(p,{'type':'activeAssetData','user':b['target'],'coin':b['coin']}),True)
                if actual['value']!=b['expected_leverage']:hl.problem('leverage_changed','Leva cambiata durante l’inizializzazione SDK: nessun invio.')
            require_bracket(b,client_ids=True)
            from hyperliquid_protection import require_live_trigger
            require_live_trigger(p,b)
            attempted=True
            if b['action']=='order':
                if b.get('bracket'):raw=exchange.bulk_orders(order_requests(b),grouping='normalTpsl')
                else:raw=exchange.order(b['coin'],b['side']=='buy',float(b['size']),float(b['price']),{'limit':{'tif':b['tif']}},reduce_only=b['reduce_only'],cloid=Cloid.from_str(b['cloid']))
            elif b['action']=='cancel':raw=exchange.cancel(b['coin'],b['oid'])
            elif b['action']=='leverage':raw=exchange.update_leverage(b['leverage'],b['coin'],b['cross'])
            else:raw=exchange.vault_usd_transfer(b['vault'],b['action']=='vault_deposit',b['usd_units'])
            state=response_state(raw,b['action'],bool(b.get('bracket')),b);observed={'sdk_response':raw,'sent_once':True,'cloid':b.get('cloid'),'network':p['network'],'target':p['target'],'notional_reserved':reserved,'grouped_tpsl':bool(b.get('bracket'))}
        except Exception as exc:
            # No exception text/traceback or signed payload leaves this process.
            state='uncertain' if attempted else 'blocked';observed={'error_class':type(exc).__name__,'code':getattr(exc,'code','local_signer_error'),'reason':'Esito non accertato: riconcilia senza retry.' if attempted else 'Nessun invio iniziato: controlla prerequisiti/firma/grant.','send_attempted':attempted,'notional_reserved':reserved}
        db.execute('UPDATE actions SET state=?,response=?,updated=? WHERE id=?',(state,json.dumps(observed),time.time(),ident));db.commit()
        if auth.get('mode')=='auto' and observed.get('code') in ('existing_leverage_cap','leverage_unverified'):
            hl.stop(home,p['name'])
        return {'receipt':hl.receipt(db.execute('SELECT * FROM actions WHERE id=?',(ident,)).fetchone()),'private_key_exposed':False}

if __name__=='__main__':
    logging.disable(logging.CRITICAL)
    import truststore
    truststore.inject_into_ssl() # standalone signer application, never injected into parent/model runtime
    parser=argparse.ArgumentParser();parser.add_argument('--home',required=True);args=parser.parse_args()
    try:
        from app_home import runtime_home
        request=json.loads(sys.stdin.read(4096));result=execute(runtime_home(args.home),request['id'])
    except Exception as exc:result={'ok':False,'code':getattr(exc,'code','signer_error'),'error_class':type(exc).__name__,'sent':False,'instruction':'Consulta operations prima di qualsiasi nuovo invio.'}
    print(json.dumps(result,ensure_ascii=False))
