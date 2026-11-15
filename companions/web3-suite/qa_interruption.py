"""Mark unfinished reports on interruption without inventing test outcomes."""
import ctypes,json,os
from pathlib import Path
from reliable_runtime import atomic_bytes

def _index_path(home):
    return Path(home)/'active-reports.json'

def _load_index(home):
    try: rows=json.loads(_index_path(home).read_text(encoding='utf-8'))
    except (OSError,ValueError): return []
    normalized=[]
    for row in rows if isinstance(rows,list) else []:
        if isinstance(row,str): normalized.append({'path':row,'pid':None})
        elif isinstance(row,dict) and isinstance(row.get('path'),str): normalized.append({'path':row['path'],'pid':row.get('pid') if isinstance(row.get('pid'),int) else None})
    return normalized

def _save_index(home,rows):
    atomic_bytes(_index_path(home),json.dumps(rows,ensure_ascii=False,indent=2).encode('utf-8'))

def _tracked_report(path):
    path=Path(path)
    if path.name.lower()!='results.json' or not any(part in path.parts for part in ('qa_runs','repair-lab')): return None
    try: return str(path.resolve())
    except OSError: return str(path)

def _process_alive(pid):
    """True when pid still has a process. Signal 0 is not used: on Windows it is not a harmless probe."""
    if not isinstance(pid,int) or pid<=0: return False
    if pid==os.getpid(): return True
    if os.name!='nt':
        try: os.kill(pid,0)
        except OSError: return False
        return True
    # use_last_error keeps the OpenProcess failure distinct from later ctypes calls.
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
    kernel.OpenProcess.restype=ctypes.c_void_p
    kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)]
    kernel.GetExitCodeProcess.restype=ctypes.c_int
    kernel.CloseHandle.argtypes=[ctypes.c_void_p]
    kernel.CloseHandle.restype=ctypes.c_int
    # PROCESS_QUERY_LIMITED_INFORMATION. A closed process can still be opened until its last handle is gone.
    handle=kernel.OpenProcess(0x1000,0,int(pid))
    if not handle: return ctypes.get_last_error()==5
    code=ctypes.c_uint32()
    ok=kernel.GetExitCodeProcess(handle,ctypes.byref(code))
    kernel.CloseHandle(handle)
    return bool(ok) and code.value==259

def remember(home,path):
    """Persist a report path before the next tool. An external kill skips the turn's finally."""
    text=_tracked_report(path)
    if text is None: return {'ok':False,'ignored':True}
    rows=_load_index(home)
    kept=[row for row in rows if row['path']!=text]
    kept.append({'path':text,'pid':os.getpid()})
    try: _save_index(home,kept)
    except OSError as exc: return {'ok':False,'error':'Indice report non scritto: '+str(exc)}
    return {'ok':True,'path':text,'pid':os.getpid()}

def forget(home,path):
    text=_tracked_report(path) or str(path)
    rows=_load_index(home)
    kept=[row for row in rows if row['path']!=text]
    if len(kept)==len(rows): return {'ok':True,'unchanged':True}
    try: _save_index(home,kept)
    except OSError as exc: return {'ok':False,'error':'Indice report non aggiornato: '+str(exc)}
    return {'ok':True}

def recover_reports(home,reason='Processo precedente terminato prima della chiusura del turno.'):
    """Finalize reports whose owner process is gone. A live owner is left untouched."""
    index=_index_path(home)
    if not index.exists(): return {'ok':True,'recovered':[],'remaining':[]}
    recovered=[]; kept=[]
    for row in _load_index(home):
        pid=row.get('pid')
        if isinstance(pid,int) and pid!=os.getpid() and _process_alive(pid):
            kept.append(row); continue
        result=finalize(row['path'],reason)
        if not isinstance(result,dict) or result.get('error'):
            kept.append(row)
            recovered.append({'path':row['path'],'ok':False,'error':None if not isinstance(result,dict) else result.get('error')})
        else:
            recovered.append({'path':row['path'],'ok':True,'status':result.get('status'),'unchanged':bool(result.get('unchanged'))})
    try: _save_index(home,kept)
    except OSError as exc: return {'ok':False,'error':'Indice report non aggiornato: '+str(exc),'recovered':recovered,'remaining':kept}
    return {'ok':True,'recovered':recovered,'remaining':kept}

def finalize(path,reason):
    path=Path(path)
    if path.name.lower()!='results.json' or not any(part in path.parts for part in ('qa_runs','repair-lab')):
        return {'ok':False,'ignored':True}
    if not path.exists():
        report={'status':'INTERRUPTED','api_requests':None,'provider_tokens':None,'phases':{'report':{'status':'NOT_VERIFIED','reason':'Turno terminato senza results.json strutturato. Nessun PASS inventato.'}},'interruption':{'reason':reason,'unverified_phases':['report'],'source':'runtime; not a model assessment'}}
        try:
            path.parent.mkdir(parents=True,exist_ok=True)
            atomic_bytes(path,json.dumps(report,ensure_ascii=False,indent=2).encode('utf-8'))
            md=path.parent/'REPORT.md'
            previous=md.read_text(encoding='utf-8') if md.exists() else '# Report\n'
            atomic_bytes(md,(previous+'\n\n## Interruzione registrata dal runtime\n'+reason+'\nresults.json assente: creato uno stato INTERRUPTED. Le righe PASS precedenti, se presenti, non sono prove. Nessun successo aggiunto.\n').encode('utf-8'))
        except OSError as exc:
            return {'ok':False,'error':'Scrittura report fallita: '+str(exc)}
        return {'ok':True,'created':True,'status':'INTERRUPTED'}
    try: report=json.loads(path.read_text(encoding='utf-8'))
    except (OSError,ValueError) as exc:
        return {'ok':False,'error':'Report illeggibile: '+str(exc)}
    phases=report.get('phases')
    if not isinstance(phases,(dict,list)):
        return {'ok':False,'error':'Report senza fasi verificabili: '+str(path)}
    pending=[]
    for name,row in (phases.items() if isinstance(phases,dict) else ((str(i),row) for i,row in enumerate(phases))):
        if isinstance(row,dict) and row.get('status') in ('NOT_RUN','IN_PROGRESS'):
            row.update(status='NOT_VERIFIED',reason='Turno interrotto: '+reason);pending.append(name)
    if not pending and report.get('status') not in ('IN_PROGRESS','NOT_RUN'):
        return {'ok':True,'unchanged':True,'status':report.get('status')}
    report.update(status='INTERRUPTED',interruption={'reason':reason,'unverified_phases':pending,'source':'runtime; not a model assessment'})
    if report.get('api_requests')==0 and not report.get('request_count_verified'):
        report['api_requests']=None;report['api_requests_note']='Contatore del report non misurato; consultare il registro sessione.'
    try:
        from qa_evidence import validate
        checked=validate(path) if path.is_file() else {'ok':False,'errors':['file assente']}
    except (OSError,ValueError,TypeError) as exc:
        checked={'ok':False,'errors':[str(exc)]}
    if not checked.get('ok'): report['report_validation_errors']=checked.get('errors')
    try:
        atomic_bytes(path,json.dumps(report,ensure_ascii=False,indent=2).encode('utf-8'))
        md=path.parent/'REPORT.md'
        previous=md.read_text(encoding='utf-8') if md.exists() else '# Report\n'
        atomic_bytes(md,(previous+'\n\n## Interruzione registrata dal runtime\n'+reason+'\nFasi non verificate: '+', '.join(pending)+'. Nessun successo aggiunto.\n').encode('utf-8'))
    except OSError as exc:
        return {'ok':False,'error':'Scrittura report fallita: '+str(exc)}
    return {'ok':True,'status':'INTERRUPTED','validation_errors':report.get('report_validation_errors')}
