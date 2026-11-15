import json
import re
import time
from email.utils import parsedate_to_datetime

class RateLimited(RuntimeError):
    def __init__(self,message,route=None,code=None):
        super().__init__(message); self.route=route; self.code=code


def transient(error):
    import urllib.error
    from api_stream import StreamInterrupted,StreamAPIError
    if isinstance(error,StreamAPIError):
        return error.error.get('code') in ('upstream_failed','provider_error','upstream_error','timeout') or error.error.get('type') in ('provider_error','stream_error')
    return isinstance(error,(TimeoutError,ConnectionResetError,urllib.error.URLError,StreamInterrupted)) or str(error).startswith(('API HTTP 500','API HTTP 502','API HTTP 503','API HTTP 504'))


def fallback_candidates(agent,payload,catalog):
    """Observed tool use, not a claim of equivalent model quality."""
    candidates=[]
    estimated=len(json.dumps(payload,ensure_ascii=False))//3+payload.get('max_tokens',4096)
    vision=any(isinstance(m.get('content'),list) and any(p.get('type')=='image_url' for p in m['content'] if isinstance(p,dict)) for m in payload.get('messages',[]))
    names=[agent.last_successful_model]+list(agent.model_observations)
    for name in names:
        if not name or name in candidates or name==payload['model'] or name.startswith(('auto','fusion')): continue
        if agent.cooldowns.get(name,0)>time.time() or not chat_model({'id':name}): continue
        observation=agent.model_observations.get(name,{})
        if name!=agent.last_successful_model and not observation.get('tool_calls_observed'): continue
        if vision and not observation.get('vision_probe_passed'): continue
        entry=next((r for r in catalog if r.get('id')==name),None)
        if entry:
            if not ready_model(entry): continue
            context=entry.get('context_window') or entry.get('context_length')
            if context and int(context)<estimated: continue
        candidates.append(name)
    return candidates[:2]

def chat_model(entry):
    ident=entry['id'].lower().split('/')[-1]
    kind=str(entry.get('type') or entry.get('task') or '').lower()
    if kind in ('embedding','embeddings','rerank','reranking'): return False
    return not (ident.startswith(('voyage-','text-embedding-','rerank-')) or 'embedding' in ident)

def ready_model(entry):
    return entry.get('available') is not False and entry.get('execution_status') not in ('exhausted','needsKey')

def catalog_context(catalog,model):
    exact=[m for m in catalog if m.get('id')==model]
    if not exact and model.startswith('auto:'):exact=[m for m in catalog if m.get('id')=='auto']
    matches=exact or [m for m in catalog if m.get('id','').rsplit('/',1)[-1]==model.rsplit('/',1)[-1]]
    sizes=[m.get('context_window') or m.get('context_length') for m in matches]
    sizes=[int(n) for n in sizes if isinstance(n,(int,float)) and n>=4096]
    return min(sizes) if sizes else 16000

def cooldown(headers,body,now=None):
    now=time.time() if now is None else now
    waits=[]
    retry=headers.get('Retry-After','')
    try: waits.append(float(retry))
    except (ValueError,TypeError):
        try: waits.append(parsedate_to_datetime(retry).timestamp()-now)
        except (ValueError,TypeError,OverflowError): pass
    try:
        data=json.loads(body); error=data.get('error',{})
        reset=error.get('retryAtMs',data.get('retryAtMs'))
        if reset is not None: waits.append(float(reset)/1000-now)
        message=str(error.get('message',''))
        match=re.search(r'~?\s*(\d+(?:\.\d+)?)\s*(h|hours?|min|minutes?|s|seconds?)\b',message,re.I)
        if match: waits.append(float(match[1])*(3600 if match[2].lower().startswith('h') else 60 if match[2].lower().startswith('m') else 1))
    except (ValueError,TypeError,AttributeError): pass
    return max([w for w in waits if w>0],default=60)
