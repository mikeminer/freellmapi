"""Measured, content-free per-turn timings. Nested phases are not additive."""
import functools
import threading
import time


class Timings:
    def __init__(self):
        self.started=time.perf_counter();self.lock=threading.RLock();self.phases={};self.active={};self.finished=None

    def snapshot(self):
        with self.lock:
            return {'elapsed_seconds':round((self.finished or time.perf_counter())-self.started,6),
                    'phases':{k:dict(v) for k,v in self.phases.items()},
                    'active':list(self.active), 'nested_phases_are_not_additive':True,
                    'context_projection':getattr(self,'context_projection',None)}


def render(value):
    if not value:return 'Nessuna misurazione disponibile per questa sessione.'
    rows=['Tempi ultimo turno · '+str(value.get('elapsed_seconds',0))+' s misurati']
    labels={'inference':'Modelli e recupero API','context_preparation':'Preparazione contesto','tools':'Strumenti','persistence':'Salvataggi','compaction':'Compattazione'}
    for key,row in sorted(value.get('phases',{}).items(),key=lambda item:item[1]['seconds'],reverse=True):
        rows.append(labels.get(key,key)+': '+str(row['seconds'])+' s · '+str(row['calls'])+' chiamate · massimo '+str(row['max_seconds'])+' s')
    projection=value.get('context_projection')
    if projection:rows.append('Ultimo payload analisi: '+str(projection['before_characters'])+' → '+str(projection['after_characters'])+' caratteri; storico completo conservato localmente.')
    return '\n'.join(rows)+'\nLe fasi annidate si sovrappongono: i tempi non vanno sommati. Nessun token stimato.'


def measured(name):
    def decorate(fn):
        @functools.wraps(fn)
        def call(self,*args,**kwargs):
            timer=getattr(self,'phase_metrics',None)
            if timer is None:return fn(self,*args,**kwargs)
            start=time.perf_counter()
            with timer.lock:timer.active[name]=timer.active.get(name,0)+1
            try:return fn(self,*args,**kwargs)
            finally:
                elapsed=time.perf_counter()-start
                with timer.lock:
                    timer.active[name]-=1
                    if not timer.active[name]:del timer.active[name]
                    row=timer.phases.setdefault(name,{'calls':0,'seconds':0,'max_seconds':0})
                    row['calls']+=1;row['seconds']=round(row['seconds']+elapsed,6);row['max_seconds']=round(max(row['max_seconds'],elapsed),6)
        return call
    return decorate
