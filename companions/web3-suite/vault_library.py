"""Derived Obsidian graph, scoped to generated notes. No history is moved."""
import hashlib,json,time,sqlite3
from pathlib import Path
from reliable_runtime import atomic_bytes
from secret_output import redact

BEGIN='<!-- freellmapi-library:begin -->';END='<!-- freellmapi-library:end -->'

def sync(library):
    home=library.path.parent.parent;status_path=home/'sessions/library-vault-sync.json'
    config=home/'config.json'
    if not config.exists():return {'state':'not_configured'}
    try:
        vault=Path(json.loads(config.read_text(encoding='utf-8-sig')).get('vault_path') or '')
        if not vault.is_absolute() or not vault.is_dir():return {'state':'not_configured'}
        memory=vault/'freellmapi-memory';base=memory/'FREELLMAPI Desktop';conflicts=[];written=0
        def link(path,label):return '[['+path.relative_to(vault).with_suffix('').as_posix()+'|'+str(label).replace('|',' ').replace(']','')+']]'
        def write(path,title,body):
            nonlocal written
            if not path.resolve().is_relative_to(vault.resolve()):raise ValueError('Generated note outside selected vault')
            managed=BEGIN+'\n# '+redact(title)+'\n\n'+body+'\n'+END
            if path.exists():
                old=path.read_text(encoding='utf-8')
                if BEGIN not in old or END not in old:conflicts.append(str(path.relative_to(vault)));return
                start=old.index(BEGIN);end=old.index(END,start)+len(END);text=old[:start]+managed+old[end:]
            else:text='---\nfreellmapi_library: true\naliases: '+json.dumps([redact(title)],ensure_ascii=False)+'\n---\n\n'+managed+'\n'
            path.parent.mkdir(parents=True,exist_ok=True)
            if not path.exists() or path.read_text(encoding='utf-8')!=text:atomic_bytes(path,text.encode('utf-8'));written+=1
        # Serialize graph snapshots across processes, including simultaneous DnD.
        with library.db() as db:
            db.execute('BEGIN IMMEDIATE')
            projects=[dict(r) for r in db.execute('SELECT * FROM projects ORDER BY created,id')]
            chats=[dict(r) for r in db.execute('SELECT * FROM chats')]
            byid={p['id']:p for p in projects}
            pp=lambda ident:base/'Projects'/(ident+'.md')
            cp=lambda ident:base/'Chats'/(ident+'.md')
            for project in projects:
                parent=byid.get(project.get('parent'));body='Struttura organizzativa; i contenuti delle chat rimangono nel loro contesto originale.\n\n'
                body+='Padre: '+(link(pp(parent['id']),parent['name']) if parent else link(base/'Index.md','Progetti'))+'\n\n'
                body+='## Sottoprogetti\n'+''.join('- '+link(pp(p['id']),p['name'])+'\n' for p in projects if p.get('parent')==project['id'])
                body+='\n## Conversazioni\n'
                for chat in chats:
                    if chat.get('project')!=project['id'] or chat.get('deleted'):continue
                    meta=home/'sessions/conversations'/(chat['session']+'.meta.json')
                    if not meta.exists():continue
                    data=json.loads(meta.read_text(encoding='utf-8'));body+='- '+link(cp(chat['session']),chat.get('title') or data.get('title') or 'Chat')+'\n'
                write(pp(project['id']),project['name'],body)
            for chat in chats:
                sid=chat['session'];source=home/'sessions/conversations'/(sid+'.json');meta=source.with_suffix('.meta.json')
                if not source.exists() or not meta.exists():continue
                data=json.loads(meta.read_text(encoding='utf-8'));title=chat.get('title') or data.get('title') or 'Chat';parent=byid.get(chat.get('project'))
                body='Sessione: `'+sid+'`\n\nStato: '+('nel cestino' if chat.get('deleted') else 'attiva nel catalogo')+'\n\n'
                body+='Progetto: '+(link(pp(parent['id']),parent['name']) if parent else link(base/'Index.md','Senza progetto'))+'\n\n'
                native=memory/'Projects'/(hashlib.sha256(str(Path(data['root']).resolve()).lower().encode()).hexdigest()[:16]+'.md')
                if native.exists():body+='Contesto nativo: '+link(native,'Memoria del workspace')+'\n\n'
                messages=json.loads(source.read_text(encoding='utf-8'));links=[]
                for i in range(max(0,len(messages)-80),len(messages)):
                    m=messages[i]
                    if m.get('role') not in ('user','assistant') or not m.get('content'):continue
                    raw=str(m['content']);ident=hashlib.sha256((str(source)+'|'+str(i)+'|'+m['role']+'|'+raw).encode()).hexdigest()[:24]
                    archive=memory/'Conversations'/(ident+'.md')
                    if archive.exists():links.append('- '+link(archive,'Turno '+str(i)+' · '+m['role']))
                body+='## Archivio conversazione\n'+'\n'.join(links)+'\n\nLo storico completo rimane nella sessione originale; questi sono collegamenti, non copie dei messaggi.\n'
                write(cp(sid),title,body)
            body='Indice dei raggruppamenti condivisi con FREELLMAPI Desktop.\n\n'
            for p in projects:
                if not p.get('parent'):body+='- '+link(pp(p['id']),p['name'])+'\n'
            ungrouped=[c for c in chats if not c.get('project') and not c.get('deleted')]
            if ungrouped:
                body+='\n## Conversazioni senza progetto\n'+''.join('- '+link(cp(c['session']),c.get('title') or c['session'])+'\n' for c in ungrouped)
            write(base/'Index.md','FREELLMAPI · progetti e conversazioni',body)
        result={'state':'conflict' if conflicts else 'synced','checked_ms':int(time.time()*1000),'written_notes':written,'conflicts':conflicts,'projects':len(projects),'chats':len(chats),'index':str(base/'Index.md')}
    except (OSError,ValueError,KeyError,sqlite3.Error) as error:result={'state':'unavailable','error_type':type(error).__name__,'retry':'sync_vault','history_moved':False}
    atomic_bytes(status_path,json.dumps(result,ensure_ascii=False,indent=2).encode('utf-8'));return result
