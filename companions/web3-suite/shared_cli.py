"""Interactive CMD is a client of the same coordinator used by phone/desktop."""
import time
from pathlib import Path
import terminal_ui as ui
from remote_client import RemoteClient

def run(home,args):
    from session_store import Sessions,choose
    from questions import ask
    client=RemoteClient(home,'cmd');store=Sessions(home)
    rows=store.list(Path.cwd());target=args.session
    fresh=args.new
    if not (target or fresh or args.resume or args.last):
        result=ask('freellmapi · sessioni',['Riprendi ultima sessione','Nuova sessione','Scegli una sessione'])
        if result.get('cancelled'):return
        choice=result.get('answer')
        if choice=='Scegli una sessione':target=choose(store)
        elif choice=='Nuova sessione':fresh=True
    if not target and not fresh and rows:target=rows[0]['id']
    state=client.connect(session=target if target and client.descriptors(target) else None,auto_start=True);cursor=state.get('cursor',0)
    def events():
        nonlocal cursor,state
        frame=client.request('/v1/events?after='+str(cursor))
        cursor=frame['cursor']
        for event in frame.get('events',[]):
            if event['kind']!='user':ui.write(event['text'])
        state=client.state()
    def wait(receipt):
        nonlocal cursor,state
        if receipt.get('state')=='attached':
            state=client.last_state;cursor=state.get('cursor',0)
            ui.write('Collegato al coordinatore della sessione '+state['session']+' · contesto conservato, nessuna azione ripetuta.')
            return receipt
        ident=receipt.get('request_id');asked=set()
        while True:
            try:
                events()
                question=state.get('question')
                if question and question['id'] not in asked:
                    asked.add(question['id']);answer=ask(question['question'],question.get('options') or ['Continua','Annulla'])
                    client.answer(question['id'],answer.get('answer',''),answer.get('cancelled',False))
                exact=client.request('/v1/state?request_id='+ident).get('request_receipt') or {}
                result=exact.get('receipt') or {}
                if result.get('state') in ('finished','interrupted_not_replayed'):
                    events();return result
                if receipt.get('state')=='unconfirmed' and not exact.get('found'):
                    ui.write('Esito non confermato: nessun reinvio. Usa /check per verificare la ricevuta.');return receipt
                time.sleep(.2)
            except KeyboardInterrupt:client.request('/v1/cancel',{});ui.write('Interruzione richiesta al coordinatore.');return {}
    if fresh:wait(client.send('/project '+str(Path.cwd())))
    elif target and state['session']!=target:wait(client.send('/resume '+target))
    if args.model and args.model!=state['model']:wait(client.send('/model '+args.model))
    if args.auto:wait(client.send('/auto on'))
    ui.write('freellmapi · coordinatore condiviso\nSessione: '+state['session']+'\nProgetto: '+state['project']+'\nStessa chat di Seeker e desktop. /exit scollega solo questo CMD.')
    for m in state.get('messages',[])[-4:]:ui.write(('tu > ' if m['role']=='user' else 'freellmapi > ')+m['content'])
    while True:
        try:
            text=ui.prompt(state['model'],state['mode'],lambda:'Coordinatore condiviso · '+state['session'][:8],on_mode=lambda mode:wait(client.send('/auto on' if mode=='auto' else '/'+mode)))
            if text in ('/exit','/quit'):return
            if text=='/menu':text=ui.menu()
            if text in ('/sessions','/resume'):
                selected=choose(store)
                if not selected:continue
                text='/resume '+selected
            if text=='/model':
                from freellmapi import select_model
                text='/model '+select_model([{'id':name} for name in state['models']],state['model'])
            if text=='/check':
                if client.pending.exists():wait(client.check_pending())
                else:ui.write('Nessun invio non confermato.')
                continue
            if text:wait(client.send(text))
        except EOFError:return
        except KeyboardInterrupt:ui.write('CMD scollegato; il coordinatore conserva sessione e contesto.');return
        except (OSError,ValueError,RuntimeError) as error:ui.write(str(error))
