"""OpenAI SSE reconstruction; incomplete tool calls are never executable."""
import json
from autonomy import BudgetExceeded

class StreamInterrupted(RuntimeError):
    def __init__(self,message,partial=''):
        super().__init__(message); self.partial=partial


class StreamAPIError(StreamInterrupted):
    def __init__(self,error,partial=''):
        super().__init__('Errore API nello stream: '+json.dumps(error),partial)
        self.error=error if isinstance(error,dict) else {'message':str(error)}

def consume(response,on_text,on_model=None,on_activity=None):
    content=''; reasoning=''; calls={}; model=None; usage={}; finish=None; done=False
    render_warning=False
    def notify(callback,*args):
        nonlocal render_warning
        if callback:
            try:callback(*args)
            except UnicodeEncodeError:render_warning=True # local display, not a failed API response
    def event(raw):
        nonlocal content,reasoning,model,usage,finish,done
        if raw=='[DONE]': done=True; return
        chunk=json.loads(raw)
        if on_activity and chunk.get('usage'): notify(on_activity,chunk['usage'])
        if 'error' in chunk: raise StreamAPIError(chunk['error'],content)
        next_model=chunk.get('model')
        if next_model and next_model!=model and on_model: notify(on_model,next_model)
        model=next_model or model; usage=chunk.get('usage') or usage
        for choice in chunk.get('choices',[]):
            if choice.get('index',0)!=0: continue
            delta=choice.get('delta',{})
            text=delta.get('content') or ''
            if text: content+=text; notify(on_text,text)
            reasoning+=delta.get('reasoning_content') or ''
            finish=choice.get('finish_reason') or finish
            for call in delta.get('tool_calls',[]):
                i=call['index']; acc=calls.setdefault(i,{'id':'','type':'function','function':{'name':'','arguments':''}})
                if call.get('id'): acc['id']=call['id']
                for key in ('name','arguments'): acc['function'][key]+=call.get('function',{}).get(key) or ''
    try:
        lines=[]
        for raw in response:
            if on_activity: notify(on_activity)
            line=raw.decode('utf-8').rstrip('\r\n')
            if line.startswith('data:'): lines.append(line[5:].lstrip())
            elif not line and lines:
                event('\n'.join(lines)); lines=[]
                if done: break
        if lines and not done: event('\n'.join(lines))
        if not done and not finish: raise RuntimeError('Connessione terminata prima del completamento.')
        ordered=[calls[i] for i in sorted(calls)]
        for call in ordered:
            if not call['id'] or not call['function']['name']: raise RuntimeError('Tool call incompleta.')
            json.loads(call['function']['arguments'])
        message={'role':'assistant','content':content or None}
        if ordered: message['tool_calls']=ordered
        if reasoning: message['reasoning_content']=reasoning
        result={'model':model,'usage':usage,'choices':[{'message':message,'finish_reason':finish}]}
        if render_warning:result['_render_warning']='Console encoding fallback; original response and tool calls preserved.'
        return result
    except (BudgetExceeded,KeyboardInterrupt,StreamAPIError): raise
    except Exception as e: raise StreamInterrupted('Streaming interrotto: '+str(e),content) from e
