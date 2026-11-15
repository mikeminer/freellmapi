"""Parallel model workers with serialized foreground tool approvals."""
import json
import queue
import threading
import time
import uuid
import re
from difflib import SequenceMatcher
import terminal_ui as ui

class Jobs:
    def __init__(self,parent,limit=3):
        self.parent=parent; self.limit=limit; self.jobs={}; self.pending=queue.Queue(); self.lock=threading.RLock()
        self.home=parent.session.parent/'jobs'; self.home.mkdir(parents=True,exist_ok=True)
        self.changed=threading.Event(); self.last_report=None; self.delivered=set()
        self.live_results={}
    def persist(self,j):
        with self.lock:
            data={k:v for k,v in j.items() if k not in ('cancel','thread','agent')}
            p=self.home/(j['id']+'.json'); tmp=p.with_suffix('.tmp')
            tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8'); tmp.replace(p)
            self.changed.set()
    def summary(self):
        return f"{sum(j['state'] in ('running','waiting','executing') for j in self.jobs.values())} attivi / {sum(j['state']=='waiting' for j in self.jobs.values())} attesa / {sum(j['state']=='completed' for j in self.jobs.values())} completati / {sum(j['state']=='failed' for j in self.jobs.values())} errori (/jobs)"
    def start(self,task,model=None,allow_restart=False,isolated=False):
        key=' '.join(re.findall(r'\w+',task.casefold()))
        if not key: raise ValueError('Compito vuoto.')
        if not allow_restart:
            for existing in self.jobs.values():
                previous=existing.get('task_key','')
                if key==previous or (len(key)>40 and SequenceMatcher(None,key,previous).ratio()>=.9):
                    if existing['state']=='cancelled':
                        raise ValueError(f"Compito già annullato ({existing['id']}). Non ricrearlo automaticamente. Per rilanciarlo esplicitamente usa /agentbg compito.")
                    return existing['id']
        if sum(j.get('thread') is not None and j['thread'].is_alive() for j in self.jobs.values())>=self.limit: raise RuntimeError('Massimo 3 subagenti simultanei (incluse richieste in cancellazione).')
        ident=uuid.uuid4().hex[:8]
        workspace=None
        if isolated:
            import workspaces
            if not self.parent.approve('Creare un worktree Git separato per il subagente: '+task): raise ValueError('Worktree negato.')
            workspace=workspaces.create(self.parent.root,self.parent.extensions.home)
        child=type(self.parent)(workspace['path'] if workspace else self.parent.root,self.parent.base,self.parent.token,model or self.parent.model,False)
        child.budget=self.parent.budget
        child.cooldowns=self.parent.cooldowns
        child.server_router=self.parent.server_router
        child.request_timeout=self.parent.request_timeout; child.stream_total_timeout=self.parent.stream_total_timeout
        child.policy=self.parent.policy; child.processes=self.parent.process_manager()
        child.depth=1; child.mode=self.parent.mode; child.memory=self.parent.memory; child.vault_root=self.parent.vault_root
        child.handoff=task if self.parent.memory else ''
        child.context_limit=self.parent.context_limit; child.active_skills=dict(self.parent.active_skills)
        child.extensions=self.parent.extensions; child.session=self.home/(ident+'-session.json')
        j={'id':ident,'task':task,'task_key':key,'model':child.model,'state':'running','activity':'avvio','result':'','workspace':workspace,'inbox':[],'delivered_messages':0,
           'cancel':threading.Event(),'agent':child,'log':str(self.home/(ident+'.log'))}
        self.jobs[ident]=j; original=child.tool
        reads={'seeker_devices','seeker_packages','seeker_logs','seeker_crashes','context_inspect','read_file','list_files','search','memory_read','memory_search','vault_read','vault_search','skills_list','skills_search','skill_read_file','skill_load','set_plan'}
        def guarded(name,args):
            if j['cancel'].is_set(): raise RuntimeError('Subagente annullato.')
            j['activity']=name; self.persist(j)
            if name in reads: return original(name,args)
            if name in ('delegate','agent_start','agent_status','agent_result','agent_cancel','agent_wait','agent_message'): return 'Delega ricorsiva non consentita.'
            req={'job':j,'name':name,'args':args,'done':threading.Event(),'result':'Azione annullata.'}
            j['state']='waiting'; self.persist(j); self.pending.put(req)
            while not req['done'].wait(.2):
                if j['cancel'].is_set(): return 'Azione annullata.'
            if not j['cancel'].is_set(): j['state']='running'; self.persist(j)
            return req['result']
        child.tool=guarded
        child.approve=lambda description: child.mode!='plan' and self.parent.approve(f'Subagente {ident}\n'+description)
        api=child.api
        def checked_api(*args,**kwargs):
            if j['cancel'].is_set(): raise RuntimeError('Subagente annullato.')
            if len(args)>1 and isinstance(args[1],dict) and args[1].get('tools'):
                with self.lock:
                    inbox=list(j['inbox']); j['inbox'].clear(); j['delivered_messages']+=len(inbox)
                if inbox:
                    notes=[{'role':'user','content':'Coordinator guidance (subject to original user instructions):\n'+text} for text in inbox]
                    child.messages.extend(notes); child.save()
                    payload=dict(args[1]); payload['messages']=list(payload['messages'])+notes
                    args=(args[0],payload,*args[2:])
            j['activity']='richiesta al modello'; self.persist(j)
            result=api(*args,**kwargs)
            if j['cancel'].is_set(): raise RuntimeError('Subagente annullato.')
            return result
        child.api=checked_api
        def run():
            try:
                with open(j['log'],'a',encoding='utf-8') as log:
                    ui.set_background(log); child.turn(task)
                j['result']='\n'.join(m.get('content') or '' for m in child.messages if m['role']=='assistant')[-24000:]
                j['state']='cancelled' if j['cancel'].is_set() else 'completed' if child.turn_outcome in (None,'completed') else 'blocked'
            except Exception as e: j['result']=str(e); j['state']='cancelled' if j['cancel'].is_set() else 'failed'
            finally:
                child.debugger.close()
                child.language_server.close()
                ui.set_background(None); j['activity']='terminato'; self.persist(j)
        self.persist(j); t=threading.Thread(target=run,name='freellmapi-'+ident,daemon=True); j['thread']=t; t.start()
        return ident
    def message(self,ident,text):
        if not isinstance(text,str) or not text.strip() or len(text)>8000: raise ValueError('Messaggio vuoto o oltre 8000 caratteri.')
        with self.lock:
            job=self.jobs.get(ident)
            if not job or job['state'] not in ('running','waiting','executing'): raise ValueError('Subagente non attivo: consulta agent_status; non lo riavvio automaticamente.')
            if len(job['inbox'])>=10: raise ValueError('Coda messaggi piena. Attendi la prossima richiesta del subagente.')
            job['inbox'].append(text); self.persist(job)
        return {'queued':True,'id':ident,'delivery':'Prossima richiesta normale al modello; un lavoro già in conclusione potrebbe non riceverlo.'}
    def service(self):
        count=0
        while True:
            try: r=self.pending.get_nowait()
            except queue.Empty: break
            j=r['job']
            try:
                if j['cancel'].is_set(): continue
                j['state']='executing'; self.persist(j)
                r['result']=type(j['agent']).tool(j['agent'],r['name'],r['args'])
            except (Exception,KeyboardInterrupt) as e:
                from autonomy import BudgetExceeded
                if isinstance(e,BudgetExceeded):
                    self.close(); raise
                r['result']='Errore/azione interrotta: '+str(e)
            finally: r['done'].set(); count+=1
        return count
    def status_line(self,j):
        state=j['state']
        action=('In coda al coordinatore, NON bloccato: agent_wait gestisce la richiesta; /jobs approve per farlo manualmente.' if state=='waiting' else
                'Lavoro in corso: usa agent_wait, non annullare/ricreare.' if state in ('running','executing') else
                'Leggi agent_result; completato indica fine attività, non verifica funzionale.' if state=='completed' else 'Leggi agent_result; non rilanciare automaticamente.')
        workspace=j.get('workspace')
        location=('\n  Worktree: '+workspace['path']+' · ramo: '+workspace['branch']+' · modifiche NON integrate') if workspace else ''
        return f"{j['id']} | {state} | {j['activity']} | modello richiesto: {j['model']} | {j['task'][:100]}\n  {action}"+location

    def report(self,compact=False):
        text='\n'.join(self.status_line(j) for j in self.jobs.values()) or 'Nessun subagente.'
        if compact and text==self.last_report: return 'Nessun cambiamento. Usa agent_wait; non ripetere agent_status/agent_result e non annullare le attività in corso.'
        if compact: self.last_report=text
        return text
    def result(self,ident,compact=False):
        j=self.jobs.get(ident)
        if j:
            if j['state'] in ('running','waiting','executing'):
                signature=(j['state'],j['activity'])
                if compact and self.live_results.get(ident)==signature:
                    self.wait_for(ident,20)
                    if j['state'] not in ('running','waiting','executing'): return self.result(ident,compact)
                    return f'{ident}: {j["state"]}, {j["activity"]}. Nessun risultato finale; usa agent_wait. Non annullare/ricreare.'
                self.live_results[ident]=signature
                return self.status_line(j)
            if compact and ident in self.delivered: return f"{ident}: risultato già restituito. Stato: {j['state']}. Log: {j['log']}"
            evidence=[]
            if self.parent.memory:
                evidence=[e for e in self.parent.memory.operational.load()['events'].values() if e['actor']=='subagent:'+ident+'-session']
            if compact: self.delivered.add(ident)
            references=[{'id':e['id'],'kind':e['kind'],'tool':e['tool'],'path':e['path']} for e in evidence[-20:]]
            return f"{j['state']} (stato attività, non verifica funzionale)\n{j['result']}\nLog: {j['log']}\nWorktree: "+json.dumps(j.get('workspace'),ensure_ascii=False)+'\nEvidenze: '+json.dumps(references,ensure_ascii=False)
        if not ident.isalnum(): raise ValueError('ID non valido.')
        p=self.home/(ident+'.json'); return 'Archivio precedente (stato non live):\n'+p.read_text(encoding='utf-8') if p.exists() else 'ID sconosciuto.'
    def cancel(self,ident,by_model=False):
        if by_model and self.jobs[ident]['state'] in ('running','waiting','executing'):
            raise ValueError('Subagente attivo: annullamento automatico bloccato per evitare cicli annulla/ricrea. Usa agent_wait. Per annullarlo esplicitamente: /jobs cancel '+ident)
        j=self.jobs[ident]; j['cancel'].set(); j['state']='cancelled'; self.persist(j)

    def wait_for(self,ident,seconds=20):
        if ident not in self.jobs: raise ValueError('ID subagente sconosciuto.')
        deadline=time.monotonic()+max(0,min(float(seconds),30))
        while self.jobs[ident]['state'] in ('running','waiting','executing'):
            self.parent.budget.check()
            self.changed.clear()
            self.service()
            if self.jobs[ident]['state'] not in ('running','waiting','executing'): break
            remaining=deadline-time.monotonic()
            if remaining<=0: break
            self.changed.wait(min(remaining,.5))
        return self.status_line(self.jobs[ident])
    def wait(self):
        while any(j['state'] in ('running','waiting','executing') for j in self.jobs.values()):
            self.parent.budget.check(); self.service(); time.sleep(.1)
        return self.report()
    def close(self):
        for ident,j in self.jobs.items():
            if j['state'] in ('running','waiting','executing'): self.cancel(ident)
