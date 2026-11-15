"""Detect unchanged reads, repeated discovery and identical failed actions per turn."""
import hashlib
import json
import re
from autonomy import BudgetExceeded


class LoopDetected(BudgetExceeded): pass

MUTATIONS={'repair_start','repair_edit','repair_verify','repair_activate','repair_rollback','repair_add_module','strategy_propose','strategy_evaluate','strategy_activate'}
FAMILIES={name:('repair' if name.startswith('repair_') else 'strategy') for name in MUTATIONS}
FAMILIES.update(repair_read='repair',repair_status='repair',strategy_status='strategy',strategy_rollback='strategy')
PREREQUISITES={
    'quota':set(),
    'uncertain':{'repair_status','read_file'},
    'invalid_arguments':{'tool_search','runtime_capabilities','repair_read','repair_status','strategy_status'},
    'invalid_id':{'repair_status','repair_read','strategy_status'},
    'conflict':{'read_file','repair_read','repair_status'},
    'test_error':{'read_file','repair_read','repair_status'},
    'operation_error':{'repair_read','repair_status','read_file','strategy_status'},
}
WAIT_COMMAND=re.compile(r'(?i)(?:^|[;&|`(])\s*(?:start-sleep|sleep|ping\s+-n)\b')
READ_METADATA={'retrieved_ms','retrieved_utc','read_ms','checked_ms','checked_at','request_id','execution_id','latency_ms','duration_seconds'}

def economic_read(value):
    """Drop transport clocks, retain market/order times, price, size and status."""
    if isinstance(value,dict):return {k:economic_read(v) for k,v in value.items() if k not in READ_METADATA}
    if isinstance(value,list):return [economic_read(v) for v in value]
    return value


def error_class(text,data):
    blob=str(text).lower(); code=''
    if isinstance(data,dict):
        code=str(data.get('code') or data.get('error_class') or '')
        blob+=' '+code.lower()
    if code in ('rate_limit_exceeded','quota_exceeded','provider_unavailable') or any(word in blob for word in ('quota','rate_limit','cooldown','provider_unavailable')): return 'quota'
    if 'execution_uncertain' in blob or code=='execution_uncertain': return 'uncertain'
    if code=='invalid_arguments' or 'invalid_arguments' in blob or 'json valido' in blob: return 'invalid_arguments'
    if code in ('invalid_id','operation_missing','operation_id_ambiguous') or 'id riparazione non valido' in blob or 'id sessione non valido' in blob or 'id checkpoint non valido' in blob or 'id sconosciuto' in blob: return 'invalid_id'
    if any(word in blob for word in ('file_changed','recovery_conflict','call_id_conflict','conflitto')): return 'conflict'
    if code in ('test_contamination','baseline_not_reproduced','test_error') or 'test_contamination' in blob or 'baseline_not_reproduced' in blob: return 'test_error'
    return 'operation_error'


def shell_wait_bypass(command):
    return bool(WAIT_COMMAND.search(command or ''))


class LoopGuard:
    def __init__(self):
        self.results={}; self.errors={}; self.warnings=[]; self.tool_failures={}
        self.equivalent={}; self.pending={}; self.satisfied={}; self.suspensions={}; self.global_failures=0
        self.financial_recovery=None

    def before(self,name,args):
        """Skip a retry until the useful prerequisite has been read. Does not run the tool."""
        if name in self.suspensions: return json.dumps(self.suspensions[name],ensure_ascii=False)
        if name in ('hyperliquid_preview','hyperliquid_submit','hyperliquid_execute_plan') and self.financial_recovery:
            return json.dumps({'ok':False,'blocked':True,'code':'financial_prerequisite_required','message':'Anteprima rinviata: completa il recupero indicato prima di un nuovo tentativo. Nessuna operazione inviata.',
                               'recovery_tools':self.financial_recovery},ensure_ascii=False)
        family=FAMILIES.get(name)
        kind=self.pending.get(family)
        if not family or name not in MUTATIONS or not kind: return None
        if self.satisfied.get((family,kind)): return None
        needed=PREREQUISITES.get(kind) or set()
        if kind=='quota':
            self._suspend(family,kind)
            return json.dumps(self.suspensions.get(name,{'ok':False,'status':'blocked','code':'quota'}),ensure_ascii=False)
        return json.dumps({'ok':False,'code':'prerequisite_required','error_class':kind,'tool':name,
            'message':'Tentativo equivalente non eseguito. Prima leggi '+', '.join(sorted(needed))+' . Cambiare cartella, formulazione o proposta non azzera il conteggio.',
            'next_step':'Leggi lo schema, un ID valido, il file aggiornato o il log. Poi riprova la stessa riparazione.'},ensure_ascii=False)

    def _suspend(self,family,kind):
        payload={'ok':False,'status':'blocked','code':'phase_suspended','error_class':kind,
            'message':'Fase '+family+' sospesa dopo tentativi equivalenti ('+kind+'). Continua le attività indipendenti e chiudi il report senza dichiarare PASS. Non usare attese shell o subagenti per aggirare il limite.'}
        for name,group in FAMILIES.items():
            if group==family and name in MUTATIONS: self.suspensions[name]=payload

    def observe(self,name,args,result,failed=False):
        # Checkpoints have runtime-owned inspection and bounded retries in
        # checkpoint_recovery, independent of provider tool_choice support.
        if name=='context_checkpoint': return None
        text=str(result)
        try: data=json.loads(text)
        except (ValueError,TypeError): data=None
        if self.financial_recovery and isinstance(data,dict) and not (data.get('ok') is False or data.get('cancelled')):
            for needed in self.financial_recovery:
                required=needed.get('arguments',{})
                if name==needed.get('name') and all((args.get(k) or data.get(k) if k=='profile' else args.get(k))==v for k,v in required.items() if k in ('profile','coin','resource','id')):
                    if name=='ask_user' and not data.get('answer'):continue
                    if name=='hyperliquid_status' and not data.get('profiles'):continue
                    if name=='hyperliquid_read' and 'data' not in data:continue
                    if name=='hyperliquid_operations' and not (data.get('receipt') if required.get('id') else 'operations' in data):continue
                    self.financial_recovery=None;self.tool_failures.pop('hyperliquid_preview',None);break
        if name=='ask_user':return None
        if name in ('hyperliquid_preview','hyperliquid_submit','hyperliquid_operations','hyperliquid_execute_plan') and isinstance(data,dict) and data.get('ok') is False and data.get('recovery_tools'):
            self.financial_recovery=data['recovery_tools']
        failed=failed or text.startswith(('Errore:','Error:')) or (isinstance(data,dict) and (data.get('ok') is False or data.get('denied') is True or data.get('blocked') is True or data.get('status')=='blocked' or data.get('code') in ('test_contamination','baseline_not_reproduced','prerequisite_required','phase_suspended','provider_unavailable','wait_bypass_blocked','subagent_bypass_blocked','protected_controller')))
        if not failed:
            if isinstance(data,dict) and data.get('changed') is True and name in ('write_file','edit_file','project_copy'):
                self.global_failures=0 # Real file progress; unrelated past failures are not a stalled loop.
            for family,kind in list(self.pending.items()):
                if name in PREREQUISITES.get(kind,set()): self.satisfied[(family,kind)]=True
        if failed:
            kind=error_class(text,data); family=FAMILIES.get(name)
            if family and name in MUTATIONS:
                key=(family,kind); self.equivalent[key]=self.equivalent.get(key,0)+1
                self.pending[family]=kind; self.satisfied[(family,kind)]=False
                if kind=='quota' or self.equivalent[key]>=3: self._suspend(family,kind)
            self.global_failures+=1
            if self.global_failures>=8: raise LoopDetected('loop_detected: limite globale di errori raggiunto. Fase sospesa dove possibile; nessun nuovo ciclo.')
            self.tool_failures[name]=self.tool_failures.get(name,0)+1
            if self.tool_failures[name]>=4:
                raise LoopDetected(f'loop_detected: {name} ha fallito quattro volte senza un esito valido, anche con errori diversi. Correggi il prerequisito; nessun riavvio del lavoro.')
            # Invalid IDs/arguments must not defeat the circuit breaker by varying every retry.
            code=text.split(':',2)[:2]
            key=name+'|'+':'.join(code)[:180]+'|'+json.dumps(args,sort_keys=True,default=str)
            count=self.errors.get(key,0)+1; self.errors[key]=count
            if count>=4: raise LoopDetected(f'loop_detected: {name} fallisce ripetutamente. Nessuna nuova azione; verifica schema, ID e prerequisiti prima di riprovare.')
            if count==2: return 'Recupero necessario: errore già ricevuto. Consulta gli ID/schema/prerequisiti disponibili; non ripetere la stessa strategia.'
            return None
        self.tool_failures.pop(name,None)
        if name=='agent_wait': return None
        if name=='process_read' and isinstance(data,dict) and (data.get('process',{}).get('state') or data.get('state')) in ('running','waiting'): return None
        if name=='process_list' and isinstance(data,list) and any(r.get('state')=='running' for r in data): return None
        if name=='lsp_query' and isinstance(data,dict) and data.get('status')=='pending': return None
        if name in ('tools_enable','tool_search','runtime_capabilities','connections_status'):
            text='discovery' # Counters/timestamps are not meaningful new observations.
        if name in ('hyperliquid_read','hyperliquid_operations','hyperliquid_status','hyperliquid_analyze','hyperliquid_scan') and isinstance(data,dict):
            normalized=economic_read(data)
            if name in ('hyperliquid_analyze','hyperliquid_scan'):
                for key in ('as_of_ms','evidence_id','scan_id','evidence_file'):normalized.pop(key,None)
                if isinstance(normalized.get('book'),dict):normalized['book'].pop('time_ms',None)
            # limit is not progress if the returned economic state is unchanged.
            args={k:v for k,v in args.items() if k!='limit'}
            if name=='hyperliquid_operations':
                normalized.pop('lookup',None)
                for row in ([normalized['receipt']] if isinstance(normalized.get('receipt'),dict) else normalized.get('operations',[])):
                    if isinstance(row,dict):row.pop('updated',None)
                if isinstance(normalized.get('receipt'),dict):args=dict(args,id=normalized['receipt']['id'])
            text=json.dumps(normalized,sort_keys=True,default=str)
        signature=hashlib.sha256((name+json.dumps(args,sort_keys=True,default=str)+text).encode()).hexdigest()
        count=self.results.get(signature,0)+1; self.results[signature]=count
        if count>=5: raise LoopDetected(f'loop_detected: {name} ha restituito lo stesso risultato 5 volte. Turno fermato senza ricominciare il lavoro.')
        if count==3: return 'Nessun progresso: questo stesso risultato è già disponibile. Usalo per procedere; non ripetere letture o ricerche identiche.'
        return None
