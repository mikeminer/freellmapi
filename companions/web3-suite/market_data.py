"""Read-only, explicitly named Binance spot market data. No order endpoints."""
import json
import re
import urllib.request
import urllib.error
from urllib.parse import urlencode
from datetime import datetime, timezone

BASE='https://data-api.binance.vision'
INTERVALS=('1m','3m','5m','15m','30m','1h','2h','4h','6h','8h','12h','1d','3d','1w','1M')

def get(path,params=None):
    url=BASE+'/api/v3/'+path+('?' + urlencode(params) if params else '')
    request=urllib.request.Request(url,headers={'User-Agent':'freellmapi/3','Accept':'application/json'})
    try:
        with urllib.request.urlopen(request,timeout=20) as response:
            raw=response.read(4_000_001)
            if len(raw)>4_000_000: raise ValueError('Risposta di mercato troppo grande.')
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        detail=exc.read(1000).decode(errors='replace')
        raise RuntimeError(f'Binance spot HTTP {exc.code}: {detail}. Nessun prezzo o simbolo sostitutivo inventato.') from None

def snapshot(pair,interval='15m',limit=100):
    if interval not in INTERVALS: raise ValueError('Intervallo non valido: '+', '.join(INTERVALS))
    symbol=re.sub(r'[/_-]','',pair.upper())
    if not re.fullmatch(r'[A-Z0-9]{5,25}',symbol): raise ValueError('Coppia non valida, esempio BTC/USDC.')
    info=get('exchangeInfo',{'symbol':symbol})
    matches=[m for m in info.get('symbols',[]) if m['symbol']==symbol]
    if len(matches)!=1: raise ValueError('Coppia non disponibile su Binance spot. Specifica un altro mercato; nessuna sostituzione automatica.')
    market=matches[0]
    ticker=get('ticker/24hr',{'symbol':symbol})
    candles=get('klines',{'symbol':symbol,'interval':interval,'limit':max(2,min(int(limit),500))})
    depth=get('depth',{'symbol':symbol,'limit':20})
    server=get('time')['serverTime']
    rows=[{'open_time_ms':r[0],'open':r[1],'high':r[2],'low':r[3],'close':r[4],
           'volume_base':r[5],'close_time_ms':r[6],'volume_quote':r[7],'closed':r[6]<server} for r in candles]
    closed=[float(r['close']) for r in rows if r['closed']]
    indicators={}
    if len(closed)>=20: indicators['sma20_closed']=sum(closed[-20:])/20
    if len(closed)>=15:
        changes=[b-a for a,b in zip(closed[-15:-1],closed[-14:])]
        gains=sum(max(x,0) for x in changes)/14; losses=sum(max(-x,0) for x in changes)/14
        indicators['rsi14_simple_closed']=100 if losses==0 and gains else 50 if losses==0 else 100-100/(1+gains/losses)
    return {'venue':'Binance spot','symbol':symbol,'base':market['baseAsset'],'quote':market['quoteAsset'],
            'market_status':market['status'],'source':BASE,'retrieved_utc':datetime.now(timezone.utc).isoformat(),
            'server_time_ms':server,'interval':interval,'ticker_24h':ticker,'candles':rows,
            'order_book':depth,'indicators':indicators,
            'limitations':'Snapshot sequenziale, non atomico né streaming; libro ordini limitato a 20 livelli. Indicatori descrittivi, non un segnale garantito. Usare solo candele chiuse per confronti storici.'}
