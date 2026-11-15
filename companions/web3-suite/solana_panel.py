"""Local Python UI, launched with the isolated Solana environment. No LLM client."""
import argparse,hashlib,json,threading
import tkinter as tk
from tkinter import ttk,messagebox,filedialog
from pathlib import Path
import solana_local as sdk

class Panel:
    def __init__(self,home):
        self.home=Path(home);self.root=tk.Tk();self.root.title('freellmapi · wallet Solana locale');self.root.geometry('850x940')
        self.root.configure(bg='#111827');self.busy=False;self.preview=None
        style=ttk.Style(self.root);style.theme_use('clam');style.configure('.',background='#111827',foreground='#e5eefb',fieldbackground='#223047');style.configure('TButton',padding=8)
        frame=ttk.Frame(self.root,padding=18);frame.pack(fill='both',expand=True)
        ttk.Label(frame,text='freellmapi / SOLANA',font=('Segoe UI',19,'bold')).pack(anchor='w')
        ttk.Label(frame,text='Wallet esistente · chiavi solo in Python locale · nessun wallet generato').pack(anchor='w',pady=8)
        data=sdk.config(home);p=data['profiles'].get(data['default'],{})
        self.name=self.field(frame,'Profilo',data['default'] or 'wallet-dedicato',list(data['profiles']))
        self.network=self.field(frame,'Rete',p.get('network','devnet'),['devnet','mainnet'],False)
        self.account=self.field(frame,'Indirizzo pubblico atteso (facoltativo al primo import)',p.get('account',''))
        rpc=sdk.profile(home,data['default']).get('rpc_url','') if data['default'] else ''
        self.rpc=self.field(frame,'RPC HTTPS opzionale (Helius o altro). Vuoto = pubblico della rete del profilo',rpc,password=True)
        row=ttk.Frame(frame);row.pack(fill='x',pady=5)
        ttk.Button(row,text='Verifica e salva RPC',command=self.save_rpc).pack(side='left')
        ttk.Button(row,text='Dashboard Helius',command=lambda:__import__('webbrowser').open('https://dashboard.helius.dev/31acc248-b2bd-49d3-a394-14956ce35202/rpc')).pack(side='left',padx=8)
        ttk.Button(row,text='Verifica SDK Helius',command=lambda:self.run(lambda:__import__('helius_local').read(self.home,{'profile':self.name.get().strip(),'resource':'network'}))).pack(side='left')
        ttk.Label(frame,text='L’RPC viene verificato sulla rete e salvato con DPAPI. Non serve reimportare il wallet per modificarlo.',wraplength=790).pack(anchor='w')
        self.secret=self.field(frame,'Chiave privata base58 / keypair JSON (mai chat)',password=True)
        row=ttk.Frame(frame);row.pack(fill='x',pady=8)
        ttk.Button(row,text='Importa file keypair',command=self.import_file).pack(side='left')
        ttk.Button(row,text='Collega wallet esistente',command=self.connect).pack(side='left',padx=8)
        ttk.Button(row,text='Svuota campo chiave',command=lambda:self.secret.set('')).pack(side='left')
        ttk.Label(frame,text='La chiave resta mascherata. Archivio Windows DPAPI, nessun fallback in chiaro. Non è un sandbox contro altri processi dello stesso utente.',wraplength=760).pack(anchor='w')
        row=ttk.Frame(frame);row.pack(fill='x',pady=8)
        ttk.Button(row,text='Saldo SOL',command=lambda:self.read('balance')).pack(side='left')
        ttk.Button(row,text='Token SPL / Token-2022',command=lambda:self.read('tokens')).pack(side='left',padx=8)
        ttk.Button(row,text='Ricevute',command=lambda:self.run(lambda:sdk.operations(home))).pack(side='left')
        ttk.Button(row,text='Pump.fun · stesso wallet',command=self.open_pump).pack(side='left',padx=8)
        ttk.Separator(frame).pack(fill='x',pady=8)
        ttk.Label(frame,text='Trasferimento SOL · anteprima e simulazione prima della firma').pack(anchor='w')
        self.recipient=self.field(frame,'Destinatario pubblico');self.amount=self.field(frame,'Importo SOL (non USDC)')
        row=ttk.Frame(frame);row.pack(fill='x',pady=8)
        ttk.Button(row,text='Prepara / simula',command=self.prepare).pack(side='left')
        self.send=ttk.Button(row,text='Rivedi e firma localmente',state='disabled',command=self.review);self.send.pack(side='left',padx=8)
        self.output=tk.Text(frame,height=11,bg='#0b1220',fg='#d9e9ff',wrap='word');self.output.pack(fill='both',expand=True)
        self.show({'state':'Pronto','execution':'Nessun invio avviato. Pump.fun ha un pannello separato e richiede una propria autorizzazione locale.'})

    def field(self,parent,label,value='',choices=None,editable=True,password=False):
        ttk.Label(parent,text=label).pack(anchor='w',pady=(6,2));variable=tk.StringVar(value=value)
        widget=ttk.Combobox(parent,textvariable=variable,values=choices,state='normal' if editable else 'readonly') if choices is not None else ttk.Entry(parent,textvariable=variable,show='●' if password else '')
        widget.pack(fill='x');return variable
    def open_pump(self):
        import subprocess,sys
        subprocess.Popen([sys.executable,'-X','utf8',str(Path(__file__).with_name('pump_panel.py')),'--home',str(self.home)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(__import__('os'),'CREATE_NO_WINDOW',0))

    def show(self,value):
        self.output.delete('1.0','end');self.output.insert('1.0',json.dumps(value,ensure_ascii=False,indent=2))

    def run(self,fn,done=None):
        if self.busy:return
        self.busy=True
        def work():
            try:value=fn()
            except Exception as exc:value={'ok':False,'message':str(exc)[:500]}
            def apply():
                self.busy=False;self.show(value)
                if done:done(value)
            self.root.after(0,apply)
        threading.Thread(target=work,daemon=True).start()

    def import_file(self):
        path=filedialog.askopenfilename(title='Importa un keypair esistente',filetypes=[('Solana keypair','*.json'),('Tutti i file','*')])
        if not path:return
        try:
            if Path(path).stat().st_size>4096:raise ValueError('File keypair troppo grande.')
            self.secret.set(Path(path).read_text(encoding='utf-8').strip())
        except Exception:messagebox.showerror('Importazione','File keypair non leggibile. Nessun contenuto viene registrato.')

    def connect(self):
        name=self.name.get().strip();network=self.network.get();secret=self.secret.get();expected=self.account.get().strip();rpc=self.rpc.get().strip()
        self.preview=None;self.send.configure(state='disabled')
        def done(value):
            if value.get('connected'):self.account.set(value['account'])
        def task():
            value=sdk.import_wallet(self.home,name,network,secret,expected)
            try:value['rpc']=sdk.configure_rpc_local(self.home,name,rpc)
            except Exception as exc:value['rpc_error']=str(exc);value['note']='Wallet importato; RPC non cambiato. Correggi l’endpoint e usa Verifica e salva RPC.'
            return value
        self.run(task,done)

    def save_rpc(self):
        name=self.name.get().strip();rpc=self.rpc.get().strip();self.preview=None;self.send.configure(state='disabled')
        self.run(lambda:sdk.configure_rpc_local(self.home,name,rpc))

    def read(self,resource):
        name=self.name.get().strip();self.run(lambda:sdk.read(self.home,{'resource':resource,'profile':name}))

    def prepare(self):
        args={'profile':self.name.get().strip(),'recipient':self.recipient.get().strip(),'amount_sol':self.amount.get().strip()}
        self.preview=None;self.send.configure(state='disabled')
        def done(value):
            if value.get('state')=='prepared':self.preview=value;self.send.configure(state='normal')
        self.run(lambda:sdk.prepare(self.home,args),done)

    def review(self):
        if self.busy or not self.preview:return
        item=self.preview;body=item['transfer']
        if self.name.get().strip()!=body['profile'] or self.network.get()!=body['network']:messagebox.showerror('Profilo','Profilo/rete cambiati: prepara una nuova anteprima.');return
        message=f"Rete: {body['network']}\nDa: {body['account']}\nA: {body['recipient']}\nSOL: {body['amount_sol']}\nFee stimata: {body['fee_lamports']} lamport\n\nInviare questo trasferimento reale?"
        if not messagebox.askyesno('Conferma esatta del trasferimento',message):return
        review_hash=hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()
        self.preview=None;self.send.configure(state='disabled');self.run(lambda:sdk.send_reviewed(self.home,item['id'],review_hash))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--home',required=True);args=parser.parse_args();panel=Panel(args.home);panel.root.mainloop()
