"""Delegate provider/key selection to the unified server using fresh readiness."""
import json
from model_health import RateLimited,chat_model,transient
from autonomy import BudgetExceeded


def supports_routing(rows):
    return any('execution_status' in r and 'supported_parameters' in r for r in rows)


def eligible(rows,payload):
    estimate=len(json.dumps(payload,ensure_ascii=False))//3+payload.get('max_tokens',4096)
    result=[]
    for row in rows:
        if row.get('execution_status')!='ready' or row.get('available') is False or not chat_model(row): continue
        params=row.get('supported_parameters')
        if payload.get('tools') and isinstance(params,list) and 'tools' not in params: continue
        context=row.get('context_window') or row.get('context_length')
        if context and int(context)<estimate: continue
        result.append(row)
    return result


def request(agent,payload,output):
    try: return agent.api('chat/completions',payload)
    except BudgetExceeded: raise
    except Exception as original:
        model=payload['model'].lower()
        runtime_selected=(getattr(agent,'model',None)=='auto' and getattr(agent,'_automatic_delivery_route',None)==payload['model'])
        missing_runtime_model=runtime_selected and str(original).startswith('API HTTP 404:') and 'model_not_found' in str(original)
        if not isinstance(original,RateLimited) and not transient(original) and not missing_runtime_model: raise
        from api_stream import StreamInterrupted
        if isinstance(original,StreamInterrupted) and model in ('auto','auto:reliable','auto:smart','auto:fast'):
            from stream_recovery import recover
            return recover(agent,payload,original,output)
        if missing_runtime_model:
            import time
            agent.cooldowns[payload['model']]=time.time()+3600
            output('Catalogo non confermato dal provider: modello automatico inesistente, rotta esclusa. Contesto e file conservati.')
        # Named profiles and explicit presets are intentional constraints.
        if (model.startswith('auto:') or model=='fusion') and not runtime_selected: raise
        if model!='auto' and not isinstance(original,RateLimited) and not runtime_selected: raise
        # Unified-key monthly budgets and account-wide caps are not model quotas.
        if getattr(original,'code',None) in ('quota_exceeded','monthly_budget_exceeded'): raise
        try: rows=agent._api('models?execution_status=ready')['data']
        except Exception:
            raise original
        agent.ready_catalog=rows
        if not eligible(rows,payload):
            output('Nessun modello pronto e compatibile nel catalogo aggiornato. Attendo il server senza riprovare modelli dalla cache.')
            raise original
        if runtime_selected and model.startswith('auto:'):
            try:
                from android_delivery import recovery_route
                alternative=recovery_route(agent,payload,{model,getattr(agent,'last_successful_model',None)})
            except Exception: alternative=None
            if alternative:
                agent._automatic_delivery_route=alternative
                payload['model']=alternative
                return agent.api('chat/completions',payload)
        output('Recupero delegato al server: auto:reliable, dopo verifica del catalogo pronto. Stessa richiesta e risultati degli strumenti conservati.')
        if runtime_selected:
            agent._automatic_delivery_route='auto:reliable'
            payload['model']='auto:reliable'
        try:return agent.api('chat/completions',{**payload,'model':'auto:reliable'})
        except Exception as failure:
            if not getattr(failure,'route',None): failure.route='auto:reliable'
            raise
