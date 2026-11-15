"""Terminal presentation, with a plain-text fallback for pipes and missing Rich."""
import os
import sys
import threading
from model_health import chat_model,ready_model
from contextlib import contextmanager

try:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.text import Text
    console = Console(highlight=False, force_terminal=sys.stdout.isatty(), force_interactive=sys.stdout.isatty(), no_color=False,
                      color_system='truecolor' if sys.stdout.isatty() else None)
except ImportError:
    console = None

PLAIN = False
_thread=threading.local()
def set_background(log): _thread.log=log

def fancy():
    return console is not None and console.is_terminal and not PLAIN and not getattr(_thread,'log',None)

def plain_write(text,end=''):
    encoding=getattr(sys.stdout,'encoding',None) or 'utf-8'
    print(str(text).encode(encoding,errors='replace').decode(encoding),end=end,flush=True)

def write(text):
    from secret_output import redact
    text=redact(text)
    from remote_access import publish
    publish('output',text)
    log=getattr(_thread,'log',None)
    if log: log.write(str(text)+'\n'); log.flush(); return
    if not fancy():
        encoding = getattr(sys.stdout, 'encoding', None) or 'utf-8'
        print(text.encode(encoding, errors='replace').decode(encoding), flush=True); return
    if text.startswith('\nfreellmapi > '):
        console.print('\n[bold #60d5ff]✦ freellmapi[/]')
        console.print(Markdown(text[len('\nfreellmapi > '):].strip(), code_theme='monokai'))
        console.print(); return
    if text.startswith('  [strumento] '):
        console.print(Text('  ◇ ' + text.split('[strumento] ',1)[1], style='bold #b7a1ff')); return
    if text.startswith('--- ') and '\n+++ ' in text:
        for line in text.splitlines():
            style = '#79db9a' if line.startswith('+') else '#ff8787' if line.startswith('-') else '#60d5ff' if line.startswith('@@') else 'dim'
            console.print(Text(line, style=style))
        return
    style = '#ff8787' if text.startswith(('Errore:', 'freellmapi:')) else '#b4becb'
    console.print(Text(text, style=style))

def banner(root, count, status='Stato: inizializzazione'):
    if not fancy():
        write(f'\nfreellmapi · coding assistant\nProgetto: {root}\nModelli: {count}'); return
    body = Text()
    body.append('  ✦  freellmapi', style='bold #60d5ff')
    body.append('   /   coding assistant\n\n', style='#b7a1ff')
    body.append('  Il tuo terminale. Il tuo modello.\n', style='bold white')
    body.append(f'  {root}\n\n', style='dim')
    body.append(f'  ● {count} modelli', style='#79db9a')
    body.append('\n  '+status, style='#b7a1ff')
    console.print()
    console.print(Panel(body, border_style='#52657a', padding=(1,1), title='[dim]workspace[/]', title_align='right'))

def composer_status(agent):
    last=getattr(agent,'last_request',{}) or {}
    duration=last.get('seconds')
    timing=f' · ultima richiesta {duration:.1f}s' if isinstance(duration,(int,float)) else ''
    budget=agent.budget
    actual=getattr(agent,'last_successful_model',None)
    model=f' · ultimo modello: {actual}' if actual else ''
    return f'API: {agent.api_state}{timing} · {budget.tokens:,} token riportati · {budget.steps} richieste{model}'

def prompt(model, mode, status='',wake=None,on_mode=None):
    status_fn=status if callable(status) else lambda:status
    if fancy() and sys.stdin.isatty():
        try:
            from terminal_composer import prompt as composer_prompt
            return composer_prompt(model,mode,status_fn,on_mode,wake)
        except ImportError: pass
    status=status_fn()
    if not fancy(): return input('\ntu > ')
    console.print()
    if status: console.print(Text(status,style='dim #8795a8'))
    console.rule(Text(f' {model}  ·  {mode}  ·  /menu  ·  /help ', style='#8795a8'), style='#344152', align='left')
    return console.input('[bold #60d5ff]❯ [/]').strip()

def stream_text(text):
    from secret_output import StreamFilter
    if not hasattr(_thread,'stream_filter'):_thread.stream_filter=StreamFilter()
    text=_thread.stream_filter.feed(text)
    if not text:return
    from remote_access import publish
    publish('stream',text)
    if fancy(): console.print(Text(text),end='')
    else: plain_write(text)

def menu():
    entries = [('Modello','/model'),('Brain · progetti e memoria','/brain'),('Task persistenti','/tasks'),('Processi in background','/processes'),('Checkpoint file','/rewind'),('Pianificazioni','/schedules'),('Regole e hook','/policy'),('Diagnostica strumenti','/doctor'),('Coordinamento e riparazioni','/improve'),('Connessioni e plugin','/connections'),('Social · bozze e X','/social'),('Budget autonomia','/limits'),('Hyperliquid · wallet e trading','/hyperliquid'),('Dati mercato','/market '),('Debugger Node.js','/debug'),('Finestre Windows','/desktop'),('Diff Git','/diff'),('Apri in Chrome','/chrome '),('Esegui PowerShell','/run '),
               ('File del progetto','/files'),('Piano senza modifiche','/plan'),('Modalità operativa','/ask'),
               ('Scegli sessione salvata','/sessions'),('Compatta contesto','/compact'),('Stato','/status'),('Seeker · APK, log e crash','/seeker'),('Contesto e memoria','/context'),('Attiva esecuzione automatica','/auto on'),('Disattiva esecuzione automatica','/auto off'),('Nuova sessione','/new')]
    if not fancy() or os.name != 'nt':
        for i,(label,_) in enumerate(entries,1): write(f'{i}. {label}')
        choice=input('Numero (Invio annulla): ')
        command=entries[int(choice)-1][1] if choice.isdigit() and 1<=int(choice)<=len(entries) else ''
    else:
        import msvcrt
        from rich.live import Live
        selected=0; command=''
        def render():
            text=Text('↑ ↓ seleziona · Invio conferma · Esc annulla\n\n',style='dim')
            page=max(5,min(14,console.size.height-9)); start=max(0,min(selected-page//2,len(entries)-page))
            for i in range(start,min(start+page,len(entries))):
                label=entries[i][0]
                text.append(f"{'❯' if i==selected else ' '} {i+1}. {label}\n",style='bold #60d5ff' if i==selected else '#b4becb')
            text.append(f'\n{selected+1}/{len(entries)}',style='dim')
            return Panel(text,title='freellmapi · accesso rapido',border_style='#52657a')
        with Live(render(),console=console,auto_refresh=False,transient=True) as live:
            while True:
                key=msvcrt.getwch()
                if key in ('\x00','\xe0'):
                    key=msvcrt.getwch()
                    if key=='H': selected=(selected-1)%len(entries)
                    if key=='P': selected=(selected+1)%len(entries)
                elif key=='\r': command=entries[selected][1]; break
                elif key=='\x1b': break
                elif key=='\x03': raise KeyboardInterrupt
                elif key in '123456789': selected=int(key)-1
                live.update(render(),refresh=True)
    if command.endswith(' '):
        value=input('Percorso/URL: ' if command.startswith('/chrome') else 'Comando: ').strip()
        return command+value if value else ''
    return command

class ModelPicker:
    def __init__(self, catalog, current):
        self.current=current; self.query=''; self.ready=True; self.selected=0
        entries={m['id']:m for m in catalog if chat_model(m)}
        for name in ('auto','auto:fast','auto:smart','auto:reliable','auto:balanced'):
            entries.setdefault(name,{'id':name,'name':'Routing automatico','available':True})
        self.entries=sorted(entries.values(),key=lambda m:(not m['id'].startswith('auto'),m['id'].lower()))
        self.selected=next((i for i,m in enumerate(self.matches()) if m['id']==current),0)
    def matches(self):
        return [m for m in self.entries if (not self.ready or ready_model(m)) and
                self.query.casefold() in (m['id']+' '+str(m.get('name',''))).casefold()]
    def key(self,key,page=10):
        matches=self.matches()
        if key=='ESC': return self.current
        if key=='ENTER':
            if matches: return matches[self.selected]['id']
        elif key in ('UP','DOWN','PGUP','PGDOWN','HOME','END'):
            if key=='HOME': self.selected=0
            elif key=='END': self.selected=len(matches)-1
            else: self.selected+= {'UP':-1,'DOWN':1,'PGUP':-page,'PGDOWN':page}[key]
        elif key=='TAB': self.ready=not self.ready; self.selected=0
        elif key=='BACKSPACE': self.query=self.query[:-1]; self.selected=0
        elif key=='CLEAR': self.query=''; self.selected=0
        elif len(key)==1 and key.isprintable(): self.query+=key; self.selected=0
        self.selected=max(0,min(self.selected,len(self.matches())-1))
        return None

def pick_model(catalog,current):
    state=ModelPicker(catalog,current)
    if not fancy() or os.name!='nt' or not sys.stdin.isatty():
        page=0
        while True:
            matches=state.matches(); pages=max(1,(len(matches)+19)//20)
            page=max(0,min(page,pages-1)); begin=page*20
            write(f'\nModelli · pagina {page+1}/{pages} · attuale: {current}')
            for i,m in enumerate(matches[begin:begin+20],begin+1): write(f"{i:3}. {m['id']}"+(' [non disponibile]' if m.get('available') is False else ''))
            value=input('Numero · n/p pagina · t tutti/disponibili · /testo cerca · Invio annulla: ').strip()
            if not value: return current
            if value.isdigit() and 1<=int(value)<=len(matches): return matches[int(value)-1]['id']
            if value=='n': page+=1
            elif value=='p': page-=1
            elif value=='t': state.key('TAB'); page=0
            elif value.startswith('/'): state.query=value[1:]; state.selected=0; page=0
    import msvcrt
    from rich.live import Live
    def height(): return max(3,min(15,console.height-11))
    def render():
        matches=state.matches(); count=height()
        start=(state.selected//count)*count
        text=Text('↑ ↓ scegli · PagSu/PagGiù · Invio conferma · Esc annulla\n',style='#b7a1ff')
        text.append('Tab: disponibili/tutti · digita per filtrare · Ctrl+U: azzera\n\n',style='dim')
        text.append(f"Filtro: {state.query or '—'}  |  {'disponibili' if state.ready else 'tutti'}  |  {len(matches)} risultati\n\n",style='#8795a8')
        for i,m in enumerate(matches[start:start+count],start):
            mark='❯' if i==state.selected else ' '
            suffix='  ● attuale' if m['id']==current else ''
            if not ready_model(m): suffix+='  ['+str(m.get('execution_status') or 'non disponibile')+']'
            text.append(f"{mark} {m['id']}{suffix}\n",style='bold #60d5ff' if i==state.selected else '#b4becb')
        if not matches: text.append('Nessun risultato. Cancella il filtro o premi Tab.\n',style='#ffcb77')
        text.append(f'\n{state.selected+1 if matches else 0}/{len(matches)}',style='dim')
        return Panel(text,title='freellmapi · scegli il modello',border_style='#52657a')
    with Live(render(),console=console,auto_refresh=False,transient=True) as live:
        while True:
            key=msvcrt.getwch()
            if key in ('\x00','\xe0'):
                key={'H':'UP','P':'DOWN','I':'PGUP','Q':'PGDOWN','G':'HOME','O':'END'}.get(msvcrt.getwch(),'IGNORE')
            elif key=='\x03': raise KeyboardInterrupt
            else: key={'\r':'ENTER','\x1b':'ESC','\t':'TAB','\b':'BACKSPACE','\x15':'CLEAR'}.get(key,key)
            result=state.key(key,height())
            if result is not None: return result
            live.update(render(),refresh=True)

def action(name, args, depth=0):
    descriptions = {
        'seeker_devices': 'Seeker · rilevamento dispositivi ADB',
        'seeker_packages': 'Seeker · lettura app installate',
        'seeker_logs': 'Seeker · lettura log app ' + str(args.get('package', '(selezionata)')),
        'seeker_crashes': 'Seeker · analisi crash/exit-info ' + str(args.get('package', '(selezionata)')),
        'seeker_install': 'Seeker · installazione APK ' + str(args.get('path', '')),
        'seeker_launch': 'Seeker · avvio app ' + str(args.get('package', '(selezionata)')),
        'seeker_stop': 'Seeker · arresto app ' + str(args.get('package', '(selezionata)')),
        'list_files': 'Elenco file: ' + str(args.get('pattern', '*')),
        'read_file': f"Lettura {args.get('path','')} · dalla riga {args.get('start',1)}",
        'search': 'Ricerca: ' + str(args.get('text', '')),
        'write_file': 'Preparazione scrittura: ' + str(args.get('path','')),
        'edit_file': 'Preparazione modifica: ' + str(args.get('path','')),
        'shell': 'Preparazione comando PowerShell',
        'fetch_url': 'Lettura URL: ' + str(args.get('url','')),
        'set_plan': 'Aggiornamento piano',
        'delegate': 'Delega: ' + str(args.get('task','')),
    }
    prefix = 'subagente' if depth else 'freellmapi'
    label = f"{prefix} · {descriptions.get(name, 'Chiamata '+name)}"
    if fancy(): console.print(Text('  ◇ ' + label[:500], style='bold #b7a1ff'))
    else: write('  > ' + label[:500])

def result(name, value, elapsed):
    value = str(value)
    failure = value.startswith(('Errore:', 'Comando negato', 'Modifica negata', 'MCP negato', 'Operazione Seeker non autorizzata', 'Azione rinviata:', 'Azione non eseguita:'))
    try:
        import json
        structured=json.loads(value)
        if isinstance(structured,dict):failure=failure or structured.get('ok') is False or structured.get('blocked') is True or structured.get('denied') is True
    except (ValueError,TypeError):pass
    title = f"  {'!' if failure else '✓'} {name} · {elapsed:.1f}s"
    if fancy(): console.print(Text(title, style='#ff8787' if failure else '#79db9a'))
    else: write(title)
    if name == 'shell':
        # Command output was already displayed as it arrived.
        write('    ' + value.split('\n',1)[0]); return
    lines = value.splitlines()
    for line in lines[:6]:
        write('    ' + line[:220])
    if len(lines) > 6: write(f'    … altre {len(lines)-6} righe inviate al modello')

def command_output(text):
    if getattr(_thread,'log',None): write(text); return
    if fancy(): console.print(Text(text.rstrip('\r\n'), style='#aab7c4'))
    else: plain_write(text)

def response_info(requested, response, metadata):
    model = response.get('model')
    headers = metadata.get('routing', {})
    routed = headers.get('x-routed-model') or headers.get('x-model-used') or headers.get('x-model')
    effective = routed or (model if model and not model.startswith(('auto', 'fusion')) else None)
    pieces = ['modello: ' + (effective or 'non comunicato dal server')]
    if requested != effective: pieces.insert(0, 'richiesto: ' + requested)
    pieces.append(f"{metadata.get('seconds',0):.1f}s")
    usage = response.get('usage') or {}
    if usage:
        pieces.append(f"token in/out: {usage.get('prompt_tokens','?')}/{usage.get('completion_tokens','?')}")
        if usage.get('total_tokens') is not None: pieces.append(f"totale: {usage['total_tokens']}")
    reason = response.get('choices',[{}])[0].get('finish_reason')
    if reason: pieces.append('esito: ' + reason)
    for key,value in headers.items(): pieces.append(key.removeprefix('x-') + ': ' + value)
    text = '  ' + '  ·  '.join(pieces)
    if fancy(): console.print(Text(text, style='dim #8795a8'))
    else: write(text)

@contextmanager
def busy(label):
    if fancy():
        with console.status(Text(label, style='#b7a1ff'), spinner='dots', spinner_style='bold #60d5ff', refresh_per_second=12) as status:
            yield lambda value: status.update(Text(value, style='#b7a1ff'))
    else:
        write(label)
        yield write

def demo():
    import time
    banner('C:\\progetti\\la-tua-app', 398)
    with busy('freellmapi sta ragionando…'):
        time.sleep(2)
    write('  [strumento] read_file')
    write('\nfreellmapi > **Pronto.** Posso leggere il progetto, modificare file e verificare il risultato.\n')
    write('--- app.py\n+++ app.py\n@@ modifica @@\n-print("ciao")\n+print("Ciao, freellmapi!")')

if __name__ == '__main__': demo()


def finish_stream():
    stream=getattr(_thread,'stream_filter',None)
    if stream is None:return
    text=stream.feed('',final=True);del _thread.stream_filter
    if text:
        if fancy():console.print(Text(text),end='')
        else:plain_write(text)
