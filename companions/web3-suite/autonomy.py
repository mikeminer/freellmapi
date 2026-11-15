"""Shared per-task budget and duplicate-action circuit breaker."""
import hashlib
import json
import threading
import time

class BudgetExceeded(RuntimeError): pass

class Budget:
    def __init__(self,seconds=1800,tokens=250000,steps=120,repeats=3):
        self.seconds=seconds; self.token_limit=tokens; self.step_limit=steps; self.repeat_limit=repeats
        self.started=time.monotonic(); self.tokens=0; self.steps=0; self.unknown_usage=0
        self.actions={}; self.lock=threading.RLock()

    def check(self):
        with self.lock:
            if self.seconds and time.monotonic()-self.started>=self.seconds: raise BudgetExceeded('Budget di tempo esaurito.')
            if self.token_limit and self.tokens>=self.token_limit: raise BudgetExceeded('Budget token esaurito.')
            if self.step_limit and self.steps>=self.step_limit: raise BudgetExceeded('Budget di richieste al modello esaurito.')

    def request(self):
        with self.lock: self.check(); self.steps+=1

    def remaining_seconds(self):
        if not self.seconds: return float('inf')
        remaining=self.seconds-(time.monotonic()-self.started)
        if remaining<=0: raise BudgetExceeded('Budget di tempo esaurito.')
        return remaining

    def account(self,response):
        with self.lock:
            usage=response.get('usage') or {}
            total=usage.get('total_tokens')
            if total is None and 'prompt_tokens' in usage and 'completion_tokens' in usage:
                total=usage['prompt_tokens']+usage['completion_tokens']
            if isinstance(total,int) and total>=0: self.tokens+=total
            else: self.unknown_usage+=1

    def action(self,name,args):
        with self.lock:
            # Repeated observations and debugger steps are normal; repeated mutations are not.
            guarded={'shell','write_file','edit_file','project_copy','android_build','seeker_install','seeker_prepare_qa','seeker_playtest','blender_run','hyperliquid_preview','hyperliquid_panel','agent_start','skill_install','browser_click',
                     'coordinator_delegate','repair_start','repair_edit','repair_verify','repair_activate','generated_tool_build',
                     'strategy_propose','strategy_evaluate','strategy_activate','repair_dependencies','repair_migrate_state','repair_add_module',
                     'desktop_click','desktop_type','desktop_hotkey','debug_start','sandbox_run','workspace_checkpoint','workspace_revert','worktree_create',
                     'process_start','schedule_create','notebook_edit','file_rewind','task_create'}
            if name not in guarded: return
            if name in ('shell','process_start'):
                from qa_execution import action_arguments
                args=action_arguments(args)
            key=hashlib.sha256((name+json.dumps(args,sort_keys=True,ensure_ascii=False)).encode()).hexdigest()
            count=self.actions.get(key,0)+1
            if count>self.repeat_limit: raise BudgetExceeded(f'Azione identica ripetuta {count} volte: {name}. Turno fermato; verifica lo stato prima di riprovare.')
            self.actions[key]=count

    def report(self):
        return {'elapsed_seconds':round(time.monotonic()-self.started,1),'limit_seconds':self.seconds,
                'reported_tokens':self.tokens,'limit_tokens':self.token_limit,'requests':self.steps,
                'limit_requests':self.step_limit,'responses_without_usage':self.unknown_usage,
                'note':'Limite 0 = illimitato localmente; quote provider indipendenti. Token conteggiati solo se comunicati dal provider. Restano timeout operativi e controlli anti-loop.'}
