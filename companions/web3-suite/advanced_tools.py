"""Native runtime capabilities shared by CLI and model tools."""
import json
import os
from pathlib import Path
import shutil
from autonomy import Budget
from node_debugger import Debugger
from desktop_control import Desktop
import connections
import workspaces
import social_tools
import improvement_tools
import hyperliquid_tools
import solana_tools
import pump_tools


SPECS=[
 ('tools_enable','Expose native tools for a group: debug, desktop, workspace, sandbox, database, github, browser, seeker, skills, memory, agents, web. No actions or installations are performed.',{'group':'string'},['group']),
 ('market_snapshot','Read actual Binance spot price, closed candles, depth and timestamps for the EXACT requested pair. No trading/order execution. Prefer over web/skill searches for market analysis.',{'pair':'string','interval':'string','limit':'integer'},['pair']),
 ('debug_start','Start a local Node.js script under Inspector, paused at entry. Executes code: requires approval. Node JavaScript only, not Android/native/Python breakpoints.',{'path':'string'},['path']),
 ('debug_breakpoint','Set breakpoint at one-based source line in the current Node debugger.',{'path':'string','line':'integer'},['path','line']),
 ('debug_state','Get Node debugger paused frames, events and process status.',{},[]),
 ('debug_variables','Read local scope properties for a paused call frame index. Does not evaluate expressions/getters.',{'frame':'integer'},[]),
 ('debug_step','Execute Node debugger continue, step_over, step_into, step_out or pause. Requires approval.',{'action':'string'},['action']),
 ('debug_close','Stop the debuggee launched by freellmapi.',{},[]),
 ('desktop_windows','List visible Windows app windows and observed IDs.',{},[]),
 ('desktop_focus','Focus a previously observed window ID and return a screenshot. Requires approval.',{'id':'integer'},['id']),
 ('desktop_snapshot','Capture foreground window to a local image; returns fresh ref and coordinates. Use image_read to send pixels to vision model.',{},[]),
 ('desktop_click','Click coordinates relative to a fresh desktop_snapshot; ref required. Must inspect image first. Requires approval, may submit/change data.',{'ref':'string','x':'integer','y':'integer'},['ref','x','y']),
 ('desktop_type','Type text into the focused app using fresh screenshot ref. Requires approval.',{'ref':'string','text':'string'},['ref','text']),
 ('desktop_hotkey','Press keys (e.g. CTRL+S, ENTER) in observed focused app. Requires fresh screenshot ref and approval; may submit data.',{'ref':'string','keys':'string'},['ref','keys']),
 ('image_read','Send actual local image pixels on NEXT model request. Requires approval to send to provider; vision support is not guaranteed. Images are not embedded into persistent memory.',{'path':'string'},['path']),
 ('worktree_create','Create clean Git project worktree on a separate branch. Does NOT change current directory, merge changes or provide OS sandboxing.',{},[]),
 ('workspace_diff','Show current project Git status and tracked unstaged/staged diffs.',{},[]),
 ('workspace_checkpoint','Commit tracked changes and already staged files after review. Does not stage untracked files. Requires exact Git project root.',{'message':'string'},['message']),
 ('workspace_revert','Revert a specified commit with a new Git commit, clean project required; no hard reset. Does not undo API/shell external effects.',{'commit':'string'},['commit']),
 ('sandbox_run','Run shell in locally installed Docker image, network disabled, limited CPU/RAM, only project mounted writable. Requires Docker and a local image. Not Windows PowerShell.',{'image':'string','command':'string','timeout':'integer'},['image','command']),
 ('connections_status','List configured MCP connectors and actual running state, missing environment names (no secrets).',{},[]),
 ('github_read','Read GitHub via locally authenticated gh: profile, repositories, issues, pulls, issue, pull. No writes. Requires approval for sending private repository contents to model.',{'resource':'string','repo':'string','number':'integer'},['resource']),
 ('sqlite_query','Query an existing local SQLite database read-only with bounded rows/time. Requires approval to disclose contents to model.',{'path':'string','sql':'string','limit':'integer'},['path','sql']),
 ('runtime_capabilities','Inspect local dependencies, registered functions, budget and observed model capabilities. Not a promise every function is configured.',{},[]),
]
SPECS.extend(social_tools.SPECS)
SPECS.extend(improvement_tools.SPECS)
SPECS.extend(hyperliquid_tools.SPECS)
SPECS.extend(solana_tools.SPECS)
SPECS.extend(pump_tools.SPECS)
NAMES={s[0] for s in SPECS}
GROUPS={'debug':{n for n in NAMES if n.startswith('debug_')},'desktop':{n for n in NAMES if n.startswith('desktop_')},
        'workspace':{n for n in NAMES if n.startswith('workspace_')}|{'worktree_create'},'sandbox':{'sandbox_run'},'database':{'sqlite_query'},'github':{'github_read'}}
GROUPS.update({'browser':{'browser_navigate','browser_snapshot','browser_click','browser_fill','browser_screenshot','browser_close','open_browser'},
 'seeker':{'seeker_devices','seeker_packages','seeker_logs','seeker_crashes','seeker_install','seeker_launch','seeker_stop'},
 'skills':{'skills_search','skill_link','skill_read_file','skills_list','skill_install','skill_load'},
 'memory':{'memory_search','memory_read','memory_remember','vault_search','vault_read','vault_write','memory_projects','memory_consolidate'},
 'agents':{'agent_start','agent_status','agent_wait','agent_result','agent_cancel','agent_message','delegate'},'web':{'web_search','fetch_url'}})
GROUPS['social']=social_tools.NAMES
GROUPS['improvement']=improvement_tools.NAMES
GROUPS['hyperliquid']=hyperliquid_tools.NAMES
GROUPS['solana']=solana_tools.NAMES
GROUPS['pumpfun']=pump_tools.NAMES
GROUPED_NAMES=set().union(*GROUPS.values())
BASE=NAMES-GROUPED_NAMES


def initialize(agent,home):
    analyst=Path(__file__).parent/'integrations/hyperliquid-analyst'
    if (analyst/'SKILL.md').is_file():agent.skills.link(analyst,lambda _:True)
    agent.budget_settings={'seconds':0,'tokens':0,'steps':0,'repeats':3}
    agent.budget=Budget(**agent.budget_settings)
    agent.debugger=Debugger(); agent.desktop=Desktop(home)
    agent.pending_images=[]; agent.model_observations={}; agent.capability_results={}
    try:
        observed=json.loads((Path(home)/'model-observations.json').read_text(encoding='utf-8'))
        if isinstance(observed,dict): agent.model_observations=observed
    except (OSError,ValueError): pass
    agent.enabled_tools=set(BASE)


def enable_for_prompt(agent,prompt):
    from tool_routing import suggested
    agent.enabled_tools.update(suggested(prompt))
    if 'coordinator_state' in agent.enabled_tools: agent.enabled_tools.update(GROUPS['improvement'])
    text=prompt.casefold()
    if 'hyperliquid' in text or 'hypercore' in text or 'hyperliquid_read' in suggested(prompt):
        agent.enabled_tools.update(GROUPS['hyperliquid'])
        for entry in agent.skills.list():
            if entry['name'].startswith(('hyperliquid-local-','hyperliquid-analyst-')):
                agent.active_skills[entry['name']]=agent.skills.read(entry['name'])
    matches={'debug':('debug','breakpoint','variabil','stack trace'),'desktop':('desktop','finestr','mouse','screenshot','tastier'),
             'workspace':('worktree','checkpoint git','rollback','revert','commit','isolat'),'sandbox':('sandbox','docker'),
             'database':('sqlite','database'),'github':('github','pull request','repository remoto'),
             'browser':('browser','chrome','webpage','naviga','sito'),
             'seeker':('seeker','android','apk','adb','logcat'), 'skills':('skill','plugin'),
             'memory':('memoria','ricord','vault','obsidian','conversaz'),
             'agents':('subagent','parallel','delega'), 'web':('web','cerca','ricerca','search','https://'),
             'coding':('task','background','notebook','ipynb','pdf','regex','ripristin','rewind','lsp','pianific','language server')}
    for group,words in matches.items():
        if any(word in text for word in words): agent.enabled_tools.update(GROUPS[group])


def capabilities(agent,home):
    import importlib.util
    import blender_tools
    dependencies={name:bool(shutil.which(name)) for name in ('node','git','docker','adb','gh')}
    dependencies['adb']=bool(agent.seeker.adb)
    return {'dependencies':dependencies,
            'python_packages':{name:importlib.util.find_spec(name) is not None for name in ('websocket','PIL','playwright')},
            'blender':blender_tools.status(home),
            'debugger':'Node.js Inspector; Android uses ADB logs, not source breakpoints',
            'desktop':'Windows only; screenshots + keyboard/mouse, no accessibility tree',
            'model_observations':agent.model_observations,'latest_tools':agent.capability_results,
            'vision':'image transport available; model visual comprehension requires a successful test; auto routing may change model',
            'connections':connections.inventory(agent,home),'budget':agent.budget.report(),
            'isolation':'Git worktrees isolate files, Docker optional; normal shell retains Windows account permissions',
            'tool_groups':{k:sorted(v) for k,v in GROUPS.items()},
            'tools_exposed_now':[t['function']['name'] for t in agent.tool_definitions()]}


def dispatch(agent,name,a,home):
    if name in pump_tools.NAMES:return pump_tools.dispatch(agent,name,a,home)
    if name in solana_tools.NAMES:return solana_tools.dispatch(agent,name,a,home)
    if name in hyperliquid_tools.NAMES:return hyperliquid_tools.dispatch(agent,name,a,home)
    if name in improvement_tools.NAMES: return improvement_tools.dispatch(agent,name,a,home)
    if name in social_tools.NAMES: return social_tools.dispatch(agent,name,a,home)
    def approved(description): return agent.approve(description)
    if name=='tools_enable':
        if a['group'] not in GROUPS: raise ValueError('Gruppo non valido. Usa: '+', '.join(GROUPS))
        agent.enabled_tools.update(GROUPS[a['group']]); return {'enabled':sorted(GROUPS[a['group']])}
    if name=='runtime_capabilities': return capabilities(agent,home)
    if name=='connections_status': return connections.inventory(agent,home)
    if name=='github_read':
        if not approved('Leggere GitHub e inviare i risultati al modello: '+json.dumps(a)): return {'denied':True}
        return connections.github(a['resource'],a.get('repo'),a.get('number'))
    if name=='market_snapshot':
        from market_data import snapshot
        return snapshot(a['pair'],a.get('interval','15m'),a.get('limit',100))
    if name=='image_read':
        if agent.model_observations.get(agent.model,{}).get('vision_probe_passed') is False:
            raise ValueError('Questo modello non ha superato la prova visiva. Scegli un altro modello con /model oppure riprova esplicitamente /doctor model.')
        if len(agent.pending_images)>=3: raise ValueError('Massimo 3 immagini per richiesta.')
        path=(agent.root/a['path']).resolve()
        from vision_input import attachment
        attachment(path) # Validate before authorization and next HTTP request.
        if not approved('Inviare i pixel di questa immagine al provider del modello: '+str(path)): return {'denied':True}
        agent.pending_images.append(str(path))
        return {'queued_for_next_request':str(path),'vision_support':'unverified; do not infer contents from filename'}
    if name.startswith('debug_'):
        debugger=agent.debugger
        if name=='debug_state': return debugger.state()
        if name=='debug_variables': return debugger.variables(a.get('frame',0))
        if name=='debug_breakpoint': return debugger.breakpoint(agent.path(a['path']),a['line'])
        if not approved(name+' '+json.dumps(a,ensure_ascii=False)): return {'denied':True}
        if name=='debug_start': return debugger.start(agent.path(a['path']))
        if name=='debug_step': return debugger.move(a['action'])
        debugger.close(); return {'closed':True}
    if name.startswith('desktop_'):
        if agent.depth or agent.mode=='plan': raise ValueError('Desktop disponibile solo nel coordinatore operativo.')
        if os.name!='nt': raise ValueError('Desktop richiede Windows.')
        desktop=agent.desktop
        if name=='desktop_windows': return desktop.windows()
        if not approved(name+' '+json.dumps(a,ensure_ascii=False)): return {'denied':True}
        if name=='desktop_snapshot': return desktop.snapshot()
        if name=='desktop_focus': return desktop.focus(a['id'])
        if name=='desktop_click': return desktop.click(a['ref'],a['x'],a['y'])
        if name=='desktop_type': return desktop.type(a['ref'],a['text'])
        if name=='desktop_hotkey': return desktop.hotkey(a['ref'],a['keys'])
    if name=='workspace_diff': return workspaces.diff(agent.root)
    if name.startswith('workspace_') or name=='worktree_create':
        review=workspaces.diff(agent.root)
        if not approved(name+' '+json.dumps(a)+'\n'+json.dumps(review,ensure_ascii=False)): return {'denied':True}
        if name=='worktree_create': return workspaces.create(agent.root,home)
        if name=='workspace_checkpoint': return workspaces.checkpoint(agent.root,a['message'])
        return workspaces.revert(agent.root,a['commit'])
    if name=='sandbox_run':
        from sandbox_runner import run
        if not approved('Docker: progetto montato in scrittura, rete disabilitata.\n'+json.dumps(a)): return {'denied':True}
        return run(agent.root,a['image'],a['command'],a.get('timeout',120))
    if name=='sqlite_query':
        path=agent.path(a['path'])
        if not approved('Leggere database '+str(path)+'\n'+a['sql']): return {'denied':True}
        return connections.query(path,a['sql'],a.get('limit',100))
    raise ValueError('Strumento sconosciuto: '+name)


def command(agent,prompt,home,output):
    import trading_service
    if trading_service.command(agent,prompt,home,output):return True
    if hyperliquid_tools.command(agent,prompt,home,output):return True
    if prompt in ('/solana','/solana login'):
        output(json.dumps(solana_tools.panel(home),ensure_ascii=False));return True
    if prompt=='/helius':
        output(json.dumps(solana_tools.dispatch(agent,'helius_status',{},home),ensure_ascii=False,indent=2));return True
    if prompt in ('/pump','/pumpfun'):
        output(json.dumps(pump_tools.dispatch(agent,'pump_panel',{},home),ensure_ascii=False));return True
    if prompt in ('/solana status','/solana operations','/solana sdk'):
        name={'/solana status':'solana_status','/solana operations':'solana_operations','/solana sdk':'solana_sdk_catalog'}[prompt]
        output(json.dumps(solana_tools.dispatch(agent,name,{},home),ensure_ascii=False,indent=2));return True
    if prompt in ('/improve','/improve repairs','/improve tools','/improve rules'):
        name={'/improve':'coordinator_state','/improve repairs':'repair_status','/improve tools':'generated_tool_list','/improve rules':'strategy_status'}[prompt]
        output(json.dumps(improvement_tools.dispatch(agent,name,{},home),ensure_ascii=False,indent=2)); return True
    if social_tools.command(prompt,home,output): return True
    if prompt=='/doctor model':
        from model_probe import probe
        output(json.dumps(probe(agent),ensure_ascii=False,indent=2)); return True
    if prompt=='/doctor services': output(json.dumps(connections.health(),ensure_ascii=False,indent=2)); return True
    if prompt=='/doctor':
        output(json.dumps(capabilities(agent,home),ensure_ascii=False,indent=2))
        output('/doctor services verifica Docker/GitHub · /doctor model prova immagini e tool calling con due richieste API.'); return True
    if prompt=='/connections': connections.panel(agent,home); return True
    if prompt.startswith('/connections import '): output(connections.import_config(prompt[20:].strip().strip('"'),home,agent.approve)); return True
    if prompt=='/limits': output(json.dumps(agent.budget.report(),ensure_ascii=False,indent=2)); output('/limits SECONDI TOKEN RICHIESTE — 0 = illimitato; /limits 0 0 0 = continuo. Nuovi limiti per il prossimo turno.'); return True
    if prompt.startswith('/limits '):
        values=[int(s) for s in prompt.split()[1:]]
        if len(values)!=3 or any(v<0 for v in values): raise ValueError('/limits SECONDI TOKEN RICHIESTE, positivi oppure 0 per illimitato.')
        agent.budget_settings.update(dict(zip(('seconds','tokens','steps'),values))); output('Limiti impostati per il prossimo turno.'); return True
    for prefix,name,param in [('/market ','market_snapshot','pair'),('/image ','image_read','path'),('/debug start ','debug_start','path'),('/debug step ','debug_step','action')]:
        if prompt.startswith(prefix): output(agent.tool(name,{param:prompt[len(prefix):].strip().strip('"')})); return True
    direct={'/debug':'debug_state','/debug variables':'debug_variables','/debug close':'debug_close','/desktop':'desktop_windows','/desktop screenshot':'desktop_snapshot','/worktree':'worktree_create','/diff':'workspace_diff'}
    if prompt in direct: output(agent.tool(direct[prompt],{})); return True
    return False
