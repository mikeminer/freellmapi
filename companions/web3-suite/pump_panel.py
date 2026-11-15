"""Local Pump control UI; reuses existing Python Solana wallet, no key import."""
import argparse,hashlib,json,threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk,messagebox
import pump_local as pump
import pump_service as service
import solana_local as sol

class Panel:
    def __init__(self,home):
        self.home=Path(home);self.busy=False;self.preview=None;self.values={}
        self.root=tk.Tk();self.root.title('freellmapi · Pump.fun · firma Python locale');self.root.geometry('900x850')
        style=ttk.Style(self.root);style.theme_use('clam');style.configure('.',background='#111827',foreground='#e5eefb',fieldbackground='#223047');style.configure('TButton',padding=7)
        self.root.configure(bg='#111827');tabs=ttk.Notebook(self.root);tabs.pack(fill='both',expand=True,padx=12,pady=10)
        trade=ttk.Frame(tabs,padding=12);settings=ttk.Frame(tabs,padding=12);state=ttk.Frame(tabs,padding=12)
        tabs.add(trade,text='Mercato e revisione');tabs.add(settings,text='Limiti e servizio');tabs.add(state,text='Stato e ricevute')
        cfg=service.load(home);profiles=sol.config(home)['profiles']
        self.field(trade,'profile','Wallet Solana già importato',cfg['profile'],list(profiles));self.field(trade,'mint','Mint esatto (non username pump.fun/u/)','')
        self.field(trade,'side','Lato','buy',['buy','sell']);self.field(trade,'amount','SOL da comprare / token da vendere','')
        self.field(trade,'slippage_bps','Slippage massimo, basis point','100')
        self.field(trade,'take_profit_pct','TP percentuale, monitorato dal PC','50');self.field(trade,'stop_loss_pct','SL percentuale, monitorato dal PC','25')
        row=ttk.Frame(trade);row.pack(fill='x',pady=8)
        ttk.Button(row,text='Analizza mint',command=self.analyze).pack(side='left');ttk.Button(row,text='Prepara e simula, senza firma',command=self.prepare).pack(side='left',padx=8)
        self.sign=ttk.Button(row,text='Rivedi e firma UNA transazione',command=self.review,state='disabled');self.sign.pack(side='left')
        ttk.Label(trade,text='Il wallet rimane nell’adapter Python esistente. Nessuna chiave viene inviata ai modelli o a Node. TP/SL richiedono PC, servizio, RPC e consenso attivi: non sono ordini on-chain garantiti.',wraplength=820).pack(anchor='w',pady=8)
        self.trade_output=tk.Text(trade,height=14,bg='#0b1220',fg='#d9e9ff',wrap='word');self.trade_output.pack(fill='both',expand=True)
        canvas=tk.Canvas(settings,bg='#111827',highlightthickness=0);bar=ttk.Scrollbar(settings,command=canvas.yview);canvas.configure(yscrollcommand=bar.set);bar.pack(side='right',fill='y');canvas.pack(side='left',fill='both',expand=True)
        content=ttk.Frame(canvas);win=canvas.create_window((0,0),window=content,anchor='nw');content.bind('<Configure>',lambda e:canvas.configure(scrollregion=canvas.bbox('all')));canvas.bind('<Configure>',lambda e:canvas.itemconfigure(win,width=e.width))
        ttk.Label(content,text='Tutti i limiti monetari sono SOL, non USDC o USD. Nessun capitale viene dedotto dal post promozionale.',wraplength=780).pack(anchor='w',pady=5)
        for k,label,value in [('max_trade_sol','Massimo speso per acquisto, incluse fee/rent',''),('max_total_sol','Spesa cumulativa massima, incluse uscite',''),('fee_reserve_sol','Riserva SOL intoccabile per fee',''),('max_loss_sol','Stop nuovi acquisti al calo di cassa SOL (prudenziale, conta capitale investito)',''),('max_positions','Massimo posizioni','3'),('max_trades','Massimo transazioni totali (riservare almeno acquisto+uscita)','10'),('duration_minutes','Durata consenso, minuti (max 1440)','60'),('mints','Watchlist mint separati da virgola',','.join(cfg['mints'])),('trade_sol','Importo SOL per acquisto del servizio',cfg['trade_sol']),('route','Modello / routing analista',cfg['route'])]:self.field(content,k,label,value)
        self.discover=tk.BooleanVar(value=False);ttk.Checkbutton(content,text='Consenti ricerca di nuovi mint Pump (copertura e rischio incompleti)',variable=self.discover).pack(anchor='w',pady=4)
        self.adopt=tk.BooleanVar(value=False);ttk.Checkbutton(content,text='Adotta esplicitamente le posizioni Pump già registrate in questo profilo',variable=self.adopt).pack(anchor='w',pady=4)
        self.field(content,'rpc_url','RPC HTTPS opzionale: vuoto = pubblico della rete del profilo',sol.profile(home,cfg['profile']).get('rpc_url',''),secret=True)
        ttk.Button(content,text='Verifica e salva RPC localmente',command=self.save_rpc).pack(anchor='w',pady=6)
        ttk.Button(content,text='Salva configurazione disattivata, senza avvio',command=lambda:self.save_config('disabled')).pack(anchor='w',pady=5)
        ttk.Button(content,text='Autorizza LIVE localmente e salva, senza avvio…',command=self.authorize).pack(anchor='w',pady=5)
        ttk.Button(content,text='Avvia servizio nella modalità salvata',command=lambda:self.work(lambda:service.start(home))).pack(anchor='w',pady=5)
        ttk.Button(content,text='STOP nuovi acquisti (uscite autorizzate restano)',command=lambda:self.work(lambda:pump.stop(home))).pack(anchor='w',pady=5)
        ttk.Button(content,text='Riprendi nuovi acquisti, stessi limiti…',command=self.resume_entries).pack(anchor='w',pady=5)
        ttk.Button(content,text='STOP tutto e revoca consenso (non chiude posizioni)',command=lambda:self.work(lambda:pump.stop(home,True))).pack(anchor='w',pady=5)
        ttk.Label(content,text='Il cap è applicativo: la chiave conserva i normali poteri del wallet Solana. Non c’è un cap on-chain. Il consenso non si rinnova da solo; quando scade anche le uscite automatiche si fermano. Nessuna protezione MEV/Jito viene dichiarata da questa versione.',wraplength=780).pack(anchor='w',pady=8)
        ttk.Button(state,text='Leggi stato e ricevute',command=self.status).pack(anchor='w')
        self.state_output=tk.Text(state,bg='#0b1220',fg='#d9e9ff',wrap='word');self.state_output.pack(fill='both',expand=True,pady=8)
        self.message=tk.StringVar(value='Pronto · nessuna autorizzazione o operazione avviata');ttk.Label(self.root,textvariable=self.message,wraplength=850).pack(fill='x',padx=12,pady=6)
    def field(self,parent,key,label,value,choices=None,secret=False):
        ttk.Label(parent,text=label,wraplength=790).pack(anchor='w',pady=(6,1));v=tk.StringVar(value=value);self.values[key]=v
        w=ttk.Combobox(parent,textvariable=v,values=choices,state='readonly') if choices is not None else ttk.Entry(parent,textvariable=v,show='●' if secret else '')
        w.pack(fill='x');return v
    def text(self,key):return self.values[key].get().strip()
    def work(self,fn,done=None):
        if self.busy:return
        self.busy=True;self.message.set('Verifica in corso…')
        def task():
            try:value=fn()
            except Exception as e:value={'ok':False,'code':str(e) if isinstance(e,(pump.PumpError,ValueError)) else type(e).__name__}
            def apply():
                self.busy=False;self.message.set('Completato' if value.get('ok',True) else value['code'])
                self.trade_output.delete('1.0','end');self.trade_output.insert('1.0',json.dumps(value,indent=2,ensure_ascii=False))
                if done:done(value)
            self.root.after(0,apply)
        threading.Thread(target=task,daemon=True).start()
    def analyze(self):
        args={'profile':self.text('profile'),'mint':self.text('mint')};self.work(lambda:pump.analyze(self.home,args))
    def prepare(self):
        try:bps=int(self.text('slippage_bps'))
        except ValueError:self.message.set('Slippage: inserisci un numero intero di basis point.');return
        args={'profile':self.text('profile'),'mint':self.text('mint'),'side':self.text('side'),'slippage_bps':bps}
        args['amount_sol' if args['side']=='buy' else 'token_amount']=self.text('amount')
        protection={k:self.text(k) for k in ('take_profit_pct','stop_loss_pct')};self.preview=None;self.sign.configure(state='disabled')
        def task():return pump.prepare(self.home,{'quote_id':pump.quote(self.home,args)['quote_id'],**protection})
        def done(value):
            if value.get('state')=='prepared':self.preview=value;self.sign.configure(state='normal')
        self.work(task,done)
    def review(self):
        if self.busy or not self.preview:return
        value=self.preview
        if not messagebox.askyesno('Firma Pump.fun reale',json.dumps(value.get('preview'),indent=2,ensure_ascii=False)+'\n\nFirmare e inviare UNA transazione reale? La simulazione e TP/SL previsti non sono garanzie.'):return
        self.preview=None;self.sign.configure(state='disabled');self.work(lambda:pump.execute(self.home,value['id'],value['review_hash']))
    def config(self,mode):
        cfg=service.load(self.home);cfg.update(mode=mode,profile=self.text('profile'),mints=[m.strip() for m in self.text('mints').split(',') if m.strip()],allow_discovery=self.discover.get(),trade_sol=self.text('trade_sol'),route=self.text('route'),max_positions=int(self.text('max_positions')),slippage_bps=int(self.text('slippage_bps')),take_profit_pct=self.text('take_profit_pct'),stop_loss_pct=self.text('stop_loss_pct'))
        return cfg
    def save_config(self,mode):
        try:cfg=self.config(mode)
        except ValueError:self.message.set('Slippage e massimo posizioni devono essere numeri interi.');return
        self.work(lambda:service.configure_local(self.home,cfg))
    def authorize(self):
        if self.busy:return
        try:
            settings={k:self.text(k) for k in ('max_trade_sol','max_total_sol','fee_reserve_sol','max_loss_sol')}
            settings.update({k:int(self.text(k)) for k in ('max_positions','max_trades','duration_minutes')})
            settings.update(max_slippage_bps=int(self.text('slippage_bps')),mints=[m.strip() for m in self.text('mints').split(',') if m.strip()],allow_discovery=self.discover.get(),adopt_positions=self.adopt.get())
            normalized=pump.limits(settings);cfg=service.validate(self.config('live'));name=self.text('profile');public=pump.profile(self.home,name)
            summary={'profile':name,'network':public['network'],'account':public['account'],'settings':settings,'policy_normalized_lamports_and_bps':normalized,'service':cfg,'auto_renew':False}
            if not messagebox.askyesno('Autorizza spesa LIVE Pump.fun',json.dumps(summary,indent=2,ensure_ascii=False)+'\n\nAutorizzare spese REALI su questo wallet? Non avvia il servizio.'):return
            self.work(lambda:service.authorize_config_local(self.home,cfg,settings))
        except Exception as e:self.message.set(str(e))
    def save_rpc(self):
        name=self.text('profile');url=self.text('rpc_url');self.work(lambda:sol.configure_rpc_local(self.home,name,url))
    def resume_entries(self):
        if messagebox.askyesno('Ripresa locale','Riprendere nuovi acquisti con la modalità salvata e gli stessi limiti? Non rinnova il consenso e non avvia il servizio.'):
            self.work(lambda:service.resume_entries_local(self.home))
    def status(self):
        name=self.text('profile')
        def done(value):self.state_output.delete('1.0','end');self.state_output.insert('1.0',json.dumps(value,indent=2,ensure_ascii=False))
        self.work(lambda:{'wallet':pump.status(self.home,name),'service':service.service_status(self.home),'operations':pump.operations(self.home)},done)
if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--home',required=True);a=parser.parse_args();Panel(Path(a.home)).root.mainloop()
