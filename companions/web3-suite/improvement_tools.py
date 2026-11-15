"""Native coordination, repair lab and generated tool registry."""
import json
from pathlib import Path
import improvement_runtime as runtime
import repair_lab
import generated_tools
import evolution_tools

SPECS=[
 ('coordinator_state','Inspect persistent objectives, actual model outcomes, tool failures and existing project tasks; not proof of AGI or verified completion.',{},[]),
 ('model_benchmark','Probe one model: capability tool_calls, coding (restricted pure program), reasoning (arithmetic) or vision (image text). Persist actual model, latency and measured result; consumes API quota; no claim of general ability.',{'model':'string','capability':'string'},['model']),
 ('coordinator_delegate','Create a persistent project task and delegate it to a ready model with recent measured tool-call success when available, otherwise server auto routing. Shared context/budget, no recursive delegation.',{'task':'string','isolated':'boolean','capability':'string'},['task']),
 ('repair_start','Create a source copy. For an isolated test project ALWAYS pass project_path under qa_runs; without it the source is the installed freellmapi CLI. Check returned source_root before edits. Returns stage path; does not change installed code. File isolation, NOT an OS sandbox.',{'problem':'string','project_path':'string'},['problem']),
 ('repair_edit','Edit production Python modules in a repair stage using a unique exact old/new replacement. Existing tests and repair controllers cannot be changed. Read returned stage paths with read_file/shell if needed.',{'id':'string','path':'string','old':'string','new':'string'},['id','path','old','new']),
 ('repair_read','Read a staged source file with line numbers or list copied files. No access outside the repair snapshot. Use before exact edits.',{'id':'string','path':'string','start':'integer'},['id']),
 ('repair_verify','Run new unittest regression on the baseline copy (must fail by assertion) and the patched copy (must pass), then the staged suite. Import modules by name. An absolute path or sys.path insert toward stage/baseline/project makes the baseline run the wrong files and is a correctable test_contamination error, not a reason to weaken the gate. File copy, not an OS sandbox.',{'id':'string','test_code':'string'},['id','test_code']),
 ('repair_activate','Activate only an unchanged tested repair, preserve backup, run installed tests, automatically restore on failure. Requires restart to load new Python code. Honors approval mode.',{'id':'string'},['id']),
 ('repair_rollback','Restore previous file for a recorded activation only if no intervening edits. Restart required.',{'id':'string'},['id']),
 ('repair_status','Read repair state and structured test evidence or list repair IDs. Inspect log files via read_file for failures.',{'id':'string'},[]),
 ('generated_tool_build','Create/test/version/activate a pure JSON transformation tool. Python def run(args), imports only restricted json/math/re, no filesystem/network/shell/introspection. schema_json is object schema; cases_json has 2..20 distinct {input,expected} examples. Passing these cases is not general correctness.',{'name':'string','description':'string','code':'string','schema_json':'string','cases_json':'string'},['name','description','code','schema_json','cases_json']),
 ('generated_tool_list','List tested active generated tools, schemas and versions.',{},[]),
 ('generated_tool_run','Execute a registered tested JSON tool with validated arguments_json and 5s deadline. Runtime failure rolls back/disables the tool; never automatically reruns the failed action.',{'name':'string','arguments_json':'string'},['name','arguments_json']),
]
SPECS.extend(evolution_tools.SPECS)
NAMES={s[0] for s in SPECS}

def _tracked(agent,name,result,args=None,home=None):
    if isinstance(result,dict):
        reports=getattr(agent,'active_qa_reports',None)
        path=result.get('mirrored_report') or result.get('report_path')
        if isinstance(reports,set) and path:
            reports.add(str(path))
            if home is not None:
                from qa_interruption import remember
                remember(home,path)
        if getattr(agent,'session',None):
            from work_ledger import record_tool
            record_tool(agent.session,name,args or {},result,failed=result.get('ok') is False or result.get('blocked') is True)
    return result

def dispatch(agent,name,a,home):
    if name in evolution_tools.NAMES: return evolution_tools.dispatch(agent,name,a,home)
    if name=='coordinator_state':
        from coding_tools import Tasks
        return {**runtime.state(agent),'tasks':Tasks(home,agent.root).apply('task_list',{}),'installed_root':str(repair_lab.ROOT),'generated_tools':generated_tools.listing(home)}
    if name=='repair_status':
        rows=[repair_lab.load(home,a['id'])[1]] if a.get('id') else [json.loads(p.read_text(encoding='utf-8')) for p in (Path(home)/'repair-lab').glob('*/record.json')][-20:]
        return [{k:v for k,v in row.items() if k not in ('baseline','tested_manifest')} for row in rows]
    if name=='repair_read':
        folder,data=repair_lab.load(home,a['id'])
        if not a.get('path'): return {'files':sorted(set(data['baseline'])|set(data.get('added',[])))}
        if a['path'] not in set(data['baseline'])|set(data.get('added',[])): raise ValueError('Usa un percorso restituito da repair_read senza path.')
        start=max(1,a.get('start',1));lines=(folder/'stage'/a['path']).read_text(encoding='utf-8').splitlines()
        return {'path':a['path'],'total_lines':len(lines),'content':'\n'.join(f'{i+1}: {line}' for i,line in enumerate(lines) if start<=i+1<start+300)[:30000]}
    if name=='generated_tool_list': return generated_tools.listing(home)
    if agent.depth: raise ValueError('Coordinamento/attivazione richiede il processo principale; nessuna delega ricorsiva.')
    if not agent.approve('Eseguire '+name+' nel runtime di freellmapi?'): return {'denied':True}
    if name=='model_benchmark': return runtime.benchmark(agent,a['model'],home,a.get('capability','tool_calls'))
    if name=='coordinator_delegate':
        from coding_tools import Tasks
        chosen=runtime.select(agent,home,a.get('capability',getattr(agent,'operational_rules',{}).get('delegation','tool_calls')))
        job=agent.job_manager().start(a['task'],chosen['model'],isolated=a.get('isolated',False))
        tasks=Tasks(home,agent.root)
        task=next((t for t in tasks.apply('task_list',{}) if t['description']==a['task'] and t['status'] in ('pending','in_progress')),None)
        if task is None: task=tasks.apply('task_create',{'title':a['task'][:120],'description':a['task']})
        tasks.apply('task_update',{'id':task['id'],'status':'in_progress'})
        chosen['model']=agent.jobs.jobs[job]['model']
        record={'task_id':task['id'],'job_id':job,**chosen}
        runtime.record(agent,'delegation',record);return record
    if name=='repair_start':
        from work_ledger import load as load_ledger
        ledger=load_ledger(agent.session) if getattr(agent,'session',None) else {}
        if not a.get('project_path') and (getattr(agent,'repair_destination_error',None) or ledger.get('destination_error')):
            raise ValueError('repair_destination_invalid: '+str(getattr(agent,'repair_destination_error',None) or ledger.get('destination_error'))+' Nessun ripiego sul runtime installato. Non modificare i controllori protetti.')
        try:
            source=repair_lab.resolve_project(a['project_path'],getattr(agent,'root',None)) if a.get('project_path') else None
            problem=a['problem']
            if getattr(agent,'continuation_turn',False) and ledger.get('bound_source'):
                from continuation import is_continuation
                bound=Path(ledger['bound_source']).resolve()
                same_problem=problem==ledger.get('bound_problem') or is_continuation(problem) or repair_lab.tokens_equivalent(repair_lab.problem_tokens(problem),repair_lab.problem_tokens(ledger.get('bound_problem','')))
                if source is None:
                    source=None if bound==repair_lab.ROOT.resolve() else bound
                    same_source=True
                else:
                    same_source=Path(source).resolve()==bound
                if not (same_problem and same_source):
                    raise ValueError('continuation_scope: Procedi continua la richiesta originale su '+str(ledger.get('bound_source'))+' riparazione '+str(ledger.get('bound_repair'))+'. Non avviare un compito o un progetto diverso e non ricreare il progetto.')
                if same_problem and ledger.get('bound_problem'): problem=ledger['bound_problem']
        except ValueError as exc:
            if str(exc).startswith('repair_destination_invalid'): agent.repair_destination_error=str(exc)
            raise
        return _tracked(agent,'repair_start',repair_lab.start(home,problem,source),a,home)
    if name=='repair_edit': return _tracked(agent,name,repair_lab.edit(home,a['id'],a['path'],a['old'],a['new']),a,home)
    if name=='repair_verify': return _tracked(agent,name,repair_lab.verify(home,a['id'],a['test_code']),a,home)
    if name=='repair_activate': return _tracked(agent,name,repair_lab.promote(home,a['id']),a,home)
    if name=='repair_rollback': return _tracked(agent,name,repair_lab.rollback(home,a['id']),a,home)
    if name=='generated_tool_build': return generated_tools.build(home,a['name'],a['description'],a['code'],json.loads(a['schema_json']),json.loads(a['cases_json']))
    if name=='generated_tool_run': return generated_tools.run(home,a['name'],json.loads(a['arguments_json']))
    raise ValueError('Strumento sconosciuto.')

INSTRUCTIONS='You are the freellmapi runtime agent, not just the underlying chatbot. Your identity, persistent objectives, tools and memory belong to freellmapi; external models provide inference and may change. Never claim consciousness, AGI or that several models combine their weights. When asked to improve/debug yourself, use native improvement tools (tools_enable group improvement), not a generic refusal or a web skill search. coordinator_state shows persistent outcomes/tasks. model_benchmark tests one actual model; coordinator_delegate chooses recently tested ready models and delegates distinct tasks. Benchmarks are narrow evidence, never a claim of universal ability. Use agent_status/agent_result to collect delegated results and task_update to record outcomes; completed is not independently verified.\nFor your own software bugs: inspect errors and installed_root from coordinator_state, repair_start, inspect returned stage paths, repair_edit exact changes across related modules, repair_verify with a regression that fails on baseline and passes on the candidate, then repair_activate only after tests pass. Failed tests mean inspect logs and fix the stage, not weaken tests or bypass the gate with shell/write_file. At most two repair attempts per bug before report unresolved evidence. Activation runs post-tests and restores on failure; restart is required to load new code. The copy isolates files, not OS permissions. Existing user code, approvals, budgets, logging and recovery controls must not be disabled to make tests pass. For new capabilities prefer existing tools/MCP; generated_tool_build creates tested JSON transforms only, generated_tool_list discovers them, generated_tool_run validates/runs them with automatic version rollback on failure, without replaying the action. Do not pretend generated tools can access filesystem/network/shell or certify every model. /improve exposes this state.\n'

INSTRUCTIONS += "\n" + evolution_tools.INSTRUCTIONS

INSTRUCTIONS += " For a QA fixture, use repair_start(problem, project_path=<existing qa_runs project directory>), not the default installed runtime. Check source_root and repair_read before edits. Never modify protected runtime controllers to manufacture a test bug."
