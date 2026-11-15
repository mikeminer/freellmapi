"""Solana SDK adapter. Secret import/signing is callable only by the local GUI."""
import asyncio,base64,hashlib,json,os,re,sqlite3,time,uuid
from contextlib import contextmanager
from decimal import Decimal,InvalidOperation
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

NETWORKS={'devnet':'https://api.devnet.solana.com','mainnet':'https://api.mainnet-beta.solana.com'}
GENESIS={'devnet':'EtWTRABZaYq6iMfeYKouRu166VU2xqa1wcaWoxPkrZBG','mainnet':'5eykt4UsFv8P8NJdTREpY1vzqKqZKvdpKuc147dw2N9d'}
TOKEN_PROGRAMS=('TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA','TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb')

def folder(home):
    p=Path(home)/'solana';p.mkdir(parents=True,exist_ok=True);return p

def config(home):
    p=folder(home)/'profiles.json'
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {'default':None,'profiles':{}}

def atomic(path,value):
    from reliable_runtime import atomic_bytes
    atomic_bytes(path,json.dumps(value,indent=2).encode('utf-8'))

def pubkey(value):
    from solders.pubkey import Pubkey
    try:return Pubkey.from_string(value)
    except Exception:raise ValueError('Indirizzo pubblico Solana non valido.') from None

def profile(home,name=None):
    data=config(home);name=name or data['default']
    if name not in data['profiles']:raise ValueError('Nessun profilo Solana corrispondente. Importa il wallet nel pannello Python locale /solana.')
    p=dict(data['profiles'][name],name=name)
    if p['network'] not in NETWORKS:raise ValueError('Rete non supportata.')
    pubkey(p['account'])
    if p.get('rpc_private'):
        from hl_credentials import get
        p['rpc_url']=get(folder(home)/(p['name']+'.rpc.dpapi'))
    return p

def configure_rpc_local(home,name,endpoint):
    """Local GUI only. Empty selects the public RPC; never return credentials."""
    from hl_credentials import put
    from trading_runtime import singleton
    data=config(home)
    if name not in data['profiles']:raise ValueError('Seleziona prima un wallet Solana esistente.')
    p={**data['profiles'][name],'name':name};public=not endpoint.strip()
    endpoint=endpoint.strip() or NETWORKS[p['network']];u=urlsplit(endpoint)
    if u.scheme!='https' or not u.hostname or u.username or u.password or u.fragment:raise ValueError('RPC HTTPS richiesto, senza user/password.')
    if u.hostname=='dashboard.helius.dev':raise ValueError('Questo è il dashboard Helius. Copia il suo endpoint HTTPS RPC, non l’indirizzo del dashboard.')
    candidate={**p,'rpc_url':endpoint}
    if query(candidate,'getGenesisHash',[])!=GENESIS[p['network']]:raise ValueError('RPC su una rete diversa dal profilo.')
    # An RPC change invalidates Pump grants by scope, and cannot race a send.
    with singleton(folder(home)/'pumpfun'/'execution.lock'):
        latest=config(home)
        if latest['profiles'].get(name)!=data['profiles'][name]:raise ValueError('Profilo cambiato durante la verifica: rileggilo.')
        if not public:put(folder(home)/(name+'.rpc.dpapi'),endpoint)
        latest['profiles'][name]['rpc_private']=not public;latest['profiles'][name].pop('rpc_url',None)
        atomic(folder(home)/'profiles.json',latest)
    return {'rpc_configured':True,'public_rpc':public,'provider':rpc_provider(candidate),'profile':name,'network':p['network'],'url_returned':False,'wallet_changed':False}

def rpc_provider(p):
    host=urlsplit(p.get('rpc_url') or NETWORKS[p['network']]).hostname or ''
    if host.endswith('.helius-rpc.com') or host.endswith('.helius.xyz'):return 'helius'
    return 'custom' if p.get('rpc_url') and p['rpc_url']!=NETWORKS[p['network']] else 'public'

def import_wallet(home,name,network,secret,expected=''):
    """Local GUI only. No wallet is generated and no network call is made."""
    from solders.keypair import Keypair
    from hl_credentials import put
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,48}',name) or network not in NETWORKS:raise ValueError('Nome profilo o rete non validi.')
    try:
        raw=secret.strip()
        if raw.startswith('['):
            values=json.loads(raw)
            if not isinstance(values,list) or len(values)!=64 or any(type(x) is not int or not 0<=x<=255 for x in values):raise ValueError()
            wallet=Keypair.from_bytes(bytes(values))
        else:wallet=Keypair.from_base58_string(raw)
    except Exception:raise ValueError('Chiave non valida: importa una chiave base58 di 64 byte oppure il file JSON keypair Solana. Nessun segreto viene registrato.') from None
    account=str(wallet.pubkey())
    if expected and str(pubkey(expected))!=account:raise ValueError('La chiave non corrisponde all’indirizzo pubblico indicato.')
    data=config(home);old=data['profiles'].get(name)
    if old and old['account']!=account:raise ValueError('Questo nome appartiene a un altro wallet: usa un nuovo nome, senza sovrascriverlo.')
    put(folder(home)/(name+'.dpapi'),str(wallet))
    saved_rpc={k:v for k,v in (old or {}).items() if k in ('rpc_private','rpc_url')} if old and old['network']==network else {}
    data['profiles'][name]={'account':account,'network':network,**saved_rpc};data['default']=name;atomic(folder(home)/'profiles.json',data)
    return {'connected':True,'profile':name,'account':account,'network':network,'secret_returned':False}

def status(home):
    data=config(home)
    return {'sdk':{'solana':version('solana'),'solders':version('solders')},'default':data['default'],
            'profiles':[{'account':p['account'],'network':p['network'],'name':n,'custom_rpc':bool(p.get('rpc_private') or p.get('rpc_url')),'signer_configured':(folder(home)/(n+'.dpapi')).is_file()} for n,p in data['profiles'].items()],
            'supported_execution':['SOL transfer after local GUI review'],'private_keys':'Local Python GUI + Windows DPAPI; never model tool fields. Same-user OS processes are not an isolated sandbox.'}

async def rpc(p,method,params):
    # SDK types construct/validate transactions. The standard JSON-RPC wire
    # transport uses Windows trust roots, with no retry of a sendTransaction.
    import ssl,truststore,urllib.request
    def fetch():
        endpoint=p.get('rpc_url') or NETWORKS[p['network']];parsed=urlsplit(endpoint)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:raise ValueError('Endpoint RPC HTTPS non valido.')
        request=urllib.request.Request(endpoint,data=json.dumps({'jsonrpc':'2.0','id':1,'method':method,'params':params}).encode(),headers={'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(request,context=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),timeout=15) as response:value=json.loads(response.read(2_000_000))
        except Exception as exc:raise RuntimeError('Solana RPC non disponibile ('+type(exc).__name__+'). Nessun retry automatico dell’invio.') from None
        if 'error' in value:raise RuntimeError('Solana RPC: errore '+str(value['error'].get('code'))+'. Nessun dettaglio del provider o URL viene registrato.')
        return value['result']
    return await asyncio.to_thread(fetch)

def query(p,method,params):return asyncio.run(rpc(p,method,params))

def read(home,args):
    p=profile(home,args.get('profile'));address=str(pubkey(args.get('address') or p['account']));resource=args.get('resource','balance')
    if resource=='balance':data=query(p,'getBalance',[address,{'commitment':'confirmed'}])
    elif resource=='tokens':
        data=[]
        for program in TOKEN_PROGRAMS:
            response=query(p,'getTokenAccountsByOwner',[address,{'programId':program},{'encoding':'jsonParsed','commitment':'confirmed'}])
            for item in response['value'][:200]:
                info=item['account']['data']['parsed']['info'];data.append({'token_account':item['pubkey'],'mint':info['mint'],'owner':info['owner'],'program':program,'amount':info['tokenAmount']})
    elif resource=='transactions':data=query(p,'getSignaturesForAddress',[address,{'limit':max(1,min(50,int(args.get('limit',10)))),'commitment':'confirmed'}])
    elif resource=='account':data=query(p,'getAccountInfo',[address,{'encoding':'jsonParsed','commitment':'confirmed'}])
    elif resource=='network':data={'version':query(p,'getVersion',[]),'genesis_hash':query(p,'getGenesisHash',[])}
    elif resource=='signature':
        from solders.signature import Signature
        try:signature=str(Signature.from_string(args.get('signature','')))
        except Exception:raise ValueError('Firma pubblica della transazione non valida.') from None
        data=query(p,'getSignatureStatuses',[[signature],{'searchTransactionHistory':True}])
    else:raise ValueError('Risorsa: balance, tokens, transactions, account, network, signature.')
    return {'profile':p['name'],'network':p['network'],'address':address,'resource':resource,'retrieved_ms':int(time.time()*1000),'source':rpc_provider(p),'data':data}

def transaction(p,body):
    from solders.hash import Hash
    from solders.message import Message
    from solders.system_program import transfer,TransferParams
    from solders.transaction import Transaction
    instruction=transfer(TransferParams(from_pubkey=pubkey(p['account']),to_pubkey=pubkey(body['recipient']),lamports=body['lamports']))
    return Transaction.new_unsigned(Message.new_with_blockhash([instruction],pubkey(p['account']),Hash.from_string(body['blockhash'])))

def simulate(home,args):
    p=profile(home,args.get('profile'))
    raw=args.get('transaction','')
    if not isinstance(raw,str) or len(raw)>90000:raise ValueError('Transazione base64 troppo grande o non valida.')
    from solders.transaction import VersionedTransaction
    try:VersionedTransaction.from_bytes(base64.b64decode(raw,validate=True))
    except Exception:raise ValueError('Transazione serializzata Solana non valida.') from None
    value=query(p,'simulateTransaction',[raw,{'encoding':'base64','sigVerify':False,'replaceRecentBlockhash':True,'commitment':'confirmed'}])
    return {'network':p['network'],'simulation':value,'sent':False,'signature_verified':False,'note':'Simulation is not execution or authorization.'}

def open_database(home):
    db=sqlite3.connect(folder(home)/'operations.db',timeout=10);db.row_factory=sqlite3.Row
    db.execute('PRAGMA synchronous=FULL');db.execute('CREATE TABLE IF NOT EXISTS operations(id TEXT PRIMARY KEY,state TEXT,body TEXT,signature TEXT,result TEXT)');db.commit();return db

@contextmanager
def database(home):
    db=open_database(home)
    try:
        with db:yield db
    finally:db.close()

def prepare(home,args):
    p=profile(home,args.get('profile'));recipient=str(pubkey(args.get('recipient')))
    try:amount=Decimal(str(args.get('amount_sol')));scaled=amount*10**9
    except (InvalidOperation,TypeError):raise ValueError('Importo SOL decimale non valido.') from None
    if not amount.is_finite() or amount<=0 or scaled!=scaled.to_integral_value() or scaled>2**64-1:raise ValueError('Importo SOL positivo, massimo 9 decimali, entro uint64.')
    block=query(p,'getLatestBlockhash',[{'commitment':'confirmed'}])['value']
    body={'profile':p['name'],'network':p['network'],'account':p['account'],'recipient':recipient,'lamports':int(scaled),
          'amount_sol':str(amount),'blockhash':block['blockhash'],'last_valid_block_height':block['lastValidBlockHeight'],'created_ms':int(time.time()*1000)}
    tx=transaction(p,body);encoded=base64.b64encode(bytes(tx)).decode()
    body['fee_lamports']=query(p,'getFeeForMessage',[base64.b64encode(bytes(tx.message)).decode(),{'commitment':'confirmed'}])['value']
    if body['fee_lamports'] is None:raise ValueError('Commissione non verificata: anteprima non creata.')
    simulation=simulate(home,{'profile':p['name'],'transaction':encoded})
    if simulation['simulation']['value']['err'] is not None:raise ValueError('Simulazione rifiutata: '+str(simulation['simulation']['value']['err'])[:200])
    ident=uuid.uuid4().hex
    with database(home) as db:db.execute('INSERT INTO operations VALUES(?,?,?,?,?)',(ident,'prepared',json.dumps(body),None,None))
    return {'id':ident,'state':'prepared','transfer':body,'simulation':simulation,'sent':False,'local_review_required':True}

def operations(home):
    with database(home) as db:rows=db.execute('SELECT * FROM operations ORDER BY rowid DESC LIMIT 30').fetchall()
    return {'operations':[{'id':r['id'],'state':r['state'],'body':{k:v for k,v in json.loads(r['body']).items() if k not in ('unsigned_transaction','instructions','rpc_url')},'signature':r['signature'],'result':json.loads(r['result']) if r['result'] else None} for r in rows]}

def send_reviewed(home,ident,review_hash):
    """Local GUI only after exact preview review. Never exposed as a model tool."""
    from solders.keypair import Keypair
    from solders.hash import Hash
    from hl_credentials import get
    db=open_database(home)
    try:
        db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT * FROM operations WHERE id=?',(ident,)).fetchone()
        if not row or row['state']!='prepared':raise ValueError('Anteprima assente o già usata. Nessun reinvio.')
        body=json.loads(row['body']);p=profile(home,body['profile'])
        if body.get('protocol')=='pumpfun':raise ValueError('Usa la revisione Pump.fun: questa anteprima non è un trasferimento SOL.')
        if hashlib.sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()!=review_hash:raise ValueError('Anteprima modificata dopo la revisione: nessuna firma.')
        if (body['network'],body['account'])!=(p['network'],p['account']):raise ValueError('Profilo modificato dopo l’anteprima.')
        if time.time()*1000-body['created_ms']>90000:raise ValueError('Anteprima scaduta: rileggi i dati, senza reinviare la precedente.')
        if query(p,'getGenesisHash',[])!=GENESIS[p['network']]:raise ValueError('Identità della rete RPC non corrispondente: nessuna firma.')
        # Never duplicate a payment whose outcome is still uncertain.
        pending=db.execute("SELECT body FROM operations WHERE state IN ('sending','uncertain')").fetchall()
        if any((b:=json.loads(r['body']))['account']==p['account'] and b['network']==p['network'] for r in pending):raise ValueError('Esito precedente incerto: riconcilia la firma prima di altri invii.')
        tx=transaction(p,body);wallet=Keypair.from_base58_string(get(folder(home)/(p['name']+'.dpapi')))
        if str(wallet.pubkey())!=p['account']:raise ValueError('Firma locale non corrispondente al wallet.')
        tx.sign([wallet],Hash.from_string(body['blockhash']));signature=str(tx.signatures[0])
        db.execute('UPDATE operations SET state=?,signature=? WHERE id=?',('sending',signature,ident));db.commit()
        try:
            observed=query(p,'sendTransaction',[base64.b64encode(bytes(tx)).decode(),{'encoding':'base64','skipPreflight':False,'preflightCommitment':'confirmed','maxRetries':0}])
            if observed!=signature:raise RuntimeError('Firma di risposta non corrispondente')
            result={'state':'submitted','signature':signature,'confirmed':False}
        except Exception:result={'state':'uncertain','signature':signature,'confirmed':False,'instruction':'Verifica getSignatureStatuses; non creare un trasferimento sostitutivo.'}
        db.execute('UPDATE operations SET state=?,result=? WHERE id=?',(result['state'],json.dumps(result),ident));db.commit();return result
    finally:db.close()

def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--home',required=True);args=parser.parse_args()
    request=json.loads(__import__('sys').stdin.read(120000));name=request['command'];body=request.get('arguments',{})
    # This IPC entry point deliberately cannot import a private key or send.
    actions={'status':lambda:status(args.home),'read':lambda:read(args.home,body),'prepare':lambda:prepare(args.home,body),
             'simulate':lambda:simulate(args.home,body),'operations':lambda:operations(args.home),
             'helius_status':lambda:__import__('helius_local').status(args.home,body),
             'helius_read':lambda:__import__('helius_local').read(args.home,body)}
    try:
        if name not in actions:raise ValueError('Operazione SDK non esposta.')
        print(json.dumps(actions[name](),ensure_ascii=False))
    except Exception as exc:print(json.dumps({'ok':False,'code':'solana_operation_failed','message':str(exc)[:500],'sent':False},ensure_ascii=False))

if __name__=='__main__':main()
