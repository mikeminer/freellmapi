"""Resolve a fresh confirmation only against a provably UNSENT native plan."""
import hashlib,json,re,sqlite3
from pathlib import Path


def affirmative(text):
    return bool(re.fullmatch(r'\s*(?:s[iì]|yes|confermo|ok|okay|procedi|prosegui|continua|vai avanti)[.!\s]*',text or '',re.I))


def resolve(agent,prompt,home):
    if not affirmative(prompt) or getattr(agent,'depth',0) or getattr(agent,'hyperliquid_controller',None) or getattr(agent,'mode','ask')=='plan':return None
    from work_ledger import load
    active=load(agent.session).get('financial_request') or {}
    if active.get('state')!='blocked' or not active.get('id'):return None
    # Do not inherit consent through a topic change, a summary or model prose.
    users=[m['content'] for m in agent.messages if m.get('role')=='user' and isinstance(m.get('content'),str)]
    if users and users[-1]==prompt:users.pop()
    found=False
    for text in reversed(users[-6:]):
        if affirmative(text):continue
        found=text[:2000]==active.get('request');break
    if not found:return None
    import hyperliquid_tools as h
    if not (Path(home)/'hyperliquid/operations.db').is_file():return None
    p=h.profile(home);scope=h.fingerprint(p)
    ident=hashlib.sha256((str(Path(agent.session).resolve())+':'+active['id']+':'+scope).encode()).hexdigest()
    try:
        with h.database(home) as db:
            row=db.execute('SELECT * FROM chat_executions WHERE id=?',(ident,)).fetchone()
            if not row or row['state']!='blocked' or not row['operation_id'] or not row['result']:return None
            result=json.loads(row['result'])
            if result.get('sent') is not False or result.get('code')!='plan_price_moved':return None
            receipt=h.receipt(db.execute('SELECT * FROM actions WHERE id=?',(row['operation_id'],)).fetchone())
            if not receipt or receipt['state']!='prepared' or receipt['body'].get('action')!='order':return None
            if receipt['profile']!=p['name'] or receipt['body'].get('scope')!=scope:return None
            proposal=json.loads(row['proposal']);plan=proposal['plan']
            if plan.get('decision')!='enter' or plan.get('coin')!=receipt['body'].get('coin'):return None
    except (sqlite3.Error,ValueError,KeyError,TypeError):return None
    return {'venue':'hyperliquid','kind':'pair','pair':plan['coin']+'/USDC','coin':plan['coin'],'quote':'USDC',
            'trading':True,'execution_action':'order','confirmation_of':active['id'],'profile':p['name'],'scope':scope,
            'request_text':active['request'],'confirmed_proposal':proposal}
