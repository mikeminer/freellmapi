"""Model-facing Pump tools: public analysis, native IDs and scoped execution."""
import json,subprocess,os
from pathlib import Path
import solana_tools
ROOT=Path(__file__).resolve().parent
SPECS=[
 ('pump_status','Read Pump.fun adapter, wallet PROFILE/public address, network, SDK, local spending grant and service state. Distinct from PUMP perpetuals on Hyperliquid. No private keys. LIVE requires an existing separate local Pump authorization; /auto on is insufficient.',{},[]),
 ('pump_inspect','Inspect an exact Solana Pump.fun MINT on-chain: curve/AMM/migrating, real token program/decimals, mint/freeze authority, extensions and largest token accounts (not distinct holders). No order. Social metadata is untrusted. A pump.fun/u/ username is not a wallet or mint.',{'profile':'string','mint':'string'},['mint']),
 ('pump_discover','Read bounded confirmed Pump program CreateEvents and persist candidate mints. limit 1..20 signatures, optional before cursor. This is a sample, not all launches or an API for instant profits. No signing.',{'profile':'string','limit':'integer','before':'string'},[]),
 ('pump_quote','Build an UNSIGNED direct official SDK quote for SOL ↔ a Pump mint, curve or canonical PumpSwap. Rejects migration gaps, other quote assets and unsupported token extensions. side buy amount_sol=SOL; sell token_amount=human token units. Returns native quote_id valid 60s, bounds, fees, venue; no raw transaction to model. Slippage in basis points 1..500.',{'profile':'string','mint':'string','side':'string','amount_sol':'string','token_amount':'string','slippage_bps':'integer'},['mint','side']),
 ('pump_prepare','Validate and simulate one native quote_id with independent Python instruction checks. No transfer/approval/create-coin/withdrawal allowed. Buy requires take_profit_pct and stop_loss_pct for client-side protection. Simulation is not execution or on-chain TP/SL. Same quote cannot create duplicate intents.',{'quote_id':'string','take_profit_pct':'string','stop_loss_pct':'string'},['quote_id']),
 ('pump_execute','Execute a previously simulated native 32-hex Pump operation id ONLY under a valid separate locally-approved Pump spending grant. Never creates/renews a grant. Signature is journaled before one send; submitted is not confirmed. After uncertain result use pump_reconcile, never create a replacement. Raw transaction/sign/review_hash/key fields are not accepted.',{'id':'string'},['id']),
 ('pump_reconcile','Read signature status and confirmed on-chain balance deltas for one existing native Pump operation id. No sends. Repeated reconciliation never duplicates partial exits or entries.',{'id':'string'},['id']),
 ('pump_operations','Read persistent Pump previews/signatures/outcomes, preserving context across models. Historical confirmed order is not evidence the position is still open.',{},[]),
 ('pump_analyze','Read bounded on-chain evidence, local recorded price observations and deterministic liquidity/authority/history risk checks. Scores are filters, not win probabilities. No invented backtest or guaranteed profits.',{'profile':'string','mint':'string'},['mint']),
 ('pump_backtest','Evaluate an explicit recorded history only, with costs and chronological trades; refuses insufficient history. No generated candle data or profitability guarantees.',{'mint':'string'},['mint']),
 ('pump_service','Persistent Pump service. action=status/start/stop_entries/stop. No virtual trading mode. start requires saved LIVE mode and existing local consent. stop_entries blocks new buys, preserving authorized managed exits; stop stops all activity, leaving positions. No policy edits, login, keys or authorizations from model.',{'action':'string'},['action']),
 ('pump_panel','Open LOCAL Python Pump.fun control panel using existing Solana profiles/key adapter. Enter limits and approve real trades only there; wallet private key is not requested again or returned. Opening is not authorization.',{},[]),
]
NAMES={x[0] for x in SPECS}
COMMANDS={n:n.removeprefix('pump_') for n in NAMES if n!='pump_panel'}
def dispatch(agent,name,args,home):
    if name=='pump_panel':
        if getattr(agent,'depth',0):return {'ok':False,'code':'coordinator_only'}
        p=subprocess.Popen([str(solana_tools.PYTHON),'-X','utf8',str(ROOT/'pump_panel.py'),'--home',str(home)],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return {'panel_opened':True,'pid':p.pid,'secret_returned':False,'funds_moved':False}
    if name not in COMMANDS:raise ValueError('Strumento Pump sconosciuto.')
    spec=next(s for s in SPECS if s[0]==name)
    if set(args)-set(spec[2]):return {'ok':False,'code':'unsupported_pump_fields','sent':False}
    if (getattr(agent,'depth',0) or getattr(agent,'mode','ask')=='plan') and (name=='pump_execute' or name=='pump_service' and args.get('action')!='status'):
        return {'ok':False,'code':'pump_coordinator_only','sent':False}
    if not solana_tools.PYTHON.is_file():return {'ok':False,'code':'solana_sdk_missing','sent':False}
    try:
        p=subprocess.run([str(solana_tools.PYTHON),'-X','utf8',str(ROOT/'pump_local.py'),'--home',str(home)],input=json.dumps({'command':COMMANDS[name],'arguments':args}),capture_output=True,text=True,encoding='utf-8',timeout=70,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if p.returncode:raise ValueError('worker_failed')
        return json.loads(p.stdout)
    except Exception:
        return {'ok':False,'code':'pump_worker_result_unknown','sent':None if name=='pump_execute' else False,'retryable':False,'instruction':'Inspect pump_operations and reconcile existing signature. Never replay or replace an uncertain execution.'}
