"""Immutable, versioned proposal DTO. It carries no credentials or authority."""
import json
from dataclasses import dataclass
from decimal import Decimal,InvalidOperation
from typing import Literal


class PlanError(ValueError):
    def __init__(self,code,**details):
        super().__init__(code);self.details=details


def amount(value,label):
    if isinstance(value,bool) or not isinstance(value,(str,int,float,Decimal)):raise PlanError('invalid_plan_'+label)
    try:d=Decimal(str(value))
    except (InvalidOperation,TypeError):raise PlanError('invalid_plan_'+label) from None
    if not d.is_finite() or d<=0 or d>Decimal('1e15'):raise PlanError('invalid_plan_'+label)
    return format(d.normalize(),'f')


@dataclass(frozen=True)
class EntryTrigger:
    kind:Literal['breakout','pullback']
    direction:Literal['above','below']
    level:str
    confirmation:Literal['touch','close','retest']
    ttl_seconds:int
    interval:str|None=None
    retest_tolerance_bps:int|None=None

    @classmethod
    def parse(cls,value):
        allowed={'kind','direction','level','confirmation','ttl_seconds','interval','retest_tolerance_bps'}
        if not isinstance(value,dict) or set(value)-allowed:raise PlanError('invalid_entry_trigger_fields')
        if value.get('kind') not in ('breakout','pullback') or value.get('direction') not in ('above','below'):raise PlanError('invalid_entry_trigger')
        confirmation=value.get('confirmation')
        if confirmation not in ('touch','close','retest'):raise PlanError('invalid_confirmation')
        ttl=value.get('ttl_seconds')
        if type(ttl) is not int or not 60<=ttl<=86400:raise PlanError('invalid_watch_ttl')
        interval=value.get('interval')
        if confirmation=='close' and interval not in ('1m','5m','15m','1h','4h'):raise PlanError('close_interval_required')
        if interval is not None and interval not in ('1m','5m','15m','1h','4h'):raise PlanError('invalid_trigger_interval')
        tolerance=value.get('retest_tolerance_bps')
        if tolerance is not None and (type(tolerance) is not int or not 1<=tolerance<=1000):raise PlanError('invalid_retest_tolerance')
        if confirmation=='retest' and value['kind']!='breakout':raise PlanError('retest_requires_breakout')
        return cls(value['kind'],value['direction'],amount(value.get('level'),'trigger_level'),confirmation,ttl,interval,tolerance)

    def to_dict(self):
        out={'kind':self.kind,'direction':self.direction,'level':self.level,'confirmation':self.confirmation,'ttl_seconds':self.ttl_seconds}
        if self.interval is not None:out['interval']=self.interval
        if self.retest_tolerance_bps is not None:out['retest_tolerance_bps']=self.retest_tolerance_bps
        return out


@dataclass(frozen=True)
class TradePlan:
    decision:Literal['hold','watch','enter']
    reason:str
    coin:str|None=None
    side:Literal['buy','sell']|None=None
    size:str|None=None
    entry:str|None=None
    take_profit:str|None=None
    stop_loss:str|None=None
    leverage:int|None=None
    trigger:EntryTrigger|None=None
    schema_version:int=1

    @classmethod
    def parse(cls,value):
        if isinstance(value,cls):
            if value.schema_version!=1:raise PlanError('unsupported_plan_version')
            try:value=value.to_dict()
            except (AttributeError,TypeError):raise PlanError('invalid_typed_plan') from None
        allowed={'decision','reason','coin','side','size','entry','take_profit','stop_loss','leverage','trigger'}
        if not isinstance(value,dict):raise PlanError('invalid_plan_fields',field='plan',expected_type='JSON object')
        unknown=sorted(set(value)-allowed)
        if unknown:raise PlanError('invalid_plan_fields',field='plan',unknown_fields=unknown,allowed_fields=sorted(allowed))
        reason=value.get('reason');decision=value.get('decision')
        if not isinstance(reason,str) or not 1<=len(reason)<=1200:raise PlanError('reason_required')
        if decision=='hold':
            if set(value)-{'decision','reason'}:raise PlanError('hold_has_order_fields')
            return cls('hold',reason)
        if decision not in ('watch','enter'):raise PlanError('invalid_plan_decision')
        missing=sorted({'coin','side','size','entry','take_profit','stop_loss','leverage'}-set(value))
        if missing:raise PlanError('missing_plan_fields:'+','.join(missing),field='plan',missing_fields=missing)
        coin=value['coin'];side=value['side'];leverage=value['leverage']
        if not isinstance(coin,str) or not coin or len(coin)>40 or any(c.isspace() for c in coin):raise PlanError('invalid_plan_coin')
        if side not in ('buy','sell'):raise PlanError('invalid_side')
        if type(leverage) is not int or leverage<1:raise PlanError('invalid_plan_leverage')
        fields={k:amount(value[k],k) for k in ('size','entry','take_profit','stop_loss')}
        entry,tp,sl=(Decimal(fields[k]) for k in ('entry','take_profit','stop_loss'))
        if not (sl<entry<tp if side=='buy' else tp<entry<sl):raise PlanError('invalid_bracket')
        trigger=EntryTrigger.parse(value['trigger']) if value.get('trigger') is not None else None
        if decision=='watch' and trigger is None:raise PlanError('watch_trigger_required')
        if trigger:
            expected='above' if (side=='buy')==(trigger.kind=='breakout') else 'below'
            if trigger.direction!=expected:raise PlanError('trigger_direction_side_mismatch')
            if decision=='watch' and trigger.level!=fields['entry']:raise PlanError('trigger_level_must_equal_reference_entry')
        return cls(decision,reason,coin,side,leverage=leverage,trigger=trigger,**fields)

    def to_dict(self):
        out={'decision':self.decision,'reason':self.reason}
        if self.decision!='hold':
            out.update({k:getattr(self,k) for k in ('coin','side','size','entry','take_profit','stop_loss','leverage')})
            if self.trigger is not None:out['trigger']=self.trigger.to_dict()
        return out

    def envelope(self):return {'schema':'freellmapi.trade-plan','version':self.schema_version,'plan':self.to_dict()}
