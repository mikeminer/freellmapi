"""Bounded /info-only recovery of grouped receipts. Never signs or clears STOP.

Flat balances and unknownOid alone are not closure evidence. Require the exact
SDK entry oid and a contiguous fill chain from flat, through that entry, to flat,
followed by fresh position AND open-order reads. Ambiguous history stays blocked.
"""
import json,time
from decimal import Decimal,InvalidOperation
from hyperliquid_protection import sdk_evidence,verify_live


def closed_evidence(row,p,reader):
    b=row['body'];response=row.get('response') or {}
    if not sdk_evidence(b,response)['accepted']:return None
    statuses=response.get('sdk_response',{}).get('response',{}).get('data',{}).get('statuses',[])
    entry=(statuses[0].get('filled') if statuses and isinstance(statuses[0],dict) else None) or {}
    oid=entry.get('oid')
    if type(oid) is not int or oid<=0:return None
    now=int(time.time()*1000);start=int(row['created']*1000)-5000
    fills=reader(p,{'type':'userFillsByTime','user':b['target'],'startTime':start,'endTime':now,'aggregateByTime':False})
    if not isinstance(fills,list) or len(fills)>=2000:return None
    matched=[f for f in fills if isinstance(f,dict) and f.get('coin')==b['coin']]
    try:
        matched.sort(key=lambda f:(f['time'],f['tid']))
        if len({f['tid'] for f in matched})!=len(matched):return None
        first=next(i for i,f in enumerate(matched) if f.get('oid')==oid)
        chain=[];position=Decimal(0);opened=Decimal(0);expected=Decimal(b['size']);closed=False;additional_oids=[]
        direction=1 if b['side']=='buy' else -1
        for fill in matched[first:]:
            if type(fill.get('time')) is not int or not start<=fill['time']<=now or not isinstance(fill.get('hash'),str) or not fill['hash'].startswith('0x'):return None
            before=Decimal(str(fill['startPosition']));size=Decimal(str(fill['sz']))
            if not before.is_finite() or not size.is_finite() or size<=0 or before!=position:return None
            if opened<expected:
                if fill.get('oid')!=oid or fill.get('side')!=('B' if direction==1 else 'A') or fill.get('dir')!=('Open Long' if direction==1 else 'Open Short'):return None
                opened+=size
                if opened>expected:return None
                position+=direction*size
            else:
                if fill.get('side')==('B' if direction==1 else 'A') and fill.get('dir')==('Open Long' if direction==1 else 'Open Short'):
                    # The user can add to the fungible exchange position. A
                    # complete, gap-free aggregate cycle to zero still proves
                    # closure, without attributing those extra trades to us.
                    if fill.get('oid')==oid:return None
                    additional_oids.append(fill.get('oid'));position+=direction*size
                else:
                    if fill.get('side')!=('A' if direction==1 else 'B') or fill.get('dir')!=('Close Long' if direction==1 else 'Close Short') or size>abs(position):return None
                    position-=direction*size
            chain.append(fill)
            if opened==expected and position==0:closed=True;break
        if not closed:return None
        dex=b.get('dex','')
        state=reader(p,{'type':'clearinghouseState','user':b['target'],'dex':dex})
        orders=reader(p,{'type':'frontendOpenOrders','user':b['target'],'dex':dex})
        if not isinstance(state,dict) or not isinstance(state.get('assetPositions'),list) or not isinstance(orders,list):return None
        for wrapped in state['assetPositions']:
            actual=wrapped.get('position',{})
            if actual.get('coin')==b['coin']:
                size=Decimal(str(actual.get('szi')))
                if not size.is_finite() or size!=0:return None
        if any(not isinstance(order,dict) or order.get('coin')==b['coin'] for order in orders):return None
        if int(time.time()*1000)-now>30000:return None
        return {'state':'closed','entry_oid':oid,'checked_ms':int(time.time()*1000),'fills':chain,
                'additional_entry_oids':additional_oids,'aggregate_cycle':bool(additional_oids),
                'flat_position_verified':True,'no_open_orders_for_coin':True,'resent':False,
                'evidence':'Exact SDK entry oid, contiguous fills from zero back to zero, then fresh same-target /info positions and orders. Historical closure only; no financial authority restored.'}
    except (KeyError,TypeError,ValueError,InvalidOperation,StopIteration):return None


def reconcile(home,row,reader,persist=True):
    import hyperliquid_tools as h
    p=h.profile(home,row['profile']);b=row['body']
    if b.get('scope')!=h.fingerprint(p):h.problem('profile_changed','Scope cambiato: nessuna riconciliazione su altro account/rete.')
    response=dict(row.get('response') or {});proof=sdk_evidence(b,response)
    proof.update(verify_live(b,p,reader,response));new_state=row['state']
    if proof['accepted'] and proof['state'] in ('active','pending_entry','exit_filled'):
        new_state='filled' if proof['leg_states']['entry']=='filled' else 'accepted'
    else:
        closure=closed_evidence(row,p,reader)
        if closure:proof['closure']=closure;proof['state']='closed';new_state='closed'
    proof['reconciled']=True;proof['resent']=False
    previous=response.get('protection')
    if previous:response['protection_history']=(response.get('protection_history',[])+[previous])[-5:]
    response['protection']=proof
    if persist:
        with h.database(home) as db:
            db.execute('UPDATE actions SET state=?,response=?,updated=? WHERE id=? AND state=? AND updated=? AND body=?',
                       (new_state,json.dumps(response),time.time(),row['id'],row['state'],row['updated'],json.dumps(b)));db.commit()
            latest=h.receipt(db.execute('SELECT * FROM actions WHERE id=?',(row['id'],)).fetchone())
    else:latest={**row,'state':new_state,'response':response}
    return {'receipt':latest,'leg_observations':proof['observations'],'reconciliation':proof,'resent':False,
            'instruction':'Riconciliazione in sola lettura. closed è una chiusura storica provata, non un nuovo ordine. Nessun rinnovo di budget/permessi o cancellazione di STOP.'}
