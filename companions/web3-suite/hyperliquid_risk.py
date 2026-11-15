"""Conservative USDC risk accounting; never signs or changes exchange state.

Unified spot balances are the source of collateral. Positive unrealized PnL is
not credited; negative PnL, holds, position margin and pending entry notional
are reserved conservatively. These are risk limits, not a portfolio valuation.
"""
from concurrent.futures import ThreadPoolExecutor,wait
from decimal import Decimal,InvalidOperation
import time

VERSION='usdc-unified-v1'

class RiskError(ValueError):
    def __init__(self,code,message,**details):self.code=code;self.details=details;super().__init__(message)

def reject(code,message,**details):raise RiskError(code,message,**details)

def dec(value,label,negative=False):
    try:r=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError):reject('risk_data_invalid','Dato rischio non valido: '+label)
    if not r.is_finite() or (r<0 and not negative):reject('risk_data_invalid','Dato rischio non finito/negativo: '+label)
    return r

def text(v):return format(v.normalize(),'f')

def directory(dexs,metas,selected):
    if not isinstance(dexs,list) or not isinstance(metas,list) or not 1<=len(dexs)<=32 or len(dexs)!=len(metas):
        reject('risk_directory_incomplete','Elenco DEX/metadati incompleto o oltre il limite di scansione; nessun rischio nuovo.')
    result={}
    for i,(dex,meta) in enumerate(zip(dexs,metas)):
        name='' if i==0 and dex is None else dex.get('name') if isinstance(dex,dict) else None
        if name is None or name in result or not isinstance(meta,dict) or type(meta.get('collateralToken')) is not int:
            reject('risk_directory_incomplete','Identità DEX/collaterale non verificata.')
        if not isinstance(meta.get('universe'),list):reject('risk_directory_incomplete','Universo DEX mancante.')
        if any((str(a.get('name','')).split(':')[0] if ':' in str(a.get('name','')) else '')!=name for a in meta['universe']):
            reject('risk_directory_incomplete','Ordine dei metadati DEX non coerente con i nomi dei mercati.')
        result[name]=meta['collateralToken']
    if result.get(selected)!=0:reject('unsupported_collateral','Unified Account: esecuzione supportata solo per perpetual con collaterale USDC token 0.')
    return result

def unified(spot,collaterals,states,orders,selected):
    if set(states)!=set(collaterals) or set(orders)!=set(collaterals):reject('risk_scan_incomplete','Manca almeno un DEX: nessuna stima parziale del rischio.')
    balances=spot.get('balances') if isinstance(spot,dict) else None
    if not isinstance(balances,list):reject('risk_data_invalid','Saldi Unified Account mancanti.')
    usdc=[b for b in balances if b.get('token')==0]
    if len(usdc)!=1 or usdc[0].get('coin')!='USDC':reject('collateral_identity','Saldo canonico USDC token 0 non verificato.')
    cash=dec(usdc[0].get('total'),'USDC total');hold=dec(usdc[0].get('hold'),'USDC hold')
    positions=[];open_orders=[];loss=Decimal(0);used=Decimal(0);isolated=Decimal(0);maintenance=Decimal(0);seen=set()
    for dex,token in collaterals.items():
        state=states[dex];pending=orders[dex]
        if not isinstance(state,dict) or not isinstance(state.get('assetPositions'),list) or not isinstance(pending,list):reject('risk_scan_incomplete','Posizioni/ordini DEX mancanti.')
        active=[x for x in state['assetPositions'] if dec(x.get('position',{}).get('szi'),'position size',True)!=0]
        if token!=0:
            if active or pending:reject('unsupported_collateral_exposure','Esposizione su altro collaterale: non convertirla implicitamente in USD; nessun rischio nuovo.')
            continue
        maintenance+=dec(state.get('crossMaintenanceMarginUsed'),'cross maintenance')
        for wrapped in active:
            p=wrapped['position'];margin=dec(p.get('marginUsed'),'position margin')
            if p.get('leverage',{}).get('type') not in ('cross','isolated'):reject('risk_data_invalid','Tipo margine posizione non verificato.')
            used+=margin
            if p['leverage']['type']=='isolated':isolated+=margin
            loss+=min(Decimal(0),dec(p.get('unrealizedPnl'),'unrealized PnL',True))
            dec(p.get('positionValue'),'position notional',True)
            positions.append(dict(p,dex=dex))
        for order in pending:
            key=(dex,order.get('oid'))
            if key[1] is None or key in seen:reject('risk_data_invalid','ID ordine mancante/duplicato durante scansione rischio.')
            seen.add(key)
            dec(order.get('sz'),'order size');dec(order.get('limitPx'),'order limit')
            open_orders.append(dict(order,dex=dex))
    equity=cash+loss
    # Reserve full pending entry notional, even if an order has leverage. Holds
    # can overlap margin reserves: double reservation is intentionally conservative.
    pending=sum((dec(o['sz'],'size')*dec(o['limitPx'],'price') for o in open_orders if not o.get('reduceOnly',False)),Decimal(0))
    available=max(Decimal(0),equity-hold-used-pending)
    caps=spot.get('tokenToAvailableAfterMaintenance')
    if caps is not None:
        rows=[r for r in caps if isinstance(r,list) and len(r)==2 and r[0]==0]
        if len(rows)!=1:reject('risk_data_invalid','Disponibilità USDC dopo maintenance non verificata.')
        available=min(available,dec(rows[0][1],'available after maintenance'))
    denominator=cash-isolated
    if maintenance>0 and denominator<=maintenance:reject('maintenance_risk','Collaterale libero insufficiente per maintenance; nessun rischio nuovo.')
    ratio=maintenance/denominator if denominator>0 else Decimal(0)
    gross=sum((abs(dec(p['positionValue'],'position notional',True)) for p in positions),Decimal(0))+pending
    return {'version':VERSION,'account_mode':'unifiedAccount','collateral_token':0,'collateral_usdc':text(cash),
            'risk_equity_usd':text(equity),'available_usd':text(available),'spot_hold_usdc':text(hold),
            'negative_unrealized_pnl_usd':text(loss),'position_margin_usd':text(used),'isolated_margin_usd':text(isolated),
            'pending_entry_notional_usd':text(pending),'gross_exposure_usd':text(gross),
            'cross_maintenance_usd':text(maintenance),'unified_maintenance_ratio':text(ratio),
            'dexs_scanned':len(collaterals),'positions':positions,'orders':open_orders,
            'conservative':True,'note':'USDC risk budget only, not total account value. No credit for positive unrealized PnL or other spot assets.'}

def snapshot(p,query,dex='',mode=None):
    started=time.monotonic();window_start_ms=int(time.time()*1000);mode=mode or query(p,{'type':'userAbstraction','user':p['target']})
    if mode in ('default','disabled'):
        raw=query(p,{'type':'clearinghouseState','user':p['target'],'dex':dex})
        return {'version':VERSION,'account_mode':mode,'risk_equity_usd':text(dec(raw['marginSummary']['accountValue'],'perps equity')),
                'positions':[x['position'] for x in raw['assetPositions']],'state':raw}
    if mode!='unifiedAccount':reject('unsupported_account_mode','Modalità rischio non supportata: '+str(mode)+'. Nessun cambio modalità automatico.')
    collaterals=directory(query(p,{'type':'perpDexs'}),query(p,{'type':'allPerpMetas'}),dex)
    spot=query(p,{'type':'spotClearinghouseState','user':p['target']})
    pool=ThreadPoolExecutor(max_workers=4)
    futures={(name,kind):pool.submit(query,p,{'type':kind,'user':p['target'],'dex':name}) for name in collaterals for kind in ('clearinghouseState','frontendOpenOrders')}
    try:
        done,pending=wait(futures.values(),timeout=15)
        if pending:reject('risk_scan_timeout','Scansione rischio incompleta entro 15s. Nessuna firma o invio.')
        states={name:futures[name,'clearinghouseState'].result() for name in collaterals}
        orders={name:futures[name,'frontendOpenOrders'].result() for name in collaterals}
    finally:pool.shutdown(wait=False,cancel_futures=True)
    if time.monotonic()-started>20:reject('risk_snapshot_stale','Snapshot rischio troppo lento; nessuna firma con dati vecchi.')
    result=unified(spot,collaterals,states,orders,dex);result['retrieved_ms']=int(time.time()*1000);result['window_start_ms']=window_start_ms
    return result

def capacity(risk,asset,body,notional):
    if risk['account_mode']!='unifiedAccount' or body.get('reduce_only'):return
    caps=asset.get('maxTradeSzs');funds=asset.get('availableToTrade')
    if not isinstance(caps,list) or len(caps)!=2 or not isinstance(funds,list) or len(funds)!=2:reject('asset_capacity_unverified','Capacità reale activeAssetData mancante; nessun ingresso.')
    if dec(body['size'],'size')>min(dec(x,'max trade size') for x in caps):reject('asset_size_cap','Size oltre la capacità exchange; ricalcola entro i limiti o HOLD.')
    leverage=dec(asset.get('leverage',{}).get('value'),'actual leverage')
    if leverage<1:reject('risk_data_invalid','Leva effettiva non valida.')
    required=notional/leverage+notional*Decimal('0.002')
    available=min(dec(risk['available_usd'],'free margin'),*(dec(x,'available to trade') for x in funds))
    if required>available:
        reject('unified_margin_cap','Il saldo non è necessariamente zero: questa quantità richiede '+text(required)+' USDC di margine prudenziale, contro '+text(available)+' disponibili. Include una riserva prudenziale dello 0,2% del nozionale, non una stima esatta delle commissioni. Rileggi lo snapshot e rivaluta il piano o HOLD; nessun reinvio invariato.',
               required_margin_usdc=text(required),available_margin_usdc=text(available),collateral_usdc=risk.get('collateral_usdc'),
               actual_leverage=text(leverage),reserve_notional_rate='0.002',max_notional_before_other_limits_usd=text(available/(1/leverage+Decimal('0.002'))))
    return text(required)
