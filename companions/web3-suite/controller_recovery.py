"""Bounded inference failover for the financial controller; never retries tools."""
import time
from autonomy import BudgetExceeded
from model_health import RateLimited,transient
from server_integration import eligible

def alias(name):
    return str(name or '').lower().rsplit('/',1)[-1].removesuffix('free')

def recoverable(error):
    if isinstance(error,RateLimited):return error.code not in ('quota_exceeded','monthly_budget_exceeded')
    text=str(error)
    return transient(error) or (text.startswith(('API HTTP 400:','API HTTP 404:')) and any(c in text for c in ('model_not_found','provider_rejected_request')))

def reject_reply(agent,actual):
    bad=getattr(agent,'hyperliquid_failed_routes',set())
    bad.update((alias(actual),alias(getattr(agent,'hyperliquid_route',None))))
    agent.hyperliquid_failed_routes=bad;agent.hyperliquid_route=None
    agent.hyperliquid_protocol_bad=set(getattr(agent,'hyperliquid_protocol_bad',set()))|bad

def request(agent,payload,output,max_attempts=3):
    if type(max_attempts) is not int or not 1<=max_attempts<=3:raise ValueError('Inference attempts must be between 1 and 3')
    # Transport cooldowns expire. A timed-out inference route must not remain
    # permanently excluded after another model has advanced the same cycle.
    bad=set(getattr(agent,'hyperliquid_protocol_bad',set()))|{alias(k) for k,v in agent.cooldowns.items() if v>time.time()}
    route=getattr(agent,'hyperliquid_route',None) or payload['model']
    nonstream=getattr(agent,'hyperliquid_nonstream_routes',set())
    attempts=[]
    for _ in range(max_attempts):
        if alias(route) in bad or route is None:
            rows=agent._api('models?execution_status=ready').get('data',[])
            agent.ready_catalog=rows
            candidates=[]
            for row in eligible(rows,payload):
                name=row['id']
                if alias(name) in bad or name.startswith(('auto','fusion')) or agent.cooldowns.get(name,0)>time.time():continue
                observed=[v for k,v in agent.model_observations.items() if alias(k)==alias(name)]
                score=min((v.get('last_seconds',999) for v in observed if v.get('tool_calls_observed')),default=9999)
                candidates.append((score,name))
            if not candidates:break
            route=min(candidates,key=lambda item:item[0])[1]
        try:
            preferred=any(alias(k)==alias(route) and v.get('nonstream_preferred_until',0)>time.time() for k,v in agent.model_observations.items())
            stream_verified=any(alias(k)==alias(route) and v.get('stream_tool_verified') for k,v in agent.model_observations.items())
            attempt={**payload,'model':route}
            if alias(route) in nonstream or preferred or (payload.get('tools') and not stream_verified):
                attempt.pop('stream_options',None);attempt['stream']=False
            result=agent.api('chat/completions',attempt)
            agent.hyperliquid_route=route
            agent.hyperliquid_failed_routes=bad
            # Keep the successful requested route sticky; provider aliases are
            # not guessed or used unless they exist in a fresh catalog.
            return result
        except BudgetExceeded:raise
        except Exception as error:
            if not recoverable(error):raise
            attempts.append({'route':route,'error_type':type(error).__name__,'code':getattr(error,'code',None)})
            if _+1<max_attempts and attempt.get('stream') and alias(route) not in nonstream and not isinstance(error,RateLimited) and transient(error):
                nonstream.add(alias(route));agent.hyperliquid_nonstream_routes=nonstream
                output('Streaming modello interrotto: riprovo la stessa inferenza senza streaming, una sola volta. Contesto e ID invariati; nessuno strumento rieseguito.')
                continue
            bad.add(alias(route));agent.hyperliquid_failed_routes=bad
            delay=60 if isinstance(error,RateLimited) else 3600 if 'model_not_found' in str(error) else 10
            agent.cooldowns[route]=max(agent.cooldowns.get(route,0),time.time()+delay)
            agent.save()
            output('Recupero Hyperliquid: cambio rotta modello dopo '+type(error).__name__+'. Stessi messaggi, ID e risultati; nessuno strumento rieseguito.')
            route=None
    agent.hyperliquid_recovery_failures=attempts
    agent.save()
    raise RuntimeError(f'Recupero API Hyperliquid esaurito dopo al massimo {max_attempts} tentativi compatibili. Sessione/ID conservati; nessuno strumento rieseguito.')
