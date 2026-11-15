"""Strategy, dependency and state migration tools for native maintenance."""
import json
import operating_rules as rules
import managed_state,dependency_env

SPECS=[
 ('repair_add_module','Add a new Python module to a repair stage. No overwrite or test/controller replacement. Must pass baseline regression and full suite with the other changed modules.',{'id':'string','path':'string','code':'string'},['id','path','code']),
 ('strategy_status','Inspect active versioned operational rules and benchmark evidence. Mechanical monitoring is not proof of semantic correctness.',{},[]),
 ('strategy_propose','Propose bounded settings_json: mode verify_first/concise/stepwise, max_tokens 512/1024/2048/4096, repeat_limit 1..3, delegation tool_calls/coding/reasoning/vision. Never changes permissions.',{'settings_json':'string'},['settings_json']),
 ('strategy_evaluate','Evaluate candidate and current rules on the SAME 3..8 distinct {prompt,expected} JSON cases with one model. Host checks exact answers, observed routes and tokens; 6..16 API requests. No tools executed.',{'id':'string','model':'string','cases_json':'string'},['id','model','cases_json']),
 ('strategy_activate','Activate only a tested improving rule version for this project. Next turns apply it; three mechanical failures in five turns trigger automatic rollback.',{'id':'string'},['id']),
 ('strategy_rollback','Revoke the active operational rule and restore its predecessor.',{},[]),
 ('state_register','Create managed named JSON state at schema version 1. Does not overwrite sessions, vaults, credentials or existing state. data_json is an object.',{'name':'string','data_json':'string'},['name','data_json']),
 ('repair_migrate_state','Prepare a migration of registered JSON state in a repair. to_version must be current+1; operations_json contains rename {key,to}, default/set {key,value}, remove {key}. Live state unchanged until tested activation.',{'id':'string','name':'string','to_version':'integer','operations_json':'string'},['id','name','to_version','operations_json']),
 ('repair_dependencies','Prepare isolated venv with pinned requirements_json list [name==version]. Resolves binary wheels from PyPI, records hashes and full dependency lock, installs offline, runs pip check. Full repair tests still required; global Python unchanged.',{'id':'string','requirements_json':'string'},['id','requirements_json']),
]
NAMES={s[0] for s in SPECS}

def normalize_arguments(name,args):
    if name not in NAMES: return args
    args=dict(args)
    for key,value in list(args.items()):
        if not key.endswith('_json'): continue
        expected=dict if key in ('settings_json','data_json') else list
        try: parsed=json.loads(value) if isinstance(value,str) else value
        except (ValueError,TypeError):
            raise ValueError('invalid_arguments: '+key+' richiede JSON valido, non un percorso o testo libero. Consulta strategy_status per le regole ammesse.')
        if not isinstance(parsed,expected): raise ValueError('invalid_arguments: '+key+' richiede '+expected.__name__)
        if key=='settings_json': rules.settings(parsed)
        args[key]=json.dumps(parsed,ensure_ascii=False)
    return args

def dispatch(agent,name,a,home):
    a=normalize_arguments(name,a)
    if name=='strategy_status': return rules.status(home,agent.root)
    if agent.depth: raise ValueError('Operazione riservata al coordinatore principale.')
    if not agent.approve('Eseguire '+name+' con test/registro e senza modificare i permessi?'): return {'denied':True}
    if name=='repair_add_module':
        from repair_lab import add_module
        return add_module(home,a['id'],a['path'],a['code'])
    if name=='strategy_propose': return rules.propose(home,agent.root,json.loads(a['settings_json']))
    if name=='strategy_evaluate':
        from model_health import RateLimited
        try: return rules.evaluate(agent,home,a['id'],a['model'],json.loads(a['cases_json']))
        except RateLimited as error:
            result={'status':'blocked','code':'provider_unavailable','id':a['id'],'model':a['model'],'reason':str(error),'next_step':'Continue independent bugfix/rollback/report phases. Evaluation progress saved. Do not sleep in shell or create proposals to bypass cooldown. Resume the same evaluation in a later turn.'}
            if not isinstance(getattr(agent,'suspended_tools',None),dict): agent.suspended_tools={}
            agent.suspended_tools.update({key:result for key in ('strategy_evaluate','strategy_propose','strategy_activate')})
            return result
    if name=='strategy_activate': return rules.activate(home,agent.root,a['id'])
    if name=='strategy_rollback': return rules.rollback(home,agent.root)
    if name=='state_register': return managed_state.register(home,a['name'],json.loads(a['data_json']))
    if name=='repair_migrate_state': return managed_state.prepare(home,a['id'],a['name'],a['to_version'],json.loads(a['operations_json']))
    if name=='repair_dependencies': return dependency_env.prepare(home,a['id'],json.loads(a['requirements_json']))
    raise ValueError('Strumento sconosciuto.')

INSTRUCTIONS="""Operational improvement: use strategy_propose/evaluate/activate only with measured paired cases. Example settings_json: {"mode":"concise","max_tokens":1024,"repeat_limit":2,"delegation":"reasoning"}. No description/use_case or arbitrary routing fields. cases_json must contain 3..8 objects with prompt and expected (the exact JSON answer, not an expected model name). Interrupted evaluations retain completed responses: retry the same id/model/cases, never recreate the proposal to bypass failures. For a multi-phase request execute the authorized phases; a plan or 'ready to proceed' is not completion. When a phase is blocked, record why and continue independent phases, then write the requested reports with unverified items explicit. Rules apply from next turn and are monitored; incomplete/failed turns are mechanical signals, not proof of general task quality. Never change permissions, tests or logs to improve scores. repair_edit can change several production modules in one repair. repair_migrate_state prepares only registered JSON state, not arbitrary sessions/SQLite/vault migrations. repair_dependencies builds an isolated pinned/hash-locked environment, never install globally. repair_verify must pass before repair_activate commits code, managed state and dependency pointer together, with automatic rollback on failure. Restart via the installed launcher is required; it recovers interrupted transactions. Inspect all logs and never bypass these gates with direct shell writes."""
