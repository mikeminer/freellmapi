"""Read-only, reproducible Hyperliquid market diagnostics. Not a prediction model."""
import hashlib,json,math,time
from concurrent.futures import ThreadPoolExecutor

INTERVALS={'5m':300,'15m':900,'1h':3600,'4h':14400,'1d':86400}


def finite(value,positive=False):
    number=float(value)
    if not math.isfinite(number) or (positive and number<=0):raise ValueError('Invalid finite market number')
    return number


def ema(series,period):
    value=sum(series[:period])/period;alpha=2/(period+1)
    for price in series[period:]:value+=alpha*(price-value)
    return value


def diagnostics(bars,interval,now):
    duration=INTERVALS[interval]*1000;closed=[]
    if not isinstance(bars,list):raise ValueError('Invalid candle response')
    for bar in bars:
        if int(bar['T'])>=now:continue
        if bar.get('i',interval)!=interval:raise ValueError('Wrong candle interval')
        end=int(bar['T']);start=int(bar['t'])
        if not 0<end-start<=duration or end-start<duration-1:raise ValueError('Invalid candle duration')
        o,c,h,l=(finite(bar[k],True) for k in ('o','c','h','l'));v=finite(bar['v'])
        if l>min(o,c) or h<max(o,c) or l>h or v<0:raise ValueError('Invalid OHLCV')
        closed.append({'t':start,'T':end,'o':o,'c':c,'h':h,'l':l,'v':v})
    closed.sort(key=lambda b:b['t']);closed=closed[-120:]
    if len(closed)<60:raise ValueError('At least 60 closed candles required')
    if any(b['t']-a['t']!=duration for a,b in zip(closed,closed[1:])):raise ValueError('Duplicate or gapped candles')
    if not 0<=now-closed[-1]['T']<=duration+15000:raise ValueError('Stale candle history')
    closes=[b['c'] for b in closed];fast=ema(closes,20);slow=ema(closes,50)
    ranges=[max(b['h']-b['l'],abs(b['h']-a['c']),abs(b['l']-a['c'])) for a,b in zip(closed,closed[1:])]
    atr=sum(ranges[:14])/14
    moves=[b-a for a,b in zip(closes,closes[1:])];gain=sum(max(m,0) for m in moves[:14])/14;loss=sum(max(-m,0) for m in moves[:14])/14
    for tr in ranges[14:]:atr=(atr*13+tr)/14
    for move in moves[14:]:gain=(gain*13+max(move,0))/14;loss=(loss*13+max(-move,0))/14
    rsi=100-100/(1+gain/loss) if loss else 100 if gain else 50
    prior_volume=sum(b['v'] for b in closed[-21:-1])/20
    return {'closed_count':len(closed),'last_close_ms':closed[-1]['T'],'close':closes[-1],
            'ema20':fast,'ema50':slow,'ema20_change_3_bars':fast-ema(closes[:-3],20),
            'atr14_wilder':atr,'rsi14_wilder':rsi,'volume_ratio20':closed[-1]['v']/prior_volume if prior_volume else None,
            'support20':min(b['l'] for b in closed[-20:]),'resistance20':max(b['h'] for b in closed[-20:]),
            'regime':'up' if closes[-1]>fast>slow else 'down' if closes[-1]<fast<slow else 'mixed',
            'last_closed_bars':closed[-6:]}


def analyze(home,args,reader=None):
    import hyperliquid_tools as h
    p=h.profile(home,args.get('profile'));coin=args.get('coin');meta,dex=h.market(p,coin)
    coin=meta['name'];reader=reader or h.info;now=int(time.time()*1000)
    fast=args.get('interval','15m');slow=args.get('higher_interval','1h')
    if fast not in INTERVALS or slow not in INTERVALS or INTERVALS[fast]>=INTERVALS[slow]:h.problem('analysis_intervals','Usa timeframe crescenti tra 5m, 15m, 1h, 4h, 1d.')
    queries={interval:{'type':'candleSnapshot','req':{'coin':coin,'interval':interval,'startTime':now-122*INTERVALS[interval]*1000,'endTime':now}} for interval in (fast,slow)}
    queries.update(book={'type':'l2Book','coin':coin},context={'type':'metaAndAssetCtxs','dex':dex},fees={'type':'userFees','user':p['account']})
    values={};errors={}
    with ThreadPoolExecutor(max_workers=5) as pool:
        pending={name:pool.submit(reader,p,body) for name,body in queries.items()}
        for name,future in pending.items():
            try:values[name]=future.result()
            except Exception as exc:errors[name]=type(exc).__name__
    frames={}
    for interval in (fast,slow):
        try:frames[interval]=diagnostics(values.get(interval),interval,now)
        except (ValueError,KeyError,TypeError,OverflowError) as exc:errors[interval]=str(exc)[:120]
    book={};context={};fees={}
    try:
        raw=values['book'];bid=raw['levels'][0][0];ask=raw['levels'][1][0]
        best_bid=finite(bid['px'],True);best_ask=finite(ask['px'],True);mid=(best_bid+best_ask)/2
        if best_ask<best_bid or abs(int(time.time()*1000)-int(raw['time']))>30000:raise ValueError('Stale/crossed order book')
        book={'time_ms':raw['time'],'best_bid':best_bid,'best_ask':best_ask,'mid':mid,'spread_bps':(best_ask-best_bid)/mid*10000,
              'visible_depth_usd':{side:sum(finite(x['px'],True)*finite(x['sz'],True) for x in raw['levels'][n]) for n,side in enumerate(('bids','asks'))},
              'note':'Visible L2 snapshot only; not a forecast of fills or future liquidity.'}
    except (ValueError,KeyError,IndexError,TypeError,OverflowError):errors['book']='Live book invalid or stale'
    try:
        metadata,contexts=values['context'];index=next(i for i,r in enumerate(metadata['universe']) if r['name']==coin)
        raw=contexts[index];context={k:finite(raw[k],k in ('markPx','oraclePx')) for k in ('markPx','oraclePx','funding','openInterest','dayNtlVlm')}
        context['funding_unit']='rate per hourly settlement; not an 8h realized payment'
    except (ValueError,KeyError,IndexError,TypeError,StopIteration):errors['context']='Mark/funding context unavailable'
    try:
        raw=values['fees'];fees={k:finite(raw[k]) for k in ('userCrossRate','userAddRate')}
    except (ValueError,KeyError,TypeError):errors['fees']='Actual account fee rates unavailable; do not assume zero costs'
    result={'profile':p['name'],'network':p['network'],'coin':coin,'source':h.URLS[p['network']]+'/info','as_of_ms':int(time.time()*1000),
            'timeframes':frames,'book':book,'derivatives':context,'fees':fees,'errors':errors,'data_ready':not errors,
            'interpretation':'Indicators describe observed history, not probabilities or guaranteed TP. Compare LONG and SHORT with invalidation and costs; HOLD when data/setup is insufficient. No orders or limits changed.'}
    result['evidence_id']=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()[:24]
    return result


def scan(home,args,reader=None):
    """Cover every listed perp DEX once, save a complete read-only universe.

    Ranking is liquidity/absolute 24h movement, never a directional signal.
    Candle-level analysis is a separate step for selected candidates.
    """
    from pathlib import Path
    import re
    import hyperliquid_tools as h
    from reliable_runtime import atomic_bytes
    p=h.profile(home,args.get('profile'));reader=reader or h.info
    limit=args.get('limit',20);offset=args.get('offset',0)
    if type(limit) is not int or not 1<=limit<=50 or type(offset) is not int or offset<0:h.problem('scan_page','limit 1–50 e offset intero non negativo.')
    root=h.folder(home)/'analysis/scans';ident=args.get('scan_id')
    if ident:
        if not isinstance(ident,str) or not re.fullmatch(r'[a-f0-9]{24}',ident):h.problem('scan_missing','Usa lo scan_id nativo restituito dalla scansione.')
        path=root/(ident+'.json')
        if not path.is_file():h.problem('scan_missing','Scansione non trovata: crea una nuova scansione.')
        result=json.loads(path.read_text(encoding='utf-8'))
        if result['profile']!=p['name'] or result['network']!=p['network']:h.problem('scan_scope','Scansione di un altro profilo/rete.')
    else:
        dexs=reader(p,{'type':'perpDexs'})
        if not isinstance(dexs,list) or not dexs or dexs[0] is not None:h.problem('scan_directory','Directory DEX non verificata; nessun risultato completo dichiarato.')
        names=['']+[d['name'] for d in dexs[1:] if isinstance(d,dict) and isinstance(d.get('name'),str)]
        if len(names)!=len(dexs) or len(set(names))!=len(names):h.problem('scan_directory','Directory DEX incompleta o duplicata.')
        rows=[];errors={};delisted=0
        with ThreadPoolExecutor(max_workers=4) as pool:
            pending={dex:pool.submit(reader,p,{'type':'metaAndAssetCtxs','dex':dex}) for dex in names}
            for dex,future in pending.items():
                try:
                    metadata,contexts=future.result();markets=metadata['universe']
                    if len(markets)!=len(contexts):raise ValueError('Incomplete market contexts')
                    for meta,context in zip(markets,contexts):
                        if meta.get('isDelisted'):delisted+=1;continue
                        try:
                            mark=finite(context['markPx'],True);previous=finite(context['prevDayPx'],True);volume=finite(context['dayNtlVlm']);oi=finite(context['openInterest'])
                            if volume<0 or oi<0:raise ValueError('Negative market size')
                            rows.append({'coin':meta['name'],'dex':dex,'collateral_token':metadata.get('collateralToken'),
                                'mark':mark,'change24h_pct':(mark/previous-1)*100,'volume24h_usd':volume,'open_interest_base':oi,
                                'funding':finite(context['funding']),'max_leverage':meta.get('maxLeverage'),'sz_decimals':meta['szDecimals']})
                        except (KeyError,ValueError,TypeError,OverflowError):errors[dex+':'+str(meta.get('name'))]='Invalid/missing market context'
                except Exception as exc:errors[dex or 'default']=type(exc).__name__
        rows.sort(key=lambda x:(x['volume24h_usd'],abs(x['change24h_pct'])),reverse=True)
        result={'profile':p['name'],'network':p['network'],'as_of_ms':int(time.time()*1000),'source':h.URLS[p['network']]+'/info',
            'dexs_requested':len(names),'markets_scanned':len(rows),'delisted_excluded':delisted,'complete':not errors,'errors':errors,'markets':rows,
            'note':'Universe screening only. Rank by liquidity, then absolute change, not long/short conviction. Run hyperliquid_analyze on candidates; no candle analysis or profitability claimed for every market. Collateral tokens and local execution support differ across DEXs.'}
        ident=hashlib.sha256(json.dumps(result,sort_keys=True).encode()).hexdigest()[:24];result['scan_id']=ident
        root.mkdir(parents=True,exist_ok=True);atomic_bytes(root/(ident+'.json'),json.dumps(result,ensure_ascii=False,indent=2).encode('utf-8'))
    return {**result,'markets':result['markets'][offset:offset+limit],'offset':offset,'limit':limit,
            'next_offset':offset+limit if offset+limit<len(result['markets']) else None,'evidence_file':str((root/(ident+'.json')).resolve())}
