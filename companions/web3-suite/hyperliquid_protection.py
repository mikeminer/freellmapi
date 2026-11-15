"""Mandatory new-risk brackets and bounded, read-only exchange verification.

No credentials, signing, replacement orders or retries live in this module.
"""
import json,time,re
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal,InvalidOperation


def new_risk(body):
    return body.get('action')=='order' and body.get('reduce_only') is not True


def require_bracket(body,client_ids=False):
    import hyperliquid_tools as h
    if not new_risk(body):return
    bracket=body.get('bracket')
    if not isinstance(bracket,dict) or not bracket.get('take_profit') or not bracket.get('stop_loss'):
        h.problem('protection_required','Ogni nuova entrata richiede take_profit e stop_loss nello stesso preview, anche in modalità manuale. Scegli i livelli dai dati correnti; non inviare prima l’entry. Nessun ordine inviato.',
                  recovery_coin=body.get('coin'),recovery_profile=body.get('profile'),required_fields=['take_profit','stop_loss','bracket_slippage_bps'])
    tp=h.number(bracket['take_profit'],'take_profit');sl=h.number(bracket['stop_loss'],'stop_loss')
    price=h.number(body.get('price'),'entry price')
    bound=body.get('trigger_bound')
    if bound:
        limit=h.number(bound.get('limit'),'trigger limit')
        if (price>limit if body.get('side')=='buy' else price<limit):h.problem('trigger_gap','Prezzo firmato oltre il limite del trigger: nessun invio.')
    mid=h.number(body.get('mid',body.get('price')),'mid') if body.get('order_kind')=='market' else price
    low,high=min(price,mid),max(price,mid)
    if body.get('side') not in ('buy','sell') or not (sl<low and high<tp if body['side']=='buy' else tp<low and high<sl):
        h.problem('bracket_direction','Long: SL < ingresso < TP; short: TP < ingresso < SL. Per IOC i livelli devono coprire anche il peggior prezzo consentito. Nessun invio.')
    bps=bracket.get('slippage_bps')
    if type(bps) is not int or not 1<=bps<=1000 or bracket.get('grouping')!='normalTpsl':
        h.problem('bracket_invalid','Gruppo entry/TP/SL incompleto: prepara un nuovo preview con bracket_slippage_bps 1–1000.')
    for label,trigger in (('tp_limit',tp),('sl_limit',sl)):
        limit=h.number(bracket.get(label),label)
        if (body['side']=='buy' and limit>trigger) or (body['side']=='sell' and limit<trigger):
            h.problem('bracket_invalid','Limite di esecuzione TP/SL non coerente con il lato di chiusura.')
    if client_ids:
        ids=[body.get('cloid'),bracket.get('tp_cloid'),bracket.get('sl_cloid')]
        if not all(isinstance(i,str) and re.fullmatch(r'0x[0-9a-fA-F]{32}',i) for i in ids) or len(set(i.lower() for i in ids))!=3:
            h.problem('bracket_invalid','Tre client ID nativi distinti richiesti: prepara un nuovo preview, senza inventare ID.')


def sdk_evidence(body,response):
    """An HTTP 200 or entry acceptance alone is not acceptance of the exits."""
    response=response if isinstance(response,dict) else {}
    raw=response.get('sdk_response') or {}
    raw=raw if isinstance(raw,dict) else {}
    envelope=raw.get('response');envelope=envelope if isinstance(envelope,dict) else {}
    data=envelope.get('data');data=data if isinstance(data,dict) else {}
    statuses=data.get('statuses');statuses=statuses if isinstance(statuses,list) else []
    def accepted(value,entry=False):
        if isinstance(value,dict):
            if 'error' in value:return False
            leg=value.get('filled') or value.get('resting')
            return isinstance(leg,dict) and type(leg.get('oid')) is int and leg['oid']>0
        return not entry and value in ('waitingForFill','waitingForTrigger')
    complete=bool(new_risk(body) and body.get('bracket') and response and response.get('grouped_tpsl')
                  and raw.get('status')=='ok' and len(statuses)==3
                  and accepted(statuses[0],True) and all(accepted(s) for s in statuses[1:]))
    partial_fill=False
    if complete and isinstance(statuses[0],dict) and 'filled' in statuses[0]:
        try:
            size=Decimal(str(statuses[0]['filled']['totalSz']));expected=Decimal(body['size'])
            partial_fill=not size.is_finite() or size!=expected
        except (KeyError,InvalidOperation,TypeError):partial_fill=True
        complete=not partial_fill
    return {'accepted':complete,'partial_fill':partial_fill,'sdk_statuses':statuses}


def verify_live(body,profile,reader,sdk_response=None):
    """One concurrent read per known cloid. No polling or /exchange capability."""
    bracket=body['bracket'];ids={'entry':body['cloid'],'tp':bracket['tp_cloid'],'sl':bracket['sl_cloid']}
    observations={};errors={};retry_names=[];read_retries={}
    statuses=(sdk_response or {}).get('sdk_response',{}).get('response',{}).get('data',{}).get('statuses',[])
    numeric={}
    for name,status in zip(ids,statuses):
        if isinstance(status,dict):
            oid=(status.get('filled') or status.get('resting') or {}).get('oid')
            if type(oid) is int and oid>0:numeric[name]=oid
    def read(name,cloid):
        result=reader(profile,{'type':'orderStatus','user':body['target'],'oid':cloid})
        if isinstance(result,dict) and result.get('status')=='unknownOid' and name in numeric:
            result=reader(profile,{'type':'orderStatus','user':body['target'],'oid':numeric[name]})
        return result
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures={name:pool.submit(read,name,cloid) for name,cloid in ids.items()}
        for name,future in futures.items():
            try:observations[name]=future.result()
            except Exception as exc:
                errors[name]=type(exc).__name__
                if isinstance(exc,(TimeoutError,ConnectionError)) or getattr(exc,'details',{}).get('retryable') is True:retry_names.append(name)
    # One additional READ of each transiently failed leg, same original IDs.
    # Unknown IDs and mismatching orders are never hidden by retries.
    if retry_names:
        time.sleep(.25)
        with ThreadPoolExecutor(max_workers=3) as pool:
            retries={name:pool.submit(read,name,ids[name]) for name in retry_names}
            for name,future in retries.items():
                read_retries[name]=1
                try:observations[name]=future.result();errors.pop(name,None)
                except Exception as exc:errors[name]=type(exc).__name__
    def order(name):
        outer=observations.get(name,{})
        return outer.get('order',{}) if isinstance(outer,dict) and outer.get('status')=='order' else {}
    states={name:order(name).get('status','unknown') for name in ids}
    def matches(name):
        actual=order(name).get('order') or {}
        try:
            expected_size=Decimal(body['size']);size=Decimal(str(actual.get('origSz',actual.get('sz'))))
            return (actual.get('coin')==body['coin'] and actual.get('reduceOnly') is True
                    and actual.get('side')==('A' if body['side']=='buy' else 'B')
                    and size==expected_size and Decimal(str(actual.get('triggerPx')))==Decimal(bracket['take_profit' if name=='tp' else 'stop_loss']))
        except (InvalidOperation,TypeError):return False
    state='unverified'
    if states['entry']=='filled' and all(states[n]=='open' and matches(n) for n in ('tp','sl')):state='active'
    elif states['entry']=='open':
        actual=order('entry').get('order') or {}
        try:
            # A partially filled parent does not guarantee active child orders.
            remaining=Decimal(str(actual['sz']));original=Decimal(str(actual['origSz']))
            if remaining==original==Decimal(body['size']) and all(states[n]=='unknown' or (states[n]=='open' and matches(n)) for n in ('tp','sl')):state='pending_entry'
            elif remaining<original:state='partial_entry'
        except (KeyError,InvalidOperation,TypeError):pass
    if states['entry']=='filled' and any(states[n]=='filled' for n in ('tp','sl')):state='exit_filled'
    return {'state':state,'checked_ms':int(time.time()*1000),'leg_states':states,'observations':observations,'read_errors':errors,'read_retries':read_retries,'resent':False}


def finalize(home,result):
    """Persist proof once after a fresh worker result; never resend on failure."""
    import hyperliquid_tools as h
    embedded=result.get('receipt') if isinstance(result,dict) else None
    if not isinstance(embedded,dict) or not embedded.get('id') or result.get('reused'):return result
    with h.database(home) as db:
        row=h.receipt(db.execute('SELECT * FROM actions WHERE id=?',(embedded['id'],)).fetchone())
    if not row or not new_risk(row['body']) or row['state'] not in ('accepted','filled','partial','uncertain'):return result
    response=dict(row.get('response') or {})
    if response.get('protection'):return {**result,'receipt':row}
    proof=sdk_evidence(row['body'],response)
    if proof['accepted']:
        try:
            p=h.profile(home,row['profile'])
            if row['body']['scope']!=h.fingerprint(p):raise ValueError('Scope changed')
            proof.update(verify_live(row['body'],p,h.info,response))
        except Exception as exc:proof.update(state='unverified',read_errors={'verification':type(exc).__name__})
    else:proof.update(state='partial_entry' if proof['partial_fill'] else 'group_incomplete',resent=False)
    safe=proof.get('state') in ('active','pending_entry','exit_filled') and proof['accepted']
    response['protection']=proof
    state=row['state'] if safe or row['state'] in ('partial','uncertain') else 'partial'
    with h.database(home) as db:
        db.execute('UPDATE actions SET state=?,response=?,updated=? WHERE id=? AND state=?',
                   (state,json.dumps(response),time.time(),row['id'],row['state']));db.commit()
        latest=h.receipt(db.execute('SELECT * FROM actions WHERE id=?',(row['id'],)).fetchone())
    if not safe:
        from hyperliquid_authority import configured
        if not configured(h.profile(home,row['profile'])):h.stop(home,row['profile'])
        # Key-only mode is blocked by the durable partial receipt itself,
        # without forcing another credential connection after reconciliation.
    return {**result,'receipt':latest,'protection':proof,'instruction':'Nessun reinvio dell’entry. TP/SL pending_entry sono accettati ma differiti fino al riempimento completo; partial/unverified richiede riconciliazione in sola lettura.'}


def completion(body,response):
    proof=(response or {}).get('protection') or {}
    return sdk_evidence(body,response)['accepted'] and proof.get('state') in ('active','pending_entry','exit_filled')


def require_live_trigger(profile,body):
    """Last read immediately before signing; the IOC limit also caps fill drift."""
    bound=body.get('trigger_bound')
    if not bound:return
    import hyperliquid_tools as h
    mid=h.number(h.info(profile,{'type':'allMids','dex':body.get('dex','')}).get(body['coin']),'trigger mid')
    level=h.number(bound['reference'],'trigger reference')
    if abs(mid-level)/level*10000>bound['max_gap_bps']:h.problem('trigger_gap','Prezzo troppo distante dal livello previsto: nessuna firma.')
    if bound.get('confirmation')=='retest':
        tolerance=bound.get('retest_tolerance_bps')
        if type(tolerance) is not int or not 1<=tolerance<=1000 or abs(mid-level)/level*10000>tolerance:h.problem('trigger_lost','Retest non più confermato: nessuna firma.')
    elif not (mid>=level if bound.get('direction')=='above' else mid<=level):h.problem('trigger_lost','Livello d’ingresso non più confermato: nessuna firma.')
    if (mid>Decimal(bound['limit']) if body['side']=='buy' else mid<Decimal(bound['limit'])):h.problem('trigger_gap','Prezzo oltre limite del trigger: nessuna firma.')
