"""Bounded recovery of an interrupted inference, never of a tool execution."""
import time
from api_stream import StreamInterrupted
from autonomy import BudgetExceeded
from model_health import RateLimited,transient


class StreamRecoveryExhausted(StreamInterrupted):
    recovery_exhausted=True


def recover(agent,payload,original,output):
    from server_integration import eligible
    def alias(name):return str(name or '').casefold().rsplit('/',1)[-1]
    routing=getattr(agent,'last_request',{}).get('routing',{})
    actual=next((routing[k] for k in ('x-routed-model','x-model-used','x-model','x-routed-via') if routing.get(k)),None)
    failed={alias(actual or getattr(agent,'last_successful_model',None))}
    try:rows=agent._api('models?execution_status=ready')['data']
    except BudgetExceeded:raise
    except Exception:raise StreamRecoveryExhausted('Stream del provider interrotto; catalogo pronto non verificabile. Contesto e scelte conservati; nessuno strumento rieseguito.',getattr(original,'partial','')) from original
    agent.ready_catalog=rows
    candidates=[];seen=set(failed)
    for row in eligible(rows,payload):
        name=row['id'];ident=alias(name)
        if name.startswith(('auto','fusion')) or ident in seen or agent.cooldowns.get(name,0)>time.time():continue
        if payload.get('tools') and 'tools' not in (row.get('supported_parameters') or []):continue
        candidates.append(row);seen.add(ident)
    # Keep visual inputs on workers with a verified visual probe.
    vision=any(isinstance(m.get('content'),list) and any(p.get('type')=='image_url' for p in m['content'] if isinstance(p,dict)) for m in payload.get('messages',[]))
    if vision:candidates=[r for r in candidates if agent.model_observations.get(r['id'],{}).get('vision_probe_passed')]
    observed={alias(k) for k,v in agent.model_observations.items() if v.get('tool_calls_observed') or v.get('tool_probe_passed')}
    candidates.sort(key=lambda r:alias(r['id']) not in observed)
    last=original
    for index,row in enumerate(candidates[:2],1):
        name=row['id']
        output('Stream provider interrotto: recupero '+str(index)+'/2 con '+name+' (catalogo pronto). Scelte e risultati conservati; nessuno strumento rieseguito.')
        try:return agent.api('chat/completions',{**payload,'model':name})
        except BudgetExceeded:raise
        except Exception as error:
            if isinstance(error,RateLimited) and getattr(error,'code',None) in ('quota_exceeded','monthly_budget_exceeded'):raise
            if not isinstance(error,RateLimited) and not transient(error):raise
            agent.cooldowns[name]=max(agent.cooldowns.get(name,0),time.time()+60);last=error
    raise StreamRecoveryExhausted('Recupero stream sospeso dopo '+str(min(2,len(candidates)))+' alternative pronte compatibili. Contesto e risposte conservati; nessun ordine o strumento rieseguito.',getattr(last,'partial','')) from last
