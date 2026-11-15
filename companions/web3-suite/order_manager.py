"""The single service entry lifecycle; only broker -> native submit may sign.

No model, exchange SDK or credential imports. Reconciliation never dispatches.
"""
import json,time
from trade_plan import TradePlan,PlanError


def submit_native(agent,home,ident):
    """Shared chat/service boundary, serialized per master+network across processes.

    Uses a separate OMS lock: the signer retains its own signing lock. Acquire
    before authorization; a busy OMS leaves the preview unsigned/prepared.
    """
    import hashlib
    from pathlib import Path
    from contextlib import ExitStack
    import hyperliquid_tools as h
    import os
    if os.getenv('FREELLMAPI_READ_ONLY')=='1':return h._submit_native(agent,home,ident)
    if getattr(agent,'mode','ask')=='plan' or getattr(agent,'depth',0):return h._submit_native(agent,home,ident)
    with h.SIGNER_LOCK:
        with h.database(home) as db:
            row=h.receipt(db.execute('SELECT * FROM actions WHERE id=?',(ident,)).fetchone())
            native_root=Path(db.execute('PRAGMA database_list').fetchone()[2]).parent
        if not row or row['state']!='prepared':return h._submit_native(agent,home,ident)
        body=row['body'];h.requested_coin(agent,body)
        if body.get('network') not in h.URLS or not isinstance(body.get('account'),str):h.problem('operation_scope_invalid','Scope della ricevuta incompleto: nessuna autorizzazione o firma. Consulta operations e prepara un nuovo preview valido.')
        h.address(body['account'])
        key=hashlib.sha256((body['network']+body['account'].lower()).encode()).hexdigest()[:24]
        from trading_runtime import singleton
        with ExitStack() as stack:
            try:stack.enter_context(singleton(native_root/('oms-'+key+'.lock')))
            except OSError:h.problem('oms_busy','Altra operazione gestita sullo stesso account/rete. Anteprima non autorizzata: consulta operations, senza nuovo intento o invio sostitutivo.')
            return h._submit_native(agent,home,ident)


class OrderManager:
    def __init__(self,engine):self.e=engine;self.store=engine.store

    def claim(self,ident,plan,validated,context):
        from trading_engine import TradingError,fingerprint
        with self.store.db() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT id FROM order_dispatches WHERE id=?',(ident,)).fetchone():raise TradingError('entry_already_managed_reconcile')
            row=db.execute("SELECT body FROM cycles WHERE id=? AND phase IN ('planned','triggering')",(ident,)).fetchone()
            if not row:raise TradingError('planned_or_triggered_cycle_required')
            body=json.loads(row[0])
            if body.get('policy_hash')!=fingerprint(self.e.config):raise TradingError('dispatch_policy_changed')
            body.update(plan=validated,typed_plan=plan.envelope(),**context)
            db.execute("UPDATE cycles SET phase='validated',updated=?,body=? WHERE id=?",(self.e.clock(),json.dumps(body),ident))
            db.execute("UPDATE entry_watches SET state='validated',updated=?,body=? WHERE id=?",(self.e.clock(),json.dumps(body),ident))
            db.execute('INSERT INTO order_dispatches VALUES(?,?,?,?,?,?)',(ident,fingerprint(plan.to_dict()),'validated',self.e.clock(),self.e.clock(),json.dumps(body)))
        self.store.event('oms_claimed',ident,{'plan_schema':plan.schema_version,'replayed':False})

    def dispatch(self,ident,plan,snapshot,account,pre_send=None,context=None):
        from trading_engine import TradingError,validate_plan
        with self.store.db() as db:
            if db.execute('SELECT id FROM order_dispatches WHERE id=?',(ident,)).fetchone():raise TradingError('entry_already_managed_reconcile')
        try:typed=TradePlan.parse(plan)
        except PlanError as exc:raise TradingError(str(exc)) from None
        if typed.decision!='enter':raise TradingError('entry_plan_required')
        validated=validate_plan(typed.to_dict(),snapshot,account,self.e.config,self.store,self.e.clock())
        wire={k:v for k,v in validated.items() if k!='notional'}
        typed=TradePlan.parse(wire)
        if set(context or {})-{'trigger_snapshot','trigger_account'}:raise TradingError('invalid_dispatch_context')
        self.claim(ident,typed,validated,context or {})
        self.e.hook('validated')
        prepared=self.e.broker.prepare(ident,validated) if hasattr(self.e.broker,'prepare') else None
        self.e.phase(ident,'prepared',prepared=prepared)
        if (self.store.path.parent/'STOP').exists():raise TradingError('service_stopped')
        if pre_send is not None:pre_send()
        self.e.phase(ident,'dispatching')
        receipt=self.e.broker.execute(ident,validated)
        if not isinstance(receipt,dict):receipt={'state':'uncertain','error':'invalid_broker_receipt'}
        self.e.phase(ident,'effect_observed',receipt=receipt)
        state=receipt.get('state')
        verified=self.live_receipt_verified(receipt)
        if verified:self.e.phase(ident,'done',receipt=receipt)
        elif state in ('blocked','rejected'):
            row=next(r for r in self.store.unfinished() if r['id']==ident)
            observation=self.e.broker.recover(row)
            if not observation.get('resolved'):
                self.store.set('paused','execution_requires_reconciliation');self.e.phase(ident,'paused',recovery=observation)
            else:self.e.phase(ident,'invalidated',receipt=receipt,result={'state':'invalidated','reason':'entry_blocked_or_rejected','resent':False})
        else:
            self.store.set('paused','uncertain_execution');self.e.phase(ident,'paused',receipt=receipt)
        self.store.set('next_due',self.e.clock()+self.e.config['interval'])
        return receipt

    @staticmethod
    def live_receipt_verified(receipt):
        from hyperliquid_protection import completion
        native=receipt.get('receipt') or {}
        return receipt.get('state') in ('accepted','filled') and completion(native.get('body',{}),native.get('response'))

    def reconcile(self,row,error=None):
        try:observation=self.e.broker.recover(row)
        except Exception:observation={'resolved':False,'resent':False,'error':'reconciliation_unavailable'}
        self.store.event('recovery_observation',row['id'],observation)
        if not observation.get('resolved'):
            self.store.set('paused','execution_requires_reconciliation');self.store.phase(row['id'],'paused',recovery=observation);return observation
        values={'recovery':observation}
        if error and not observation.get('receipt'):
            from secret_output import redact
            values['result']={'state':'hold','error':redact(str(error))[:800]}
        self.store.phase(row['id'],'done' if observation.get('receipt') else 'abandoned',**values)
        return observation
