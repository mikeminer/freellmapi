"""Local credential adapter. Trading plans belong to freellmapi, not this form."""
import argparse,json,threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk
import hyperliquid_tools as hl

class Panel:
    def __init__(self,home,session):
        self.home=home;self.session=session;self.busy=False;self.values={}
        self.root=tk.Tk();self.root.title('freellmapi · Hyperliquid · chiave locale')
        self.root.geometry('820x700');self.root.minsize(760,650)
        self.root.configure(bg='#111827')
        style=ttk.Style(self.root);style.theme_use('clam')
        style.configure('.',background='#111827',foreground='#dce9f4',fieldbackground='#213047')
        style.configure('TButton',padding=9);style.configure('TNotebook.Tab',padding=(16,10))
        style.map('TCombobox',fieldbackground=[('readonly','#213047')],foreground=[('readonly','#e7f3ff')])
        frame=ttk.Frame(self.root,padding=18);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='freellmapi / HYPERLIQUID',font=('Segoe UI',18,'bold')).pack(anchor='w')
        ttk.Label(frame,text='Adapter della chiave · firma e credenziali solo in Python locale').pack(anchor='w',pady=(6,14))
        tabs=ttk.Notebook(frame);tabs.pack(fill='both',expand=True)
        login=ttk.Frame(tabs,padding=16);receipts=ttk.Frame(tabs,padding=16)
        tabs.add(login,text='Chiave e account');tabs.add(receipts,text='Stato e ricevute')
        data=hl.config(home);default=data.get('default');p=data['profiles'].get(default,{})
        self.field(login,'profile','Profilo',default or 'mainnet-master',choices=list(data['profiles']) or ['mainnet-master'],editable=True)
        self.field(login,'network','Rete',p.get('network','testnet'),choices=['mainnet','testnet'])
        self.field(login,'account','Indirizzo pubblico master',p.get('account',''))
        self.field(login,'kind','Account',p.get('kind','account'),choices=['account','vault','subaccount'])
        self.field(login,'target','Vault / subaccount (vuoto = master)',p.get('target',''))
        self.field(login,'signer_kind','Tipo firma',p.get('signer_kind','agent'),choices=['agent','master'])
        self.field(login,'signing_secret','Chiave API / firma privata locale',password=True)
        ttk.Label(login,text='La chiave resta mascherata dopo il collegamento. Non inserirla in chat.',wraplength=720).pack(anchor='w',pady=8)
        ttk.Label(login,text='Nessuna whitelist coin o policy locale di leva e quantità. I nuovi piani arrivano dalla chat; Hyperliquid verifica margine, precisione e leva ammessa. Il collegamento non riavvia ordini o strategie precedenti.',wraplength=720).pack(anchor='w',pady=10)
        row=ttk.Frame(login);row.pack(fill='x',pady=8)
        for label,fn in [('Carica profilo',self.load_profile),('Salva profilo',lambda:self.work(self.save_profile)),('Collega chiave locale',self.connect),('Svuota campo chiave',self.clear_signing_secret)]:
            ttk.Button(row,text=label,command=fn).pack(side='left',padx=4)
        ttk.Button(login,text='Verifica API e account (nessun ordine)',command=self.read_account).pack(anchor='w',pady=8)
        ttk.Button(receipts,text='Aggiorna stato e ricevute',command=lambda:self.work(self.state)).pack(anchor='w',pady=8)
        self.output=tk.Text(receipts,bg='#0a1220',fg='#bde6ed',wrap='word',font=('Consolas',9));self.output.pack(fill='both',expand=True)
        connected=default and hl.secret_path(home,default).is_file()
        self.status=tk.StringVar(value='Chiave già salvata localmente · '+default if connected else 'Collega la chiave per le nuove richieste di trading.')
        ttk.Label(frame,textvariable=self.status,wraplength=780).pack(anchor='w',pady=(12,0))
    def field(self,parent,key,label,value='',choices=None,password=False,editable=False):
        row=ttk.Frame(parent);row.pack(fill='x',pady=5)
        ttk.Label(row,text=label,width=32).pack(side='left')
        var=tk.StringVar(value=str(value));self.values[key]=var
        widget=ttk.Combobox(row,textvariable=var,values=choices,state='normal' if editable else 'readonly') if choices else ttk.Entry(row,textvariable=var,show='*' if password else '')
        widget.pack(side='left',fill='x',expand=True)
    def capture(self):return {k:v.get().strip() for k,v in self.values.items() if k!='signing_secret'}
    def work(self,fn):
        if self.busy:return
        self.busy=True;self.status.set('Verifica locale / API in corso…')
        def worker():
            try:
                result=fn();self.root.after(0,lambda:self.done(result))
            except Exception as exc:
                message=str(exc) if isinstance(exc,hl.HLProblem) else type(exc).__name__+': operazione non completata; nessuna chiave nei log.'
                self.root.after(0,lambda:self.done({'error':message}))
        threading.Thread(target=worker,daemon=True).start()
    def done(self,result):
        self.busy=False
        self.status.set(result.get('error','Completato.') if isinstance(result,dict) else 'Completato.')
        if isinstance(result,dict) and result.get('connected'):
            self.status.set('Chiave collegata · '+result['network']+' · nuovi piani dalla chat. Nessun ordine avviato dal collegamento.')
        self.output.delete('1.0','end');self.output.insert('1.0',json.dumps(result,ensure_ascii=False,indent=2))
    def save_profile(self,values=None):
        v=values or self.capture();return hl.configure(self.home,v['profile'],v['network'],v['account'],v['target'] or None,v['kind'])
    def load_profile(self):
        try:
            p=hl.profile(self.home,self.values['profile'].get())
            for key in ('network','account','kind','target','signer_kind'):self.values[key].set(p.get(key,'agent' if key=='signer_kind' else ''))
            self.status.set('Profilo caricato: '+p['name'])
        except hl.HLProblem as exc:self.status.set(str(exc))
    def connect(self):
        if self.busy:return
        v=self.capture();key=self.values['signing_secret'].get().strip()
        def action():
            from hyperliquid_signer import connect
            self.save_profile(v);return connect(self.home,v['profile'],key,v['signer_kind'])
        self.work(action)
    def clear_signing_secret(self):self.values['signing_secret'].set('')
    def read_account(self):
        name=self.values['profile'].get()
        self.work(lambda:hl.read(self.home,{'profile':name,'resource':'account'}))
    def state(self):return {'configuration':hl.status(self.home),'operations':hl.operations(self.home)}
    def run(self):self.root.mainloop()

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--home',required=True);parser.add_argument('--session',required=True);args=parser.parse_args()
    from app_home import runtime_home
    Panel(runtime_home(args.home),Path(args.session)).run()
