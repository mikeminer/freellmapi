"""Helius public-data SDK bridge. Wallet keys are never loaded or passed to Node."""
import json,os,shutil,subprocess
from pathlib import Path
import solana_local as sol

SDK=Path(__file__).resolve().parent/'integrations/pumpfun'
RESOURCES={'network','assets_by_owner','asset','priority_fee'}

def status(home,args=None):
    args=args or {};p=sol.profile(home,args.get('profile'))
    manifest=SDK/'node_modules/helius-sdk/package.json'
    return {'provider':sol.rpc_provider(p),'configured':sol.rpc_provider(p)=='helius',
        'sdk':{'package':'helius-sdk','version':json.loads(manifest.read_text())['version'] if manifest.is_file() else None},
        'profile':p['name'],'account':p['account'],'network':p['network'],'resources':sorted(RESOURCES),
        'read_only':True,'wallet_key_loaded':False,'credentials_returned':False,
        'setup':'/solana: inserisci localmente l’endpoint HTTPS RPC Helius. Campo vuoto = RPC pubblico; il dashboard non è un endpoint.'}

def read(home,args):
    if set(args)-{'profile','resource','address','page','limit','account_keys'}:raise ValueError('Campo Helius non esposto: usa soltanto parametri pubblici.')
    resource=args.get('resource','network')
    if resource not in RESOURCES:raise ValueError('Risorsa Helius non valida: '+', '.join(sorted(RESOURCES)))
    p=sol.profile(home,args.get('profile'))
    if sol.rpc_provider(p)!='helius':return {'ok':False,'code':'helius_not_configured','sent':False,'message':'Salva il tuo endpoint Helius nel pannello locale /solana. Le normali letture Solana usano già l’RPC pubblico se il campo è vuoto.'}
    node=shutil.which('node')
    if not node or not (SDK/'node_modules/helius-sdk/package.json').is_file():return {'ok':False,'code':'helius_sdk_missing','sent':False}
    body={k:v for k,v in args.items() if k!='profile'};body.update(resource=resource,network=p['network'],rpc_url=p['rpc_url'])
    if resource=='assets_by_owner':body.setdefault('address',p['account'])
    env={k:v for k,v in os.environ.items() if k.upper() in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP','USERPROFILE','LOCALAPPDATA','NODE_EXTRA_CA_CERTS')};env['NODE_USE_SYSTEM_CA']='1'
    try:
        process=subprocess.run([node,str(SDK/'helius-worker.mjs')],input=json.dumps(body),capture_output=True,text=True,encoding='utf-8',timeout=32,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if process.returncode:raise ValueError()
        result=json.loads(process.stdout)
        # Defence in depth; do not forward raw stderr, subprocess args or endpoint.
        from urllib.parse import urlsplit,parse_qs
        raw=json.dumps(result);secrets=[p['rpc_url'],*parse_qs(urlsplit(p['rpc_url']).query).get('api-key',[])]
        for secret in secrets:
            if secret:raw=raw.replace(secret,'[redacted]')
        return json.loads(raw)
    except subprocess.TimeoutExpired:return {'ok':False,'code':'helius_timeout','sent':False,'retryable':False}
    except Exception:return {'ok':False,'code':'helius_worker_failed','sent':False,'retryable':False}
