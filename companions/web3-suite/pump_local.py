"""Pump intent/quote journal and local signer. Node sees public keys only."""
import base64,hashlib,json,os,shutil,sqlite3,struct,subprocess,sys,time,uuid
from contextlib import contextmanager
from decimal import Decimal,InvalidOperation
from pathlib import Path
import solana_local as sol

ROOT=Path(__file__).resolve().parent;SDK=ROOT/'integrations/pumpfun'
PROGRAMS={'curve':'6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P','amm':'pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA'}
WSOL='So11111111111111111111111111111111111111112';SYSTEM='11111111111111111111111111111111'
ATA='ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL';COMPUTE='ComputeBudget111111111111111111111111111111'

class PumpError(ValueError):pass
def fail(code):raise PumpError(code)
def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def atomic(path,value):sol.atomic(path,value)
def directory(home):p=sol.folder(home)/'pumpfun';p.mkdir(exist_ok=True);return p
def amount(value,decimals=9):
    if isinstance(value,bool):fail('invalid_amount')
    try:n=Decimal(str(value));scaled=n*(10**decimals)
    except (InvalidOperation,TypeError):fail('invalid_amount')
    if not n.is_finite() or n<=0 or scaled!=scaled.to_integral_value() or scaled>2**64-1:fail('invalid_amount_precision')
    return int(scaled)
def integer(n,low,high):
    if type(n) is not int or not low<=n<=high:fail('invalid_integer_limit')
    return n
def profile(home,name=None):
    p=sol.profile(home,name);p['rpc_url']=p.get('rpc_url') or sol.NETWORKS[p['network']]
    from urllib.parse import urlsplit
    u=urlsplit(p['rpc_url'])
    if u.scheme!='https' or not u.hostname or u.username or u.password or u.fragment:fail('https_rpc_required')
    return p
def scope(p):return digest({k:p[k] for k in ('name','account','network','rpc_url')})
def public_sdk(p,command,**args):
    node=shutil.which('node')
    if not node or not (SDK/'node_modules/@pump-fun/pump-sdk/package.json').is_file():fail('official_pump_sdk_missing')
    # Do not inherit wallet keys, model API keys, NODE_OPTIONS or arbitrary hooks.
    env={k:v for k,v in os.environ.items() if k.upper() in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP','USERPROFILE','LOCALAPPDATA','NODE_EXTRA_CA_CERTS')}
    env['NODE_USE_SYSTEM_CA']='1'
    request={'command':command,'network':p['network'],'rpc_url':p['rpc_url'],'account':p['account'],**args}
    try:r=subprocess.run([node,str(SDK/'worker.mjs')],input=json.dumps(request),capture_output=True,text=True,encoding='utf-8',timeout=50,env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except subprocess.TimeoutExpired:fail('public_sdk_timeout_no_transaction_sent')
    if r.returncode:fail('public_sdk_process_failed')
    try:value=json.loads(r.stdout)
    except ValueError:fail('public_sdk_invalid_result')
    if value.get('ok') is False:fail(value.get('code','public_sdk_failed'))
    return value

@contextmanager
def db(home):
    connection=sqlite3.connect(directory(home)/'state.sqlite',timeout=10);connection.row_factory=sqlite3.Row
    connection.execute('PRAGMA synchronous=FULL')
    connection.executescript('''CREATE TABLE IF NOT EXISTS quotes(id TEXT PRIMARY KEY,created REAL,body TEXT);
      CREATE TABLE IF NOT EXISTS observations(mint TEXT,at REAL,body TEXT,PRIMARY KEY(mint,at));
      CREATE TABLE IF NOT EXISTS grants(profile TEXT PRIMARY KEY,body TEXT);
      CREATE TABLE IF NOT EXISTS positions(id TEXT PRIMARY KEY,profile TEXT,mint TEXT,mode TEXT,state TEXT,body TEXT);
      CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,body TEXT);
      CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,time REAL,kind TEXT,body TEXT);
      CREATE TABLE IF NOT EXISTS settlements(id TEXT PRIMARY KEY,body TEXT);
      CREATE TABLE IF NOT EXISTS reservations(id TEXT PRIMARY KEY,grant_id TEXT,amount INTEGER);
      CREATE TABLE IF NOT EXISTS candidates(mint TEXT PRIMARY KEY,created REAL,body TEXT);''')
    connection.execute('CREATE TABLE IF NOT EXISTS market_events(id TEXT PRIMARY KEY,mint TEXT,time REAL,body TEXT)')
    try:
        with connection:yield connection
    finally:connection.close()
def get_state(home,key,default=None):
    with db(home) as d:r=d.execute('SELECT body FROM state WHERE key=?',(key,)).fetchone()
    return json.loads(r[0]) if r else default
def set_state(home,key,value):
    with db(home) as d:d.execute('INSERT OR REPLACE INTO state VALUES(?,?)',(key,json.dumps(value)))
def event(home,kind,value):
    from secret_output import redact
    with db(home) as d:d.execute('INSERT INTO events(time,kind,body) VALUES(?,?,?)',(time.time(),kind,redact(json.dumps(value))))
def status(home,name=None):
    p=profile(home,name)
    with db(home) as d:
        r=d.execute('SELECT body FROM grants WHERE profile=?',(p['name'],)).fetchone()
        positions=[json.loads(x[0]) for x in d.execute("SELECT body FROM positions WHERE state='open' AND profile=?",(p['name'],))]
    grant=json.loads(r[0]) if r else None
    valid=bool(grant and grant.get('active') and grant['expires']>time.time() and grant['scope']==scope(p))
    return {'profile':p['name'],'network':p['network'],'account':p['account'],'signer_configured':(sol.folder(home)/(p['name']+'.dpapi')).is_file(),
        'sdk':public_sdk(p,'status'),'grant_active':valid,'grant_expires':grant.get('expires') if grant else None,
        'limits':grant.get('policy') if grant else None,'positions':positions,'service':get_state(home,'service',{'state':'not_started'}),
        'protection':'client-side conditional exits, require running PC, fresh quotes, RPC and valid local grant; not guaranteed on-chain TP/SL',
        'withdrawal_tool':False,'onchain_spending_cap':False,'security':'Application checks; wallet key itself retains normal Solana authority. Same-user OS processes are not a sandbox.'}

def inspect(home,args):
    p=profile(home,args.get('profile'));mint=str(sol.pubkey(args.get('mint')))
    result=public_sdk(p,'inspect',mint=mint)
    base=int(result.get('pool_base_reserves') or result['virtual_token_reserves'])
    quote_reserve=int(result.get('pool_quote_reserves') or result['virtual_quote_reserves'])
    if result['venue']=='amm':quote_reserve+=int(result.get('pool_virtual_quote_reserves',0))
    if base>0 and quote_reserve>0:result['price_sol']=str(Decimal(quote_reserve)/Decimal(base)*(Decimal(10)**(result['decimals']-9)))
    result['profile']=p['name'];result['read_only']=True
    with db(home) as d:
        d.execute('INSERT OR REPLACE INTO observations VALUES(?,?,?)',(mint,time.time(),json.dumps(result)))
        d.execute('DELETE FROM observations WHERE mint=? AND at NOT IN (SELECT at FROM observations WHERE mint=? ORDER BY at DESC LIMIT 500)',(mint,mint))
    return result

def analyze(home,args):
    snapshot=inspect(home,args);mint=snapshot['mint'];blockers=[]
    if snapshot['venue']=='migrating':blockers.append('migration_pending')
    if snapshot.get('mayhem'):blockers.append('mayhem_mode_not_supported_for_automation')
    if snapshot['mint_authority'] or snapshot['freeze_authority']:blockers.append('mutable_mint_or_freeze_authority')
    if snapshot['unsupported_extensions']:blockers.append('unsupported_extensions')
    largest=snapshot.get('largest_token_accounts');top10=None
    if largest is None:blockers.append('holder_concentration_unverified')
    elif int(snapshot['supply']):top10=sum(int(x['amount']) for x in largest)*10000//int(snapshot['supply'])
    if top10 is not None and top10>8000:blockers.append('top_accounts_over_80_percent_of_supply')
    spaced=history_points(home,mint,snapshot['decimals'])
    if len(spaced)<30 or spaced[-1][0]-spaced[0][0]<300:blockers.append('insufficient_recorded_history')
    momentum=str((spaced[-1][1]/spaced[-5][1]-1)*100) if len(spaced)>=5 and spaced[-5][1]>0 else None
    return {'evidence_id':uuid.uuid4().hex,'snapshot':snapshot,'top10_token_account_share_bps':top10,'history_points':len(spaced),
        'momentum_last_five_pct':momentum,'blockers':blockers,'data_ready':not blockers,
        'score':max(0,100-25*len(blockers)),'score_meaning':'coverage/risk filter only, not a win probability or investment recommendation',
        'observations_source':'recorded confirmed on-chain observations; no synthetic candles'}

def history_points(home,mint,decimals):
    with db(home) as d:
        rows=d.execute('SELECT at,body FROM observations WHERE mint=? ORDER BY at',(mint,)).fetchall()
        events=d.execute('SELECT time,body FROM market_events WHERE mint=? ORDER BY time',(mint,)).fetchall()
    prices=[(r['at'],Decimal(json.loads(r['body'])['price_sol'])) for r in rows if json.loads(r['body']).get('price_sol')]
    for r in events:
        b=json.loads(r['body']);base=b.get('virtual_token_reserves');quote=b.get('virtual_quote_reserves')
        if b.get('kind')=='TradeEvent' and base and quote and int(base)>0:prices.append((r['time'],Decimal(quote)/Decimal(base)*(Decimal(10)**(decimals-9))))
    spaced=[]
    for point in sorted(prices):
        if point[1]>0 and (not spaced or point[0]-spaced[-1][0]>=10):spaced.append(point)
    return spaced[-500:]

def backtest(home,args):
    mint=str(sol.pubkey(args.get('mint')))
    with db(home) as d:last=d.execute('SELECT body FROM observations WHERE mint=? ORDER BY at DESC LIMIT 1',(mint,)).fetchone()
    if not last:fail('insufficient_recorded_history_no_backtest_invented')
    points=history_points(home,mint,json.loads(last[0])['decimals'])
    if len(points)<30 or points[-1][0]-points[0][0]<300:fail('insufficient_recorded_history_no_backtest_invented')
    cash=Decimal(1);qty=Decimal(0);entry=Decimal(0);cost=Decimal('.02');trades=[]
    for i,(timestamp,price) in enumerate(points):
        if i<8:continue
        # Signals use previous observations, execution at the next observation.
        previous=[p for _,p in points[i-8:i]];fast=sum(previous[-3:])/3;slow=sum(previous)/8
        if qty==0 and fast>slow:
            entry=cash;qty=cash*(1-cost)/price;cash=Decimal(0)
        elif qty and (qty*price>=entry*Decimal('1.5') or qty*price<=entry*Decimal('.75') or fast<slow):
            cash=qty*price*(1-cost);trades.append({'at':timestamp,'net_return_pct':str((cash/entry-1)*100)});qty=Decimal(0)
    nav=cash+(qty*points[-1][1]*(1-cost) if qty else 0)
    return {'mint':mint,'points':len(points),'span_seconds':points[-1][0]-points[0][0],'closed_trades':trades,'closed_trade_count':len(trades),
        'win_rate':str(Decimal(sum(Decimal(t['net_return_pct'])>0 for t in trades))/len(trades)) if trades else None,
        'final_virtual_nav_sol':str(nav),'initial_virtual_sol':'1','live_execution':False,
        'assumptions':'Historical illustrative momentum baseline, 2% cost EACH side, 50% target/25% stop; next-observation execution. No liquidity guarantee, parameter optimization or future return claim.'}
def discover(home,args):
    p=profile(home,args.get('profile'));value=public_sdk(p,'discover',limit=integer(args.get('limit',10),1,20),**({'before':args['before']} if args.get('before') else {}))
    with db(home) as d:
        for coin in value['coins']:d.execute('INSERT OR IGNORE INTO candidates VALUES(?,?,?)',(coin['mint'],time.time(),json.dumps(coin)))
        d.execute('DELETE FROM candidates WHERE created<?',(time.time()-3600,))
    return value

def quote(home,args):
    allowed={'profile','mint','side','amount_sol','token_amount','slippage_bps','priority_micro_lamports'}
    if set(args)-allowed:fail('unsupported_quote_fields')
    p=profile(home,args.get('profile'));mint=str(sol.pubkey(args.get('mint')));side=args.get('side')
    if side not in ('buy','sell'):fail('invalid_side')
    snapshot=public_sdk(p,'inspect',mint=mint)
    raw=amount(args.get('amount_sol'),9) if side=='buy' else amount(args.get('token_amount'),snapshot['decimals'])
    bps=integer(args.get('slippage_bps',100),1,500);priority=integer(args.get('priority_micro_lamports',0),0,1000000)
    value=public_sdk(p,'quote',mint=mint,side=side,amount=str(raw),slippage_bps=bps,priority_micro_lamports=priority)
    value['largest_token_accounts']=snapshot.get('largest_token_accounts');value['risk_data_error']=snapshot.get('risk_data_error')
    base=Decimal(value.get('pool_base_reserves') or value['virtual_token_reserves'])
    quote_reserve=Decimal(value.get('pool_quote_reserves') or value['virtual_quote_reserves'])
    if value['venue']=='amm':quote_reserve+=Decimal(value.get('pool_virtual_quote_reserves',0))
    if base>0 and quote_reserve>0:
        spot=quote_reserve/base;execution=Decimal(value['input_amount'])/Decimal(value['expected_output']) if side=='buy' else Decimal(value['expected_output'])/Decimal(value['input_amount'])
        value['impact_including_protocol_cost_bps']=max(0,int(((execution/spot-1) if side=='buy' else (1-execution/spot))*10000))
    value.update(profile=p['name'],scope=scope(p),created=time.time(),quote_id=uuid.uuid4().hex)
    # Keep large unsigned payload in native storage, not in the model context.
    with db(home) as d:d.execute('INSERT INTO quotes VALUES(?,?,?)',(value['quote_id'],value['created'],json.dumps(value)))
    with db(home) as d:d.execute('DELETE FROM quotes WHERE created<?',(time.time()-3600,))
    return summary(value)
def summary(value):
    return {k:v for k,v in value.items() if k not in ('unsigned_transaction','instructions','rpc_url')}
def load_quote(home,ident):
    if not isinstance(ident,str) or not __import__('re').fullmatch('[a-f0-9]{32}',ident):fail('native_quote_id_required')
    with db(home) as d:r=d.execute('SELECT body FROM quotes WHERE id=?',(ident,)).fetchone()
    if not r:fail('quote_not_found_use_pump_quote')
    value=json.loads(r[0])
    if not 0<=time.time()-value['created']<=60:fail('quote_expired_reanalyze_no_send')
    p=profile(home,value['profile'])
    if value['scope']!=scope(p):fail('profile_changed')
    return p,value

def _pda(seeds,program):return str(sol.pubkey(program).find_program_address(seeds,sol.pubkey(program))[0])
def associated(owner,mint,token):
    from solders.pubkey import Pubkey
    return str(Pubkey.find_program_address([bytes(sol.pubkey(owner)),bytes(sol.pubkey(token)),bytes(sol.pubkey(mint))],sol.pubkey(ATA))[0])
def validate_transaction(p,b):
    """Independent Python fence: exact SDK intent, one trade, no withdrawals/approvals."""
    from solders.transaction import VersionedTransaction
    from solders.pubkey import Pubkey
    try:tx=VersionedTransaction.from_bytes(base64.b64decode(b['unsigned_transaction'],validate=True))
    except Exception:fail('invalid_unsigned_transaction')
    m=tx.message;keys=[str(k) for k in m.account_keys]
    if m.header.num_required_signatures!=1 or keys[0]!=p['account'] or m.address_table_lookups or any(bytes(s)!=bytes(64) for s in tx.signatures):fail('unexpected_signer_or_lookup_table')
    mint=b['mint'];token=b['token_program'];user=p['account']
    curve=str(Pubkey.find_program_address([b'bonding-curve',bytes(sol.pubkey(mint))],sol.pubkey(PROGRAMS['curve']))[0])
    authority=Pubkey.find_program_address([b'pool-authority',bytes(sol.pubkey(mint))],sol.pubkey(PROGRAMS['curve']))[0]
    pool=str(Pubkey.find_program_address([b'pool',(0).to_bytes(2,'little'),bytes(authority),bytes(sol.pubkey(mint)),bytes(sol.pubkey(WSOL))],sol.pubkey(PROGRAMS['amm']))[0])
    wsol_ata=associated(user,WSOL,sol.TOKEN_PROGRAMS[0]);mint_ata=associated(user,mint,token)
    definitions={}
    for venue in PROGRAMS:
        idl=json.loads((SDK/'idl'/(venue+'.json')).read_text())
        for ix in idl['instructions']:definitions[(PROGRAMS[venue],bytes(ix['discriminator']))]=ix
    trades=0;compute_limit=0;compute_price=0;transferred=0;compute_seen=set()
    for ix in m.instructions:
        program=keys[ix.program_id_index];accounts=[keys[i] for i in ix.accounts];data=bytes(ix.data)
        if program in PROGRAMS.values():
            definition=definitions.get((program,data[:8]));name=definition['name'] if definition else ''
            if not definition:fail('program_instruction_not_allowed')
            named={a['name']:accounts[i] for i,a in enumerate(definition['accounts']) if i<len(accounts)}
            if named.get('user')!=user:fail('trade_user_mismatch')
            if name=='extend_account':
                if named.get('account') not in (curve,pool):fail('extend_unknown_account')
                continue
            if name=='init_user_volume_accumulator':continue
            expected_name=('buy_exact_quote_in_v3' if b['venue']=='curve' else 'buy_exact_quote_in_v2') if b['side']=='buy' else ('sell_v3' if b['venue']=='curve' else 'sell_v2')
            if name!=expected_name or program!=PROGRAMS[b['venue']]:fail('wrong_trade_instruction')
            if named.get('base_mint')!=mint or named.get('quote_mint') not in (WSOL,SYSTEM):fail('trade_mint_mismatch')
            if named.get('base_token_program')!=token or named.get('quote_token_program')!=sol.TOKEN_PROGRAMS[0]:fail('trade_token_program_mismatch')
            for key in ('associated_base_user','user_base_token_account'):
                if key in named and named[key]!=mint_ata:fail('foreign_trade_token_account')
            for key in ('associated_quote_user','user_quote_token_account'):
                if key in named and named[key]!=wsol_ata:fail('foreign_trade_quote_account')
            if named.get('bonding_curve',curve)!=curve or named.get('pool',pool)!=pool:fail('noncanonical_pump_route')
            if len(data)<24:fail('trade_bounds_missing')
            amount_in,minimum=struct.unpack_from('<QQ',data,8)
            if amount_in!=int(b['input_amount']) or minimum<int(b['minimum_output']):fail('trade_amount_or_slippage_mismatch')
            trades+=1
        elif program==COMPUTE:
            if data[:1]==b'\x02' and len(data)==5 and 2 not in compute_seen:compute_limit=struct.unpack_from('<I',data,1)[0];compute_seen.add(2)
            elif data[:1]==b'\x03' and len(data)==9 and 3 not in compute_seen:compute_price=struct.unpack_from('<Q',data,1)[0];compute_seen.add(3)
            else:fail('unexpected_compute_instruction')
        elif program==ATA:
            if data not in (b'',b'\x01') or len(accounts)<6 or accounts[0]!=user or accounts[2]!=user or (accounts[1],accounts[3],accounts[5]) not in ((mint_ata,mint,token),(wsol_ata,WSOL,sol.TOKEN_PROGRAMS[0])):fail('foreign_associated_account')
        elif program==SYSTEM:
            if len(data)!=12 or data[:4]!=b'\x02\x00\x00\x00' or accounts!=[user,wsol_ata]:fail('withdrawal_or_unknown_system_instruction')
            transferred+=struct.unpack_from('<Q',data,4)[0]
        elif program in sol.TOKEN_PROGRAMS:
            if data==b'\x11' and accounts==[wsol_ata]:pass
            elif data==b'\x09' and accounts==[wsol_ata,user,user]:pass
            else:fail('token_transfer_approval_or_unknown_instruction')
        else:fail('program_not_allowed')
    if trades!=1 or compute_limit!=300000 or compute_price!=b['priority_micro_lamports'] or transferred>(int(b['input_amount']) if b['side']=='buy' else 0):fail('transaction_scope_or_fee_mismatch')
    if str(m.recent_blockhash)!=b['blockhash']:fail('blockhash_changed')
    return tx

def prepare(home,args):
    if set(args)-{'quote_id','take_profit_pct','stop_loss_pct'}:fail('prepare_only_native_quote_id')
    p,q=load_quote(home,args.get('quote_id'));validate_transaction(p,q)
    if q['side']=='buy' and not {'take_profit_pct','stop_loss_pct'}<=set(args):fail('buy_requires_explicit_take_profit_and_stop_loss')
    tp=Decimal(str(args.get('take_profit_pct',50)));sl=Decimal(str(args.get('stop_loss_pct',25)))
    if not tp.is_finite() or not sl.is_finite() or not 0<tp<=1000 or not 0<sl<100:fail('invalid_protection_percent')
    if sol.query(p,'getGenesisHash',[])!=sol.GENESIS[p['network']]:fail('wrong_rpc_genesis')
    if q['side']=='buy' and (q['mint_authority'] or q['freeze_authority']):fail('mint_or_freeze_authority_present')
    if q['side']=='sell' and int(q['input_amount'])>int(q['token_balance']):fail('insufficient_token_balance')
    simulation=sol.simulate(home,{'profile':p['name'],'transaction':q['unsigned_transaction']})
    if simulation['simulation']['value']['err'] is not None:fail('simulation_rejected_no_send')
    # Share Solana operations with transfers, so uncertain transfers/swaps block each other.
    body={**q,'protocol':'pumpfun','take_profit_pct':str(tp),'stop_loss_pct':str(sl),'created_ms':int(time.time()*1000),
          'maximum_debit_lamports':(int(q['max_input']) if q['side']=='buy' else 0)+q['fee_lamports']+q['rent_buffer_lamports']}
    ident=q['quote_id']
    with sol.database(home) as d:
        old=d.execute('SELECT * FROM operations WHERE id=?',(ident,)).fetchone()
        if old:
            if digest(json.loads(old['body']))!=digest(body):
                # created_ms is receipt time, not a reason to recreate an intent.
                prior=json.loads(old['body']);body['created_ms']=prior['created_ms']
                if digest(prior)!=digest(body):fail('quote_already_prepared_different_protection')
            return {'id':ident,'state':old['state'],'reused':True,'sent':False}
        d.execute('INSERT INTO operations VALUES(?,?,?,?,?)',(ident,'prepared',json.dumps(body),None,None))
    return {'id':ident,'state':'prepared','preview':summary(body),'review_hash':digest(body),'simulation_passed':True,'sent':False,'local_review_or_grant_required':True}

def limits(settings):
    required={'max_trade_sol','max_total_sol','fee_reserve_sol','max_loss_sol','max_positions','max_trades','max_slippage_bps','duration_minutes'}
    if not required<=set(settings):fail('explicit_sol_risk_limits_required')
    out={k.replace('_sol','_lamports'):amount(settings[k]) for k in ('max_trade_sol','max_total_sol','fee_reserve_sol','max_loss_sol')}
    for k,low,high in [('max_positions',1,8),('max_trades',2,1000),('max_slippage_bps',1,500),('duration_minutes',1,1440)]:out[k]=integer(settings[k],low,high)
    if out['max_trade_lamports']>out['max_total_lamports']:fail('trade_exceeds_total_cap')
    out['mints']=[str(sol.pubkey(m)) for m in settings.get('mints',[])]
    out['allow_discovery']=settings.get('allow_discovery') is True
    out['max_network_fee_lamports']=integer(settings.get('max_network_fee_lamports',100000),5000,1000000)
    out['max_network_fee_bps']=integer(settings.get('max_network_fee_bps',500),1,5000)
    out['max_impact_bps']=integer(settings.get('max_impact_bps',500),1,5000)
    out['max_top10_account_bps']=integer(settings.get('max_top10_account_bps',3500),1,10000)
    out['max_top1_account_bps']=integer(settings.get('max_top1_account_bps',1200),1,10000)
    out['min_liquidity_lamports']=amount(settings.get('min_liquidity_sol','3'))
    if not out['mints'] and not out['allow_discovery']:fail('explicit_mints_or_discovery_scope_required')
    out['require_private_relay']=False
    if settings.get('require_private_relay'):fail('private_relay_not_implemented_do_not_claim_mev_protection')
    return out
def authorize_local(home,name,settings,*,persist_config=None):
    """Only local panel calls this after exact consent; no worker/agent IPC route."""
    p=profile(home,name);policy=limits(settings)
    if not (sol.folder(home)/(name+'.dpapi')).is_file():fail('existing_local_wallet_required')
    if sol.query(p,'getGenesisHash',[])!=sol.GENESIS[p['network']]:fail('wrong_rpc_genesis')
    baseline=sol.query(p,'getBalance',[p['account'],{'commitment':'confirmed'}])['value']
    if baseline<policy['fee_reserve_lamports']:fail('balance_below_fee_reserve')
    with sol.database(home) as d:
        for row in d.execute("SELECT body FROM operations WHERE state IN ('sending','uncertain','submitted')"):
            b=json.loads(row[0])
            if b.get('account')==p['account'] and b.get('network')==p['network']:fail('reconcile_pending_signature_before_authorization')
    now=time.time();grant={'id':uuid.uuid4().hex,'active':True,'scope':scope(p),'profile':name,'created':now,'expires':now+policy['duration_minutes']*60,
        'policy':policy,'baseline_lamports':baseline,'spent_lamports':0,'trades':0,'entry_stop':False}
    with db(home) as d:
        old=d.execute('SELECT body FROM grants WHERE profile=?',(name,)).fetchone()
        if old and json.loads(old[0]).get('active'):fail('stop_existing_pump_grant_before_reauthorizing')
        d.execute('INSERT OR REPLACE INTO grants VALUES(?,?)',(name,json.dumps(grant)))
        if settings.get('adopt_positions') is True:
            for row in d.execute("SELECT id,body FROM positions WHERE profile=? AND mode='live' AND state='open'",(name,)).fetchall():
                body=json.loads(row['body'])
                if body.get('network')!=p['network']:fail('position_scope_changed')
                body['grant_id']=grant['id'];d.execute('UPDATE positions SET body=? WHERE id=?',(json.dumps(body),row['id']))
        if persist_config:persist_config()
    return {'authorized':True,'profile':name,'network':p['network'],'expires':grant['expires'],'policy':policy,'service_started':False,'auto_renew':False,'secret_returned':False}
def stop(home,all_actions=False):
    with db(home) as d:
        for r in d.execute('SELECT profile,body FROM grants').fetchall():
            g=json.loads(r['body']);g['entry_stop']=True
            if all_actions:g['active']=False
            d.execute('UPDATE grants SET body=? WHERE profile=?',(json.dumps(g),r['profile']))
    atomic(directory(home)/('STOP' if all_actions else 'ENTRY_STOP'),{'at':time.time()})
    return {'new_entries_stopped':True,'all_actions_stopped':all_actions,'positions_closed':False,'funds_moved':False}
def checked_grant(home,p,b):
    with db(home) as d:r=d.execute('SELECT body FROM grants WHERE profile=?',(p['name'],)).fetchone()
    g=json.loads(r[0]) if r else None
    if not g or not g.get('active') or g['expires']<=time.time() or g['scope']!=scope(p) or (directory(home)/'STOP').exists():fail('local_pump_grant_required_or_expired')
    policy=g['policy']
    if b['fee_lamports']>policy['max_network_fee_lamports']:fail('network_fee_cap')
    denominator=int(b['input_amount'] if b['side']=='buy' else b['expected_output'])
    if b['fee_lamports']*10000>denominator*policy['max_network_fee_bps']:fail('network_fee_ratio_cap')
    with db(home) as d:reserved=d.execute('SELECT * FROM reservations WHERE id=?',(b['quote_id'],)).fetchone()
    if reserved and reserved['grant_id']!=g['id']:fail('intent_reserved_under_other_grant')
    prior=reserved['amount'] if reserved else 0
    if g['spent_lamports']-prior+b['maximum_debit_lamports']>policy['max_total_lamports']:fail('total_spend_cap_including_exit_fees')
    if g['trades']-bool(reserved)>=policy['max_trades']:fail('transaction_count_cap')
    if b['slippage_bps']>policy['max_slippage_bps']:fail('slippage_cap')
    if b['mint'] not in policy['mints'] and not policy['allow_discovery']:fail('mint_outside_local_grant')
    balance=sol.query(p,'getBalance',[p['account'],{'commitment':'confirmed'}])['value']
    if balance-b['maximum_debit_lamports']<policy['fee_reserve_lamports']:fail('fee_reserve_or_balance_cap')
    if b['side']=='buy':
        if b.get('largest_token_accounts') is None:fail('holder_concentration_unverified')
        supply=int(b['supply']);holders=[int(x['amount']) for x in b['largest_token_accounts']]
        if supply<=0 or sum(holders)*10000>supply*policy['max_top10_account_bps'] or max(holders,default=0)*10000>supply*policy['max_top1_account_bps']:fail('observed_account_concentration_cap')
        liquidity=int(b['pool_quote_reserves'])-int(b.get('pool_fee_buckets',0)) if b['venue']=='amm' else int(b.get('real_quote_reserves') or 0)
        if liquidity<policy['min_liquidity_lamports']:fail('real_liquidity_below_cap')
        if b.get('mayhem'):fail('mayhem_automation_not_allowed')
        if b.get('impact_including_protocol_cost_bps') is None or b['impact_including_protocol_cost_bps']>policy['max_impact_bps']:fail('price_impact_unverified_or_exceeds_cap')
        if g['entry_stop'] or (directory(home)/'ENTRY_STOP').exists():fail('new_entries_stopped')
        if g['baseline_lamports']-balance>=policy['max_loss_lamports']:fail('cash_drawdown_stop_requires_review')
        # Conservative cash drawdown counts deployed principal; never assumes an unsold token's paper value is cash.
        if b['maximum_debit_lamports']>policy['max_trade_lamports'] or g['spent_lamports']-prior+b['maximum_debit_lamports']>policy['max_total_lamports']:fail('spend_cap')
        with db(home) as d:positions=d.execute("SELECT mint FROM positions WHERE profile=? AND mode='live' AND state='open'",(p['name'],)).fetchall()
        if len(positions)>=policy['max_positions'] or b['mint'] in [x[0] for x in positions]:fail('position_or_duplicate_mint_cap')
        if policy['max_trades']-(g['trades']-bool(reserved))<len(positions)+2:fail('reserve_transaction_slots_for_exits')
        reserve=(len(positions)+1)*(b['fee_lamports']+b['rent_buffer_lamports'])
        if g['spent_lamports']-prior+b['maximum_debit_lamports']+reserve>policy['max_total_lamports']:fail('reserve_budget_for_exit_fees')
    else:
        with db(home) as d:r=d.execute("SELECT body FROM positions WHERE profile=? AND mint=? AND mode='live' AND state='open'",(p['name'],b['mint'])).fetchone()
        position=json.loads(r[0]) if r else None
        if not position or position.get('grant_id')!=g['id']:fail('position_not_owned_by_current_grant_use_local_review')
        if int(b['input_amount'])!=int(position['quantity']):fail('automatic_sell_must_close_managed_position_exactly')
    return g

def execute(home,ident,review_hash=None):
    """Exact GUI review OR existing native grant. Signature persisted BEFORE send."""
    from trading_runtime import singleton
    if os.getenv('FREELLMAPI_READ_ONLY')=='1':fail('read_only_signing_forbidden')
    with singleton(directory(home)/'execution.lock'):
        with sol.database(home) as d:
            d.execute('BEGIN IMMEDIATE');r=d.execute('SELECT * FROM operations WHERE id=?',(ident,)).fetchone()
            if not r:fail('native_operation_id_required')
            if r['state']!='prepared':return {'id':ident,'state':r['state'],'signature':r['signature'],'reused':True,'resent':False}
            b=json.loads(r['body'])
            if b.get('protocol')!='pumpfun':fail('not_a_pump_operation')
            p=profile(home,b['profile'])
            if b['scope']!=scope(p) or not 0<=time.time()-b['created']<=60:fail('stale_or_changed_preview')
            tx=validate_transaction(p,b)
            if review_hash is not None:
                if review_hash!=digest(b):fail('review_hash_mismatch')
                grant=None
            else:grant=checked_grant(home,p,b)
            if sol.query(p,'getGenesisHash',[])!=sol.GENESIS[p['network']]:fail('wrong_rpc_genesis')
            for pending in d.execute("SELECT body FROM operations WHERE state IN ('sending','uncertain','submitted')"):
                prior=json.loads(pending[0])
                if (prior.get('account'),prior.get('network'))==(p['account'],p['network']):fail('previous_signature_requires_reconciliation')
            if sol.query(p,'getBlockHeight',[{'commitment':'confirmed'}])>b['last_valid_block_height']:fail('blockhash_expired')
            sim=sol.simulate(home,{'profile':p['name'],'transaction':b['unsigned_transaction']})
            if sim['simulation']['value']['err'] is not None:fail('simulation_rejected_no_send')
            from hl_credentials import get
            from solders.keypair import Keypair
            from solders.transaction import VersionedTransaction
            wallet=Keypair.from_base58_string(get(sol.folder(home)/(p['name']+'.dpapi')))
            if str(wallet.pubkey())!=p['account']:fail('local_wallet_mismatch')
            if grant:
                fresh=checked_grant(home,p,b)
                if fresh['id']!=grant['id']:fail('grant_changed')
                with db(home) as ledger:
                    ledger.execute('BEGIN IMMEDIATE')
                    reserved=ledger.execute('SELECT * FROM reservations WHERE id=?',(ident,)).fetchone()
                    if not reserved:
                        ledger.execute('INSERT INTO reservations VALUES(?,?,?)',(ident,fresh['id'],b['maximum_debit_lamports']))
                        fresh['spent_lamports']+=b['maximum_debit_lamports'];fresh['trades']+=1
                    ledger.execute('UPDATE grants SET body=? WHERE profile=?',(json.dumps(fresh),p['name']))
            signed=VersionedTransaction(tx.message,[wallet]);signature=str(signed.signatures[0])
            if grant:checked_grant(home,p,b)
            if scope(profile(home,p['name']))!=scope(p):fail('profile_changed_before_send')
            b['grant_id']=grant['id'] if grant else None;b['authorization']='local_grant' if grant else 'exact_local_review'
            d.execute('UPDATE operations SET state=?,signature=?,body=? WHERE id=?',('sending',signature,json.dumps(b),ident));d.commit()
            try:
                observed=sol.query(p,'sendTransaction',[base64.b64encode(bytes(signed)).decode(),{'encoding':'base64','skipPreflight':False,'preflightCommitment':'confirmed','maxRetries':0}])
                if observed!=signature:fail('rpc_signature_mismatch')
                state='submitted'
            except Exception:state='uncertain'
            result={'id':ident,'state':state,'signature':signature,'confirmed':False,'resent':False,'instruction':'Reconcile signature read-only; never replace an unknown send.'}
            d.execute('UPDATE operations SET state=?,result=? WHERE id=?',(state,json.dumps(result),ident));d.commit();return result

def reconcile(home,ident):
    with sol.database(home) as d:
        r=d.execute('SELECT * FROM operations WHERE id=?',(ident,)).fetchone()
        if not r:fail('native_operation_id_required')
        b=json.loads(r['body'])
        if b.get('protocol')!='pumpfun':fail('not_a_pump_operation')
        if r['state'] in ('confirmed','failed'):return {**(json.loads(r['result']) if r['result'] else {}),'id':ident,'state':r['state'],'resent':False}
        if not r['signature']:return {'id':ident,'state':r['state'],'resent':False}
        p=profile(home,b['profile'])
        # Provider rotation invalidates signing authority/previews, not receipts.
        if (b['account'],b['network'])!=(p['account'],p['network']):fail('profile_changed_no_reconciliation_on_other_wallet_or_network')
        if sol.query(p,'getGenesisHash',[])!=sol.GENESIS[p['network']]:fail('wrong_rpc_genesis')
        status=sol.query(p,'getSignatureStatuses',[[r['signature']],{'searchTransactionHistory':True}])['value'][0]
        if not status:return {'id':ident,'state':r['state'],'signature':r['signature'],'resolved':False,'resent':False}
        if status.get('err') is not None:state='failed';result={'error':'on_chain_transaction_failed','resolved':True}
        elif status.get('confirmationStatus') not in ('confirmed','finalized'):return {'id':ident,'state':r['state'],'resolved':False,'resent':False}
        else:
            tx=sol.query(p,'getTransaction',[r['signature'],{'encoding':'jsonParsed','maxSupportedTransactionVersion':0,'commitment':'confirmed'}])
            if not tx or tx['meta'].get('err') is not None:fail('confirmed_metadata_unavailable_no_replay')
            def token_total(key):return sum(int(x['uiTokenAmount']['amount']) for x in tx['meta'].get(key,[]) if x.get('mint')==b['mint'] and x.get('owner')==p['account'])
            delta=token_total('postTokenBalances')-token_total('preTokenBalances')
            if (b['side']=='buy' and delta<=0) or (b['side']=='sell' and delta>=0):fail('confirmed_token_delta_mismatch')
            state='confirmed';result={'resolved':True,'token_delta':str(delta),'fee_lamports':tx['meta']['fee']}
            position={'id':ident,'profile':p['name'],'network':p['network'],'mint':b['mint'],'mode':'live','state':'open','quantity':str(delta),'grant_id':b.get('grant_id'),
                'cost_lamports':max(0,tx['meta']['preBalances'][0]-tx['meta']['postBalances'][0]),'take_profit_pct':b['take_profit_pct'],'stop_loss_pct':b['stop_loss_pct'],'entry_signature':r['signature'],'opened':time.time()}
            with db(home) as ledger:
                ledger.execute('BEGIN IMMEDIATE');settled=ledger.execute('SELECT body FROM settlements WHERE id=?',(ident,)).fetchone()
                if settled:result=json.loads(settled[0])
                elif b['side']=='buy':ledger.execute('INSERT OR IGNORE INTO positions VALUES(?,?,?,?,?,?)',(ident,p['name'],b['mint'],'live','open',json.dumps(position)))
                else:
                    old=ledger.execute("SELECT * FROM positions WHERE profile=? AND mint=? AND mode='live' AND state='open'",(p['name'],b['mint'])).fetchone()
                    if old:
                        position=json.loads(old['body']);position['quantity']=str(max(0,int(position['quantity'])+delta));position['state']='closed' if position['quantity']=='0' else 'open'
                        ledger.execute('UPDATE positions SET state=?,body=? WHERE id=?',(position['state'],json.dumps(position),old['id']))
                ledger.execute('INSERT OR IGNORE INTO settlements VALUES(?,?)',(ident,json.dumps(result)))
        result.update(id=ident,state=state,signature=r['signature'],resent=False)
        d.execute('UPDATE operations SET state=?,result=? WHERE id=?',(state,json.dumps(result),ident));return result

def operations(home):
    value=sol.operations(home)
    value['operations']=[{**x,'body':summary(x['body'])} for x in value['operations'] if x['body'].get('protocol')=='pumpfun']
    return value

def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--home',required=True);a=parser.parse_args()
    request={};args={}
    try:
        request=json.loads(sys.stdin.read(100000));args=request.get('arguments',{});command=request['command']
        actions={'status':lambda:status(a.home),'inspect':lambda:inspect(a.home,args),'discover':lambda:discover(a.home,args),'quote':lambda:quote(a.home,args),
            'prepare':lambda:prepare(a.home,args),'execute':lambda:execute(a.home,args.get('id')),'reconcile':lambda:reconcile(a.home,args.get('id')),'operations':lambda:operations(a.home),
            'analyze':lambda:analyze(a.home,args),'backtest':lambda:backtest(a.home,args),'service':lambda:__import__('pump_service').command(a.home,args)}
        if command not in actions:fail('private_operation_not_exposed')
        print(json.dumps(actions[command](),ensure_ascii=False))
    except Exception as e:
        code=str(e) if isinstance(e,PumpError) else 'pump_operation_failed'
        uncertain=False
        if request.get('command')=='execute':
            try:
                with sol.database(a.home) as d:r=d.execute('SELECT state FROM operations WHERE id=?',(args.get('id'),)).fetchone()
                uncertain=bool(r and r[0] in ('sending','uncertain','submitted','confirmed'))
            except Exception:uncertain=True
        print(json.dumps({'ok':False,'code':code,'error_type':type(e).__name__,'sent':None if uncertain else False,'retryable':False,'instruction':'Inspect native state; never retry an unknown send or alter risk limits.'}))
if __name__=='__main__':main()
