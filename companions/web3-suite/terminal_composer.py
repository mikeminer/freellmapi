"""Compact terminal composer; actual mode changes, no simulated telemetry."""
from prompt_toolkit.application import Application
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window, Dimension
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import TextArea
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style
from prompt_toolkit.history import InMemoryHistory

HISTORY=InMemoryHistory()
MODES=('ask','plan','auto')
LABELS={'ask':'conferma azioni','plan':'sola pianificazione','auto':'approvazione automatica'}

def create(model,mode,status,on_mode=None,wake=None,input=None,output=None):
    state={'mode':mode,'help':False}
    keys=KeyBindings()
    editor=TextArea(height=Dimension(min=1,max=5),prompt=' ❯ ',multiline=False,
                    wrap_lines=True,history=HISTORY,style='class:input')
    def accept(buffer):
        app.exit(result=buffer.text)
        return True
    editor.accept_handler=accept
    @keys.add('s-tab')
    def cycle(event):
        if on_mode is None: return
        selected=MODES[(MODES.index(state['mode'])+1)%len(MODES)]
        on_mode(selected);state['mode']=selected;event.app.invalidate()
    @keys.add('c-x')
    def shortcuts(event):
        state['help']=not state['help'];event.app.invalidate()
    @keys.add('c-c')
    def cancel(event): event.app.exit(exception=KeyboardInterrupt())
    @keys.add('c-d')
    def end(event):
        if not editor.text: event.app.exit(exception=EOFError())
    def text_window(text,style='',height=1):
        return Window(FormattedTextControl(text),height=height,style=style,wrap_lines=True)
    def footer():
        return [('class:accent',f' {model} '),('class:muted',f'· {state["mode"]} · {LABELS.get(state["mode"],state["mode"])} ')]
    def shortcuts_text():
        value=' Shift+Tab: modalità   Ctrl+C: annulla   Ctrl+X: scorciatoie'
        if state['help']: value+='\n /model: modelli   /menu: strumenti   /paste: testo lungo\n /status: stato   /sessions: sessioni   /exit: esci'
        return value
    body=HSplit([
        text_window(lambda:status() if callable(status) else status,'class:muted',height=Dimension(min=1,max=2)),
        Window(height=1,char='─',style='class:border'),
        VSplit([Window(width=1,char='│',style='class:border'),editor,Window(width=1,char='│',style='class:border')]),
        VSplit([Window(char='─',style='class:border'),text_window(footer,'',1)],padding=1),
        text_window(shortcuts_text,'class:muted',height=lambda:3 if state['help'] else 1),
    ])
    app=Application(layout=Layout(body,focused_element=editor),key_bindings=keys,
        full_screen=False,refresh_interval=.5,mouse_support=False,input=input,output=output,
        style=Style.from_dict({'input':'#e5e7eb','border':'#687487','muted':'#8795a8','accent':'bold #60d5ff'}))
    def pre_run():
        if wake is None: return
        import asyncio
        async def watch():
            while True:
                await asyncio.sleep(.5)
                if not editor.text:
                    result=wake()
                    if result: app.exit(result=result);return
        app.create_background_task(watch())
    return app,pre_run

def prompt(model,mode,status,on_mode=None,wake=None):
    app,pre_run=create(model,mode,status,on_mode,wake)
    return app.run(pre_run=pre_run)
