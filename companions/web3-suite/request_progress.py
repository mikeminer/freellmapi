"""Live request telemetry: measured time/activity, provider-reported token counts."""
import threading
import time
from contextlib import contextmanager
import terminal_ui as ui


class Progress:
    def __init__(self, label, clock=time.monotonic):
        self.label=label; self.clock=clock; self.started=clock()
        self.last_activity=None; self.events=0; self.usage={}; self.actual=None
        self.lock=threading.RLock()

    def model(self,value):
        if value and not value.startswith(('auto','fusion')):
            with self.lock: self.actual=value

    def activity(self,usage=None):
        with self.lock:
            self.last_activity=self.clock(); self.events+=1
            if isinstance(usage,dict):
                for key in ('prompt_tokens','completion_tokens','total_tokens','input_tokens','output_tokens'):
                    value=usage.get(key)
                    if isinstance(value,int) and not isinstance(value,bool) and value>=0: self.usage[key]=value

    def render(self):
        with self.lock:
            now=self.clock(); elapsed=now-self.started
            incoming=self.usage.get('prompt_tokens',self.usage.get('input_tokens'))
            outgoing=self.usage.get('completion_tokens',self.usage.get('output_tokens'))
            total=self.usage.get('total_tokens')
            if total is None and incoming is not None and outgoing is not None: total=incoming+outgoing
            tokens=(f'token in/out: {incoming if incoming is not None else "?"}/{outgoing if outgoing is not None else "?"} · totale: {total if total is not None else "?"}'
                    if self.usage else 'token: in attesa del server')
            silence=now-(self.last_activity if self.last_activity is not None else self.started)
            activity=(f'ultimo dato {silence:.1f}s fa · eventi: {self.events}' if self.last_activity is not None else 'in attesa del primo dato')
            if silence>=20: activity+=f' · nessun nuovo dato da {silence:.0f}s (blocco non accertato)'
            return f'{self.label}'+(f' · modello effettivo: {self.actual}' if self.actual else '')+f' · {elapsed:.1f}s · {tokens} · {activity}'

    def snapshot(self):
        """Only measured telemetry; missing usage is never estimated as tokens."""
        with self.lock:
            now=self.clock()
            return {'actual_model':self.actual,'elapsed_seconds':max(0,now-self.started),
                    'silence_seconds':max(0,now-(self.last_activity if self.last_activity is not None else self.started)),
                    'events':self.events,'usage':dict(self.usage)}


@contextmanager
def live_progress(label):
    progress=Progress(label)
    if ui.fancy():
        from rich.live import Live
        from rich.spinner import Spinner
        from rich.text import Text
        spinner=Spinner('dots',style='bold #60d5ff')
        def render():
            spinner.update(text=Text(progress.render(),style='#b7a1ff'))
            return spinner
        with Live(get_renderable=render,console=ui.console,refresh_per_second=4,transient=True):
            yield progress
    else:
        stop=threading.Event()
        log=getattr(ui._thread,'log',None)
        ui.write(progress.render())
        def tick():
            ui.set_background(log)
            while not stop.wait(5): ui.write(progress.render())
        worker=threading.Thread(target=tick,daemon=True,name='request-telemetry'); worker.start()
        try: yield progress
        finally:
            stop.set(); worker.join(timeout=1)
