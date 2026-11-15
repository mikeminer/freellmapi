"""Trusted proposal-only strategies. No broker, SDK, signer or credential access.

The local rule strategy is an experimental baseline, not a profitability claim.
"""
from decimal import Decimal,ROUND_DOWN,ROUND_UP
from trade_plan import TradePlan


CATALOG={
    'ai':{'name':'multimodel-planner','version':1,'label':'AI multimodello','requires_model':True},
    'rules':{'name':'breakout-v1','version':1,'label':'Regole locali · breakout v1 (sperimentale)','requires_model':False},
}


class RulesStrategy:
    def __init__(self):self.telemetry={}
    def __call__(self,snapshot,account,config,history):
        from trading_engine import dec,number
        self.telemetry={'strategy':{'kind':'rules',**CATALOG['rules']},'model':None,'requests':0,'usage':{},'experimental':True}
        def hold(reason):return TradePlan.parse({'decision':'hold','reason':reason})
        try:
            analysis=snapshot.get('analysis') or {};frames=analysis.get('timeframes',{});coin=snapshot['coin']
            if analysis.get('data_ready') is not True or len(frames)<2:return hold('Dati analitici insufficienti: nessun setup a regole.')
            if analysis.get('profile')!=config['profile'] or analysis.get('network')!=config['network'] or analysis.get('coin')!=coin:return hold('Scope dei dati diverso dalla policy.')
            regimes=[v['regime'] for v in frames.values()]
            if all(r=='up' for r in regimes):side='buy'
            elif all(r=='down' for r in regimes):side='sell'
            else:return hold('Timeframe discordanti: il baseline a regole attende allineamento.')
            exchange_only=config.get('execution_policy')=='exchange'
            if (not exchange_only and coin not in config['coins']) or coin in {p['coin'] for p in account['positions']}|set(account.get('pending_entry_coins',[])):return hold('Mercato già occupato o fuori dallo scope legacy.')
            if not exchange_only and len(account['positions'])>=config['max_positions']:return hold('Limite posizioni raggiunto.')
            interval=next(iter(frames));frame=frames[interval];atr=dec(frame['atr14_wilder'])
            bid,ask=dec(snapshot['bid']),dec(snapshot['ask']);precision=snapshot['sz_decimals']
            if atr<=0 or bid<=0 or ask<bid or type(precision) is not int or not 0<=precision<=6:return hold('ATR, book o precisione non utilizzabili.')
            def price(value,up):
                tick=Decimal(10)**max(-(6-precision),value.adjusted()-4)
                return value.quantize(tick,rounding=ROUND_UP if up else ROUND_DOWN)
            if side=='buy':
                reference=max(ask,dec(frame['resistance20']));entry=price(reference+Decimal(10)**max(-(6-precision),reference.adjusted()-4),True)
                sl=price(entry-atr*Decimal('1.5'),False);tp=price(entry+atr*3,True)
            else:
                reference=min(bid,dec(frame['support20']));entry=price(reference-Decimal(10)**max(-(6-precision),reference.adjusted()-4),False)
                sl=price(entry+atr*Decimal('1.5'),True);tp=price(entry-atr*3,False)
            if min(entry,tp,sl)<=0:return hold('Livelli non positivi: HOLD.')
            leverage=int(snapshot['active_leverage']) if exchange_only else min(int(snapshot['active_leverage']),config['max_leverage'])
            if leverage<1:return hold('Leva effettiva non verificata.')
            fees=analysis.get('fees') or {};fee=dec(fees['userCrossRate'])
            if not 0<=fee<Decimal('.01'):return hold('Commissioni effettive non utilizzabili.')
            exposure=sum((abs(dec(p['size']))*dec(p['mark']) for p in account['positions']),Decimal(0))+dec(account.get('pending_notional',0))
            cap=dec(account['available'])*leverage if exchange_only else min(dec(config['max_order_usd']),dec(config['max_exposure_usd'])-exposure,dec(config['max_turnover_usd'])-dec(account['turnover']),dec(account['available'])*leverage)
            risk_left=dec(account['available']) if exchange_only else dec(config['max_loss_usd'])-max(Decimal(0),dec(account['baseline'])-dec(account['equity']))
            if cap<=0 or risk_left<=0:return hold('Budget o rischio residuo esaurito.')
            reserve=Decimal('.002')+fee*2
            loss_per_unit=abs(entry-sl)+(entry+sl)*fee+entry*dec(config['max_slippage_bps'])/10000*2
            size=min(cap/(entry*(1+reserve)),risk_left/loss_per_unit).quantize(Decimal(10)**-precision,rounding=ROUND_DOWN)
            if size<=0 or size*entry<10:return hold('Quantità entro i limiti sotto il minimo: nessun aumento automatico.')
            cost_per_unit=(entry+tp)*fee+entry*dec(config['max_slippage_bps'])/10000*2
            funding=dec((analysis.get('derivatives') or {})['funding'])
            ttl=min(3600,config['max_watch_seconds']);paying=max(Decimal(0),funding*(1 if side=='buy' else -1))
            cost_per_unit+=entry*paying*Decimal(ttl)/3600
            if abs(tp-entry)<=cost_per_unit:return hold('Target teorico assorbito da costi/slippage/funding stimati.')
            confirmation_interval=interval if interval in ('1m','5m','15m','1h','4h') else '15m'
            return TradePlan.parse({'decision':'watch','coin':coin,'side':side,'size':number(size),'entry':number(entry),'take_profit':number(tp),'stop_loss':number(sl),'leverage':leverage,
                'reason':'Baseline sperimentale: trend concordi e breakout futuro confermato a candela chiusa; scenario contrario: falso breakout/inversione, invalidato allo SL. Nessuna probabilità di successo stimata.',
                'trigger':{'kind':'breakout','direction':'above' if side=='buy' else 'below','level':number(entry),'confirmation':'close','interval':confirmation_interval,'ttl_seconds':ttl}})
        except (ValueError,KeyError,TypeError,ArithmeticError):return hold('Dati mancanti/non validi per la strategia a regole; nessuna operazione.')


class StrategyPlanner:
    """One proposal interface for both implementations; only allowlisted sources."""
    def __init__(self,home,config):
        from trading_engine import TradingError
        source=config.get('strategy_source','ai')
        if source not in CATALOG:raise TradingError('unknown_strategy_source')
        self.source=source;self.telemetry={}
        if source=='ai':
            from trading_runtime import Planner
            self.impl=Planner(home,config)
        else:self.impl=RulesStrategy()
    def __call__(self,snapshot,account,config,history):
        from trading_engine import TradingError
        from trade_plan import PlanError
        try:
            value=self.impl(snapshot,account,config,history)
            return TradePlan.parse(value)
        except PlanError as exc:raise TradingError(str(exc)) from None
        finally:self.telemetry={**getattr(self.impl,'telemetry',{}),'strategy':{'kind':self.source,**CATALOG[self.source]},'plan_schema_version':1}
