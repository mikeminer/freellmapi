"""Non-destructive local history import; never keys, grants or financial sends."""
import hashlib,json,re,sqlite3,uuid
from pathlib import Path
from reliable_runtime import atomic_bytes

def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def merge_sessions(source,target,archive):
    source=Path(source)/'sessions/conversations';target=Path(target)/'sessions/conversations';archive=Path(archive)
    target.mkdir(parents=True,exist_ok=True);archive.mkdir(parents=True,exist_ok=True);result=[]
    for path in sorted(source.glob('*.json')):
        if not re.fullmatch('[a-f0-9]{32}',path.stem):continue
        sid=path.stem;destination=target/path.name;files=[p for p in source.glob(sid+'.*') if p.is_file()]
        # Keep a byte-identical source archive, including superseded sidecars.
        for part in files:
            saved=archive/part.name
            if saved.exists() and sha(saved)!=sha(part):raise ValueError('Source changed after snapshot: '+part.name)
            if not saved.exists():atomic_bytes(saved,part.read_bytes())
        conflict=destination.exists() and sha(destination)!=sha(path)
        for suffix in ('.work.json','.answers.json'):
            old=source/(sid+suffix);new=target/(sid+suffix)
            if old.exists() and new.exists() and sha(old)!=sha(new):conflict=True
        selected=uuid.uuid5(uuid.NAMESPACE_URL,str(source)+':'+sid+':'+sha(path)).hex if conflict else sid
        for part in files:
            new=target/(selected+part.name[len(sid):]);raw=part.read_bytes()
            if part.name.endswith('.meta.json') and selected!=sid:
                metadata=json.loads(raw);metadata.update(id=selected,title='CMD · '+metadata.get('title','Conversazione importata'))
                raw=json.dumps(metadata,ensure_ascii=False,indent=2).encode('utf-8')
            if part.name.endswith('.schedule.json'):
                schedules=json.loads(raw)
                for row in schedules.values():
                    if row.get('enabled'):row.update(enabled=False,state='migration_suspended_no_replay')
                raw=json.dumps(schedules,ensure_ascii=False,indent=2).encode('utf-8')
            if not new.exists():atomic_bytes(new,raw)
            elif new.read_bytes()!=raw and part==path:raise ValueError('Conversation conflict: '+selected)
        assert sha(target/(selected+'.json'))==sha(path)
        result.append({'source':sid,'selected':selected,'messages':len(json.loads(path.read_text(encoding='utf-8'))),'forked':conflict,'conversation_sha256':sha(path)})
    return result

def merge_receipts(source_database,home):
    """Import immutable historical rows only. Existing rows win; grants never import."""
    import hyperliquid_tools as h
    source=sqlite3.connect(Path(source_database).resolve().as_uri()+'?mode=ro',uri=True);source.row_factory=sqlite3.Row
    try:
        rows=[dict(row) for row in source.execute('SELECT * FROM actions ORDER BY id')]
        if any(r['state'] in ('authorized','sending','uncertain','partial') for r in rows):raise ValueError('Source has unresolved financial effects: reconcile before alignment')
        tables={r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        bindings=[dict(r) for r in source.execute('SELECT * FROM chat_executions')] if 'chat_executions' in tables else []
    finally:source.close()
    counts={'actions':0,'bindings':0,'grants':0,'orders_sent':0}
    with h.database(home) as db:
        db.execute('CREATE TABLE IF NOT EXISTS chat_executions(id TEXT PRIMARY KEY,proposal TEXT,operation_id TEXT,state TEXT,result TEXT,created REAL)')
        db.execute('BEGIN IMMEDIATE')
        try:
            for row in rows:
                old=db.execute('SELECT * FROM actions WHERE id=?',(row['id'],)).fetchone()
                if old:
                    if old['intent']!=row['intent'] or json.loads(old['body'])!=json.loads(row['body']):raise ValueError('Conflicting immutable receipt: '+row['id'])
                    continue
                columns=['id','intent','profile','state','created','updated','body','response']
                db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?)',tuple(row[k] for k in columns));counts['actions']+=1
            for row in bindings:
                if db.execute('SELECT id FROM chat_executions WHERE id=?',(row['id'],)).fetchone():continue
                columns=['id','proposal','operation_id','state','result','created']
                db.execute('INSERT INTO chat_executions VALUES(?,?,?,?,?,?)',tuple(row[k] for k in columns));counts['bindings']+=1
            db.commit()
        except Exception:db.rollback();raise
    return counts

def publish_binding(path,home,aliases):
    home=Path(home)
    if not home.is_absolute() or not (home/'config.json').is_file():raise ValueError('Existing canonical configuration required')
    if not aliases or any(not Path(p).is_absolute() for p in aliases):raise ValueError('Absolute previous namespaces required')
    value={'version':1,'home':str(home),'redirect_from':[str(Path(p)) for p in aliases]}
    atomic_bytes(Path(path),json.dumps(value,indent=2).encode('utf-8'));return value
