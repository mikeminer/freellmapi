"""Evidence-backed project memory and inspectable bounded retrieval."""
import hashlib
import json
import re
import threading
from datetime import datetime, timezone

CHECKPOINT_KINDS = ('objective','constraint','state','issue','next_step','decision','preference','verified')

class CheckpointError(ValueError):
    def __init__(self, code, field, message):
        super().__init__(message)
        self.code, self.field = code, field


CHECKPOINT_TOOL = {'type':'function','function':{
    'name':'context_checkpoint',
    'description':'Save ONE concise project memory after context_inspect. sources contains evidence IDs from recent_evidence, never claim IDs. To update a memory use its active claims[].id in replaces; omit replaces for a new memory. After any error call context_inspect before retrying. A verified claim must describe exactly what tool evidence proves. Do not store secrets.',
    'parameters':{'type':'object','additionalProperties':False,'properties':{
        'kind':{'type':'string','enum':list(CHECKPOINT_KINDS),'description':'Use the exact English category. For a user choice use decision; for a requested goal use objective.'},
        'text':{'type':'string','minLength':1,'maxLength':3000,'description':'One nonblank concise statement, not a JSON object.'},
        'sources':{'type':'array','minItems':1,'maxItems':25,'uniqueItems':True,'items':{'type':'string','pattern':'^[a-f0-9]{20}$'},'description':'Copy evidence IDs from context_inspect.recent_evidence.'},
        'replaces':{'type':'string','pattern':'^[a-f0-9]{12}$','description':'Optional active claim ID from context_inspect.claims. Omit for a new claim; never invent an ID.'}},
        'required':['kind','text','sources']}}}


def checkpoint_call(agent, args):
    """Enforce inspection after failed writes, even for models ignoring the schema."""
    import checkpoint_recovery as recovery
    state=getattr(agent,'checkpoint_recovery',None)
    if state and not state['active']:state=None
    if state and state['suspended']:
        return json.dumps({'ok':False,'blocked':True,'code':'checkpoint_suspended','message':'Consolidamento sospeso per questo turno. La cronologia è conservata; completa la risposta senza altri checkpoint.'})
    try:
        if agent.context_needs_inspection:
            raise CheckpointError('inspection_required',None,'Chiama context_inspect prima di salvare o riprovare. Non ripetere context_checkpoint con ID ipotizzati.')
        if not isinstance(args,dict):
            raise CheckpointError('invalid_arguments',None,'Gli argomenti devono essere un oggetto JSON.')
        missing=set(('kind','text','sources'))-args.keys()
        extra=args.keys()-set(('kind','text','sources','replaces'))
        if missing or extra:
            raise CheckpointError('invalid_fields',None,f'Campi mancanti: {sorted(missing)}; campi non ammessi: {sorted(extra)}.')
        args,repairs=recovery.sources(agent,args)
        key=json.dumps(args,sort_keys=True,ensure_ascii=False)
        if state and key in state['saved']:
            state['duplicates']+=1
            if state['duplicates']>=2:state['suspended']=True
            return json.dumps({'ok':True,'reused':True,'claim_id':state['saved'][key],'kind':args['kind']},ensure_ascii=False)
        ident=agent.memory.operational.checkpoint(args['kind'],args['text'],args['sources'],args.get('replaces'))
        if state:state['saved'][key]=ident
        return json.dumps({'ok':True,'claim_id':ident,'kind':args['kind'],'source_id_repairs':repairs},ensure_ascii=False)
    except ValueError as exc:
        agent.context_needs_inspection=True
        agent.context_recovery_pending=True
        recovery.failed(agent)
        return 'Errore: '+json.dumps({'ok':False,'code':getattr(exc,'code','invalid_checkpoint'),
            'field':getattr(exc,'field',None),'message':str(exc),'next_tool':'context_inspect',
            'recovery':'Leggi recent_evidence per sources e claims per replaces. Correggi il campo indicato; ometti replaces per una nuova memoria.',
            'allowed_kinds':list(CHECKPOINT_KINDS)},ensure_ascii=False)


def now():
    return datetime.now(timezone.utc).isoformat()

def bounded_inspection(value,budget=24000):
    """Keep valid structured IDs instead of triggering raw JSON preview truncation."""
    value=dict(value);value['claims']=[{**c,'text':str(c.get('text',''))[:800], **({'text_truncated':True} if len(str(c.get('text','')))>800 else {})} for c in value.get('claims',[])]
    value['recent_evidence']=[{**e,'summary':str(e.get('summary',''))[:350], 'summary_excerpt':True} for e in value.get('recent_evidence',[])]
    omitted={'claims':0,'evidence':0}
    if len(json.dumps(value.get('current_native_account_state',{}),ensure_ascii=False))>budget//3:
        state=value.pop('current_native_account_state');value['current_native_account_summary']={k:state.get(k) for k in ('profile','network','account_mode','collateral_usdc','available_usdc','retrieved_ms')}
    while len(json.dumps(value,ensure_ascii=False))>budget:
        if value['claims']:value['claims'].pop(0);omitted['claims']+=1
        elif len(value['recent_evidence'])>1:value['recent_evidence'].pop(0);omitted['evidence']+=1
        elif 'current_native_account_state' in value:
            state=value.pop('current_native_account_state');value['current_native_account_summary']={k:state.get(k) for k in ('profile','network','account_mode','collateral_usdc','available_usdc','retrieved_ms')}
        elif value.get('excluded'):value['excluded']=[];value['excluded_paths_omitted']=True
        else:break
    value['inspection_omitted']=omitted
    if 'pagination' in value:
        page=dict(value['pagination']);next_args={}
        for key,rows in (('claims','claims'),('evidence','recent_evidence')):
            end=page[key+'_offset']+len(value[rows])
            page[key+'_next_offset']=end if end<page[key+'_total'] else None
            next_args[key+'_offset']=end if end<page[key+'_total'] else page[key+'_offset']
        if any(page[k+'_next_offset'] is not None for k in ('claims','evidence')):
            page['next_tool']='context_inspect';page['next_arguments']={**next_args,'limit':page['limit']}
        value['pagination']=page
    return value


class ProjectContext:
    def __init__(self, vault):
        self.v = vault
        self.folder = vault.path / 'Projects' / vault.project_id
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder/'Evidence').mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.last = None
        self._inspection_cache=None
        self._legacy_owners={}
        self.inspection_metrics={'index_builds':0,'cache_hits':0,'legacy_headers_read':0}

    def load(self):
        path = self.folder/'context.json'
        if not path.exists(): return {'events':{}, 'claims':[], 'excluded':[], 'revision':0}
        return json.loads(path.read_text(encoding='utf-8'))

    def persist(self, data):
        self.v.atomic(self.folder/'context.json', json.dumps(data, ensure_ascii=False, indent=2))
        events = [e for e in data['events'].values() if e['path'] not in data['excluded']]
        requests = [e for e in events if e['kind']=='requested' and e['actor']=='main']
        text = '# Stato operativo · '+self.v.project.name+'\n\nAggiornato UTC: '+now()+'\n'
        text += '\n## Ultima richiesta (non prova di completamento)\n'+(requests[-1]['summary'] if requests else 'Non disponibile')+'\n'
        text += '\n## Scheda corrente\n'
        for c in reversed(data['claims'][-40:]):
            if c.get('superseded_by'): continue
            if any(data['events'].get(s,{}).get('path') in data['excluded'] for s in c['sources']): continue
            refs=[f"[[{data['events'][s]['path'][:-3]}]]" if s in data['events'] else s for s in c['sources']]
            text += f"- [{c['kind']}] {c['text']} (id: {c['id']}; {c['date']}; {c['authority']}; fonte: {', '.join(refs)})\n"
        text += '\n## Ultime evidenze osservate\n'
        for e in events[-12:]:
            text += f"- [{e['kind']}] {e['summary'][:350]} · [[{e['path'][:-3]}]] · {e['date']} · {e['actor']}\n"
        text += '\nUn comando riuscito prova solo quella operazione. Build, installazione, avvio e test funzionale sono verifiche distinte.\n'
        self.v.atomic(self.folder/'State.md', text)
        from brain_memory import consolidate
        consolidate(self.v)

    def ingest(self, messages, session, actor='main'):
        from memory_vault import redact
        with self.lock:
            data = self.load(); changed = False
            calls = {c['id']:c.get('function',{}) for m in messages for c in m.get('tool_calls', [])}
            for m in messages:
                role = m.get('role')
                if role not in ('user','tool'): continue
                raw = str(m.get('content',''))
                call = calls.get(m.get('tool_call_id'), {})
                tool = call.get('name','')
                if tool in ('context_checkpoint','context_inspect','memory_search','memory_read','vault_read','vault_search','agent_result','agent_status','agent_wait'): continue
                ident = hashlib.sha256((str(session)+json.dumps(m,sort_keys=True,ensure_ascii=False)).encode()).hexdigest()[:20]
                if ident in data['events']: continue
                kind = 'requested' if role=='user' else 'observed'
                if role=='tool' and raw.startswith(('Azione rinviata:', 'Azione non eseguita:')): kind='deferred'
                if role=='tool' and re.search(r'Errore:|non autorizzat|negat[oa]|interrott|timeout|Exit code: [1-9]',raw,re.I): kind='failed_or_denied'
                if tool=='ask_user':
                    try:
                        answer=json.loads(raw)
                        kind='decided' if not answer.get('cancelled') and 'answer' in answer else 'cancelled'
                    except ValueError: pass
                path = (self.folder/'Evidence'/f'{ident}.md').relative_to(self.v.path).as_posix()
                date=now()
                self.v.atomic(self.v.note_path(path), redact(f'# Evidenza {ident}\n\nData UTC: {date}\nTipo: {kind}\nAttore: {actor}\nSessione: {session}\nStrumento: {tool}\nArgomenti: {call.get("arguments", "")}\n\n{raw}\n'))
                from pathlib import Path
                data['events'][ident]={'id':ident,'kind':kind,'summary':redact((tool+': ' if tool else '')+raw)[:900], 'path':path,'date':date,'actor':actor,'tool':tool,'session':Path(str(session)).stem}
                changed=True
            if changed:
                data['revision']+=1; self.persist(data)

    def checkpoint(self, kind, text, sources=(), replaces=None, user=False):
        from memory_vault import redact
        if not isinstance(kind,str) or kind not in CHECKPOINT_KINDS:
            raise CheckpointError('invalid_kind','kind','Categoria non ammessa. Usa uno di: '+', '.join(CHECKPOINT_KINDS)+'.')
        if not isinstance(text,str) or not text.strip():
            raise CheckpointError('invalid_text','text','Il testo deve essere una stringa non vuota, non un oggetto o una lista.')
        if len(text)>3000:
            raise CheckpointError('text_too_long','text',f'Testo di {len(text)} caratteri: riducilo a massimo 3000.')
        if not user and (not isinstance(sources,list) or not 1<=len(sources)<=25 or
                any(not isinstance(s,str) or not re.fullmatch(r'[a-f0-9]{20}',s) for s in sources) or len(set(sources))!=len(sources)):
            raise CheckpointError('invalid_sources','sources','Usa una lista di 1–25 ID evidenza distinti (20 caratteri esadecimali) da recent_evidence, non ID memoria.')
        if replaces is not None and (not isinstance(replaces,str) or not re.fullmatch(r'[a-f0-9]{12}',replaces)):
            raise CheckpointError('invalid_replaces','replaces','Copia un ID memoria attivo (12 caratteri esadecimali) da claims oppure ometti replaces per creare una memoria.')
        with self.lock:
            data=self.load()
            if not user and (not sources or any(s not in data['events'] for s in sources)):
                raise CheckpointError('unknown_sources','sources','ID evidenza inesistenti: '+', '.join(s for s in sources if s not in data['events'])+'. Consulta context_inspect.')
            if not user and any(data['events'][s]['path'] in data['excluded'] for s in sources):
                raise CheckpointError('excluded_sources','sources','Una fonte esclusa non può essere usata per creare nuove memorie.')
            if not user and kind=='verified':
                if not any(data['events'][s]['kind']=='observed' and data['events'][s]['tool'] not in ('set_plan','agent_result','memory_read','memory_search') for s in sources):
                    raise CheckpointError('verification_missing','sources','Una verifica richiede risultati osservati degli strumenti, non una richiesta o un riepilogo. Usa objective/decision per richieste o scelte, oppure raccogli evidenze reali.')
            old=next((c for c in data['claims'] if c['id']==replaces),None)
            if replaces and (not old or old.get('superseded_by')):
                raise CheckpointError('inactive_replacement','replaces','Memoria da sostituire non attiva. Consulta claims in context_inspect per gli ID attivi; ometti replaces se questa è una nuova memoria.')
            ident=hashlib.sha256((now()+text).encode()).hexdigest()[:12]
            refs=['correzione esplicita utente'] if user else [s for s in sources]
            claim={'id':ident,'kind':kind,'text':redact(text),'sources':refs,'date':now(),
                   'authority':'user' if user else 'model interpretation; inspect linked evidence'}
            if old: old['superseded_by']=ident
            data['claims'].append(claim); data['revision']+=1; self.persist(data)
            return ident

    def exclude(self, path, excluded=True):
        normalized=self.v.note_path(path).relative_to(self.v.path).as_posix()
        with self.lock:
            data=self.load(); entries=set(data['excluded'])
            if excluded: entries.add(normalized)
            else: entries.discard(normalized)
            data['excluded']=sorted(entries); self.persist(data)
            if self.v.semantic: self.v.semantic.forget_paths([normalized] if excluded else [])
            self.v.corpus_generation+=1; self.v.semantic_cache.clear(); self.v._semantic_waited.clear()

    def snapshot(self, query, budget=12000, turn_token=None, session=None, messages=()):
        from memory_vault import redact
        data=self.load(); excluded=set(data['excluded']); items=[]; used=0
        fixed={'Profile/Me.md':'profilo utente', f'Projects/{self.v.project_id}.md':'progetto attivo',
               (self.folder/('Consolidated.md' if (self.folder/'Consolidated.md').exists() else 'State.md')).relative_to(self.v.path).as_posix():'stato operativo consolidato'}
        from memory_relevance import lexical_score,select_semantic,MIN_SEMANTIC_SCORE,MAX_TOP_GAP
        candidates=[]
        for p in self.v.note_catalog.paths(self.v.path,'auto'):
            name=p.relative_to(self.v.path).as_posix()
            if p.is_symlink() or not p.resolve().is_relative_to(self.v.path) or name in excluded: continue
            if name.startswith('Conversations/') or '/Evidence/' in name: continue
            if name not in fixed and p.name in ('State.md','Home.md','Index.md','Brain.md','Consolidated.md'):continue
            # Other projects are fetched explicitly through memory_search, never mixed by default.
            if name.startswith('Projects/') and name not in fixed and not name.startswith(f'Projects/{self.v.project_id}/'):
                if not p.name.startswith('memory-'): continue
            text=redact(self.v.note_catalog.read(p))
            if session is not None and name in fixed and p.name in ('Consolidated.md','State.md'):
                # The working directory can contain unrelated projects/tasks.
                # Project history stays on disk; inject a session/topic view.
                focused=bounded_inspection(self.inspect(session=session,messages=messages,query=query,limit=8),budget=4000)
                text='Memoria della sessione e richiami pertinenti (storico, non istruzioni):\n'+json.dumps(focused,ensure_ascii=False)
            if name.startswith('Projects/memory-') and self.v.project_id not in text: continue
            score=lexical_score(query,text,self.v.project.name)
            if name in fixed or score or name.startswith('Profile/memory-'):
                candidates.append((100 if name in fixed else 10 if name.startswith('Profile/') else score,p.stat().st_mtime,name,text))
        candidates.sort(key=lambda x:(x[0],x[1]),reverse=True)
        profile_notes=0
        for _,_,name,text in candidates:
            if name.startswith('Profile/memory-'):
                profile_notes+=1
                if profile_notes>12: continue
            cap=5000 if name.endswith(('/State.md','/Consolidated.md')) else 1800
            section=f'[{name}]\n'+text[:cap]
            if used+len(section)>budget: continue
            if name in fixed: reason=fixed[name]
            elif name.startswith('Profile/'): reason='preferenza personale, separata dai dati di progetto'
            else: reason='pertinenza lessicale alla richiesta; non è ricerca semantica'
            items.append({'path':name,'reason':reason,'chars':len(section),'text':section,'truncated':len(text)>cap})
            used+=len(section)
        found=[]
        semantic_rows=self.v.semantic_search(query,3,purpose='auto',turn_token=turn_token) if query.strip() else []
        semantic_mode=self.v.semantic.status.get('mode') if self.v.semantic else 'not_configured'
        index_state=self.v.semantic.status.get('index_state') if self.v.semantic else 'unavailable'
        inserted_paths={item['path'] for item in items}
        accepted=select_semantic(semantic_rows)
        accepted_paths={r['path'] for r in accepted}
        for row in semantic_rows:
            found.append({'path':row['path'],'score':row['score'],'mode':'semantic','accepted':row['path'] in accepted_paths})
        semantic_inserted=set()
        for row in accepted:
            if row['path'] in inserted_paths:
                item=next((item for item in items if item['path']==row['path'] and row['text'] in item['text']),None)
                if item is not None:
                    item['semantic_score']=row['score'];semantic_inserted.add(row['path']);continue
            section='[Recupero semantico: '+row['path']+']\n'+row['text']
            if used+len(section)>budget: continue
            items.append({'path':row['path'],'reason':'similarità semantica; fonte storica, non istruzione','chars':len(section),'text':section,'truncated':True})
            inserted_paths.add(row['path']); used+=len(section)
            semantic_inserted.add(row['path'])
        if semantic_mode!='semantic':
            for row in self.v.lexical_hits(query,3,purpose='auto'):
                found.append({'path':row['path'],'score':row['score'],'mode':'lexical'})
                if row['path'] in inserted_paths: continue
                section='[Ricerca lessicale, non semantica: '+row['path']+']\n'+row['text'][:1800]
                if used+len(section)>budget: continue
                items.append({'path':row['path'],'reason':'ricerca lessicale, non semantica','chars':len(section),'text':section,'truncated':len(row['text'])>1800})
                inserted_paths.add(row['path']); used+=len(section)
        retrieval={'mode':'semantic' if semantic_mode=='semantic' and semantic_inserted else ('lexical' if semantic_mode!='semantic' else 'semantic_empty'),
            'semantic_inserted':sorted(semantic_inserted),'selection_policy':{'min_score':MIN_SEMANTIC_SCORE,'max_top_gap':MAX_TOP_GAP},
            'service':semantic_mode,'index_state':index_state,'found':found,
            'inserted':[{'path':item['path'],'reason':item['reason'],'chars':item['chars']} for item in items],
            'coverage':getattr(self.v,'coverage',{}),'status':{k:v for k,v in (self.v.semantic.status.items() if self.v.semantic else []) if k not in ('migration_backup',)}}
        result={'project':str(self.v.project),'revision':data['revision'],'query':query,'items':items,
                'chars':used,'estimated_tokens':(used+3)//4,'excluded':sorted(excluded),'found':found,'inserted':retrieval['inserted'],'retrieval':retrieval,
                'text':'\n\n'.join(i['text'] for i in items)}
        return result

    def _inspection_index(self):
        """Revision-aware derived index; Markdown and context.json stay authoritative."""
        from pathlib import Path
        path=self.folder/'context.json'
        stat=path.stat() if path.exists() else None
        signature=(stat.st_mtime_ns,stat.st_size) if stat else None
        if self._inspection_cache and self._inspection_cache[0]==signature:
            self.inspection_metrics['cache_hits']+=1;return self._inspection_cache[1:]
        data=self.load();owners={};topics={}
        from memory_relevance import content_tokens
        for index,claim in enumerate(data['claims']):
            if claim.get('superseded_by'):continue
            for word in content_tokens(claim['text']):topics.setdefault(word,set()).add(index)
        for event in data['events'].values():
            stored=event.get('session')
            if stored is None:
                # Legacy evidence records carry ownership in the immutable MD header.
                # Read each old header once per process, not once per prompt rebuild.
                if event['id'] not in self._legacy_owners:
                    note=self.v.note_path(event['path']);stored=None
                    if note.is_file():
                        with note.open(encoding='utf-8') as stream:header=stream.read(1800)
                        match=re.search(r'^Sessione:\s*(.+)$',header,re.M);stored=match[1].strip() if match else None
                        self.inspection_metrics['legacy_headers_read']+=1
                    self._legacy_owners[event['id']]=stored
                stored=self._legacy_owners[event['id']]
            if stored is not None:owners.setdefault(Path(str(stored)).stem,set()).add(event['id'])
        self._inspection_cache=(signature,data,owners,topics);self.inspection_metrics['index_builds']+=1
        return data,owners,topics

    def inspect(self,session=None,messages=(),query='',claims_offset=0,evidence_offset=0,limit=None):
        data,owners,topics=self._inspection_index()
        claims=[c for c in data['claims'] if not c.get('superseded_by') and not any(data['events'].get(s,{}).get('path') in data['excluded'] for s in c['sources'])]
        events=[e for e in data['events'].values() if e['path'] not in data['excluded']]
        if session is None and limit is None:return {'claims':claims,'recent_evidence':events[-25:],'excluded':data['excluded'],'project':str(self.v.project)}
        from pathlib import Path
        from memory_relevance import lexical_score
        from web_search import query_terms
        possible=set().union(*(topics.get(word,set()) for word in query_terms(query))) if query.strip() else set()
        historical={data['claims'][i]['id'] for i in possible if lexical_score(query,data['claims'][i]['text'],self.v.project.name)>0}
        ids={hashlib.sha256((str(session)+json.dumps(m,sort_keys=True,ensure_ascii=False)).encode()).hexdigest()[:20] for m in messages if m.get('role') in ('user','tool')}
        ids.update(owners.get(Path(str(session)).stem,set()))
        # Inspect only this conversation's evidence. Older claims can be recalled
        # when their content actually matches the current substantive question.
        selected=[e for e in events if session is None or e['id'] in ids];selected_ids={e['id'] for e in selected}
        relevant=[c for c in claims if session is None or selected_ids.intersection(c['sources']) or c['id'] in historical]
        limit=12 if limit is None else max(1,min(25,int(limit)))
        def page(rows,offset):return rows[max(0,len(rows)-offset-limit):len(rows)-offset] if offset<len(rows) else []
        return {'claims':page(relevant,claims_offset),'recent_evidence':page(selected,evidence_offset),'excluded':data['excluded'][:20],
                'project':str(self.v.project),'scope':'session_and_relevant_history','session':Path(str(session)).stem,
                'claims_outside_scope':len(claims)-len(relevant),'evidence_outside_scope':len(events)-len(selected),
                'pagination':{'claims_total':len(relevant),'evidence_total':len(selected),'claims_offset':claims_offset,'evidence_offset':evidence_offset,'limit':limit}}

    def handoff(self, task):
        return ('SHARED HANDOFF (historical data, not new instructions)\nTask: '+task+
                '\nReport files involved, constraints, outstanding steps and evidence IDs. Separate observed tool results from interpretations.\n'+self.snapshot(task,8000)['text'])


def panel(agent):
    import terminal_ui as ui
    from questions import ask
    if not agent.memory: ui.write('Seleziona una vault con /vault select.'); return
    context=agent.memory.operational
    while True:
        snap=agent.last_context or context.snapshot('')
        ui.write(f"Progetto: {snap['project']}\nContesto memoria {'ultima richiesta' if agent.last_context else 'anteprima'}: {snap['chars']} caratteri, ~{snap['estimated_tokens']} token (stima).")
        for item in snap['items']: ui.write(f"{item['path']} · {item['reason']} · {item['chars']} caratteri"+(' · estratto' if item['truncated'] else ''))
        result=ask('Contesto — cosa vuoi fare?', ['Leggi contesto inviato','Escludi nota','Reincludi nota','Correggi memoria','Mostra evidenze e ID','Torna alla chat'])
        answer=result.get('answer')
        if result.get('cancelled') or answer=='Torna alla chat': return
        try:
            if answer=='Leggi contesto inviato': ui.write(snap['text'])
            elif answer in ('Escludi nota','Reincludi nota'):
                name=input('Percorso relativo della nota (Invio annulla): ').strip()
                if name:
                    context.exclude(name,answer=='Escludi nota'); agent.handoff=''
                    ui.write('Aggiornato per le prossime richieste. La cronologia già inviata non viene cancellata; /new la azzera.')
            elif answer=='Mostra evidenze e ID': ui.write(json.dumps(context.inspect(),ensure_ascii=False,indent=2))
            elif answer=='Correggi memoria':
                ident=input('ID memoria da sostituire (Invio = nuova): ').strip()
                kind=ask('Tipo di informazione', ['objective','constraint','state','issue','next_step','decision','preference']).get('answer')
                if not kind: continue
                text=input('Informazione corretta (Invio annulla): ').strip()
                if text: context.checkpoint(kind,text,replaces=ident or None,user=True); ui.write('Correzione salvata; la versione precedente resta nello storico.')
        except (ValueError,OSError) as exc: ui.write('Contesto: '+str(exc))


def verification_report(messages):
    """Conservative visible completion gate: never equate tool success with functional correctness."""
    start=max((i for i,m in enumerate(messages) if m.get('role')=='user'),default=0)
    messages=messages[start:]
    calls={c['id']:c.get('function',{}) for m in messages for c in m.get('tool_calls',[])}
    touched=False; checks=[]
    for m in messages:
        if m.get('role')!='tool': continue
        call=calls.get(m.get('tool_call_id'),{}); name=call.get('name'); value=str(m.get('content',''))
        if value.startswith(('Azione rinviata:', 'Azione non eseguita:')): continue
        if name in ('write_file','edit_file','shell','seeker_install','seeker_launch','seeker_stop'): touched=True
        if name in ('shell','seeker_install','seeker_launch'):
            checks.append(f"{name}: {value.splitlines()[0][:180] if value else 'nessun esito'}")
    if not touched: return ''
    return ('Verifica operativa (risultati degli strumenti, non certificazione funzionale):\n'+
            '\n'.join(checks[-8:])+'\nLa correttezza funzionale richiede test pertinenti; build, installazione e avvio sono esiti separati.')
