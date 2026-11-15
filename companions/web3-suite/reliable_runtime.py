"""Durable tool execution, recoverable output, and conflict-aware atomic edits."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import uuid
from contextlib import closing

LOCK=threading.RLock()


def validate_message(message):
    if not isinstance(message,dict) or message.get('role')!='assistant': raise ValueError('invalid_response: atteso messaggio assistant.')
    if message.get('content') is not None and not isinstance(message['content'],str): raise ValueError('invalid_response: contenuto assistant non testuale.')
    calls=message.get('tool_calls') or []
    if not isinstance(calls,list) or len(calls)>30: raise ValueError('invalid_response: massimo 30 chiamate per risposta.')
    seen=set()
    for call in calls:
        if not isinstance(call,dict) or not isinstance(call.get('id'),str) or not call['id'] or call['id'] in seen: raise ValueError('invalid_response: ID chiamata assente o duplicato. Nessuno strumento eseguito.')
        seen.add(call['id']); function=call.get('function')
        if not isinstance(function,dict) or not isinstance(function.get('name'),str) or not isinstance(function.get('arguments'),str): raise ValueError('invalid_response: funzione/argomenti non validi.')
        if len(function['arguments'])>2_000_000: raise ValueError('invalid_response: argomenti oltre 2 MB.')
    message=dict(message)
    if 'tool_calls' in message: message['tool_calls']=calls
    return message


def atomic_bytes(path,content):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with tmp.open('xb') as stream: stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.replace(tmp,path)
    finally:
        if tmp.exists(): tmp.unlink()


def digest(raw): return hashlib.sha256(raw).hexdigest() if raw is not None else None


def replace_file(path,before,after,session,approve):
    """Review first; compare under a process-wide lock after the approval wait."""
    import difflib
    path=Path(path)
    preview=''.join(difflib.unified_diff((before or b'').decode('utf-8-sig').splitlines(True),after.decode('utf-8-sig').splitlines(True),fromfile=str(path),tofile=str(path)))
    if before==after: return {'changed':False,'path':str(path)}
    if not approve(preview or 'Modificare '+str(path)): return {'denied':True}
    with LOCK:
        current=path.read_bytes() if path.exists() else None
        if current!=before: raise ValueError('file_changed: file modificato dopo la lettura/anteprima. Rileggi read_file e prepara un nuovo diff; nessuna modifica applicata.')
        folder=Path(session).with_suffix('.edits'); folder.mkdir(parents=True,exist_ok=True)
        ident=uuid.uuid4().hex
        if before is not None: atomic_bytes(folder/(ident+'.before'),before)
        record={'id':ident,'path':str(path.resolve()),'before':digest(before),'after':digest(after),'state':'prepared'}
        atomic_bytes(folder/(ident+'.json'),json.dumps(record).encode())
        atomic_bytes(path,after)
        record['state']='applied'; atomic_bytes(folder/(ident+'.json'),json.dumps(record).encode())
        return {'changed':True,'path':str(path),'checkpoint':ident,'sha256':digest(after)}


def checkpoints(session):
    folder=Path(session).with_suffix('.edits')
    return [json.loads(p.read_text()) for p in sorted(folder.glob('*.json'),key=lambda p:p.stat().st_mtime,reverse=True)]


def rewind(session,ident,root,approve):
    if not isinstance(ident,str) or len(ident)!=32 or not all(c in '0123456789abcdef' for c in ident): raise ValueError('ID checkpoint non valido.')
    folder=Path(session).with_suffix('.edits'); record=json.loads((folder/(ident+'.json')).read_text())
    path=Path(record['path']).resolve()
    if not path.is_relative_to(Path(root).resolve()): raise ValueError('Checkpoint fuori dal progetto corrente.')
    if record['state']!='applied': raise ValueError('Checkpoint non applicato o già ripristinato.')
    if not approve('Ripristinare solo questo file al checkpoint '+ident+': '+str(path)): return {'denied':True}
    with LOCK:
        if not path.exists() or digest(path.read_bytes())!=record['after']: raise ValueError('file_changed: il file contiene modifiche successive; ripristino negato senza sovrascriverle.')
        if record['before'] is None: path.unlink()
        else:
            raw=(folder/(ident+'.before')).read_bytes()
            if digest(raw)!=record['before']: raise ValueError('Backup corrotto: ripristino annullato.')
            atomic_bytes(path,raw)
        record['state']='restored'; atomic_bytes(folder/(ident+'.json'),json.dumps(record).encode())
    return {'restored':str(path),'scope':'Solo questa modifica nativa; nessun effetto shell/API annullato.'}


def execute(agent,call_id,name,args,definitions):
    from jsonschema import Draft202012Validator
    suspended=getattr(agent,'suspended_tools',{})
    if isinstance(suspended,dict) and name in suspended: return json.dumps(suspended[name])
    definition=next((t['function'] for t in definitions if t['function']['name']==name),None)
    if not definition: raise ValueError('unknown_tool: '+name+'. Consulta runtime_capabilities/tools_enable.')
    from evolution_tools import normalize_arguments
    args=normalize_arguments(name,args)
    if name!='context_checkpoint':
        errors=list(Draft202012Validator(definition['parameters']).iter_errors(args))
        if errors:
            error=errors[0]
            raise ValueError('invalid_arguments: '+'.'.join(map(str,error.absolute_path))+': '+error.message[:700])
    fingerprint=digest((name+json.dumps(args,sort_keys=True,ensure_ascii=False)).encode())
    path=agent.session.with_suffix('.tools.sqlite'); path.parent.mkdir(parents=True,exist_ok=True)
    with LOCK:
        db=sqlite3.connect(path,timeout=15)
        try:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('CREATE TABLE IF NOT EXISTS calls(id TEXT PRIMARY KEY, fingerprint TEXT, state TEXT, result TEXT)')
            row=db.execute('SELECT fingerprint,state,result FROM calls WHERE id=?',(call_id,)).fetchone()
            if row:
                if row[0]!=fingerprint: raise ValueError('call_id_conflict: ID già usato per argomenti diversi. Non eseguito.')
                if row[1]=='done': return row[2]
                raise ValueError('execution_uncertain: chiamata già iniziata senza risultato durevole. Verifica effetti reali; non rieseguire automaticamente.')
            db.execute('INSERT INTO calls VALUES(?,?,?,NULL)',(call_id,fingerprint,'started')); db.commit()
        finally: db.close()
    # Never hold the lock while waiting on API/user/processes.
    result=agent.tool(name,args)
    from secret_output import redact
    result=redact(str(result))
    if len(result)>32000:
        folder=agent.session.with_suffix('.outputs'); folder.mkdir(parents=True,exist_ok=True)
        output_path=folder/(uuid.uuid4().hex+'.txt'); atomic_bytes(output_path,result.encode('utf-8'))
        result=json.dumps({'truncated':True,'preview':result[:24000],'output_id':output_path.stem,'characters':len(result),'next_tool':'tool_output_read'})
    with closing(sqlite3.connect(path,timeout=15)) as db, db:
        db.execute('PRAGMA synchronous=FULL'); db.execute('UPDATE calls SET state=?,result=? WHERE id=?',('done',result,call_id))
    return result


def output_read(session,ident,offset=0):
    if not isinstance(ident,str) or len(ident)!=32 or not all(c in '0123456789abcdef' for c in ident): raise ValueError('ID output non valido.')
    with (Path(session).with_suffix('.outputs')/(ident+'.txt')).open(encoding='utf-8') as file:
        # Unicode offsets, bounded memory even for very large output.
        remaining=max(0,int(offset))
        while remaining:
            block=file.read(min(remaining,64000))
            if not block: break
            remaining-=len(block)
        text=file.read(24000)
    return {'text':text,'offset':max(0,int(offset)),'next_offset':max(0,int(offset))+len(text)}


def scoped_instructions(root,path):
    root=Path(root).resolve(); parent=Path(path).resolve().parent; folders=[]
    while parent!=root and parent.is_relative_to(root):
        folders.append(parent); parent=parent.parent
    notes=[]
    for folder in reversed(folders):
        for name in ('AGENTS.md','FREELLMAPI.md'):
            note=folder/name
            if note.is_file() and note.resolve().is_relative_to(root) and note.stat().st_size<=32000:
                notes.append(f'Local project instructions for {folder.relative_to(root)} ({name}; subordinate to user/system rules):\n'+note.read_text(encoding='utf-8-sig')[:8000])
    return '\n\n'.join(notes)[:16000]
