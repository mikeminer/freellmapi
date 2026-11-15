"""Transactional organization sidecar. Never rewrites conversation/context files."""
import contextlib,sqlite3,time,uuid
from pathlib import Path

class Library:
    def __init__(self,home):self.path=Path(home)/'sessions/library.sqlite3'
    @contextlib.contextmanager
    def db(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        db=sqlite3.connect(self.path,timeout=8);db.row_factory=sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        db.executescript('''CREATE TABLE IF NOT EXISTS projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,root TEXT NOT NULL,created REAL NOT NULL,parent TEXT REFERENCES projects(id) ON DELETE SET NULL);
          CREATE TABLE IF NOT EXISTS chats(session TEXT PRIMARY KEY,title TEXT,project TEXT REFERENCES projects(id) ON DELETE SET NULL,deleted REAL);''')
        if 'parent' not in [r['name'] for r in db.execute('PRAGMA table_info(projects)')]:
            try:db.execute('ALTER TABLE projects ADD COLUMN parent TEXT REFERENCES projects(id) ON DELETE SET NULL')
            except sqlite3.OperationalError:
                if 'parent' not in [r['name'] for r in db.execute('PRAGMA table_info(projects)')]:raise
        try:
            with db:yield db
        finally:db.close()
    @staticmethod
    def name(value):
        from secret_output import redact
        value=str(value or '').strip()
        if not value or len(value)>120 or any(ord(c)<32 for c in value):raise ValueError('Nome: da 1 a 120 caratteri, su una sola riga.')
        return redact(value)
    def projects(self):
        if not self.path.exists():return []
        with self.db() as db:return [dict(row) for row in db.execute('SELECT * FROM projects ORDER BY created,id')]
    def create(self,name,root,parent=None):
        name=self.name(name);root=Path(root).expanduser().resolve()
        if not root.is_dir():raise ValueError('Scegli una cartella di lavoro esistente.')
        row={'id':uuid.uuid4().hex,'name':name,'root':str(root),'created':time.time(),'parent':parent or None}
        with self.db() as db:db.execute('INSERT INTO projects(id,name,root,created,parent) VALUES(:id,:name,:root,:created,:parent)',row)
        self.sync()
        return row
    def project(self,ident):
        with self.db() as db:row=db.execute('SELECT * FROM projects WHERE id=?',(ident,)).fetchone()
        if not row:raise ValueError('Progetto non trovato.')
        return dict(row)
    def edit_project(self,ident,name=None,delete=False):
        self.project(ident)
        with self.db() as db:
            if delete:db.execute('DELETE FROM projects WHERE id=?',(ident,))
            else:db.execute('UPDATE projects SET name=? WHERE id=?',(self.name(name),ident))
        self.sync()
    def move_project(self,ident,parent=None):
        with self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            rows={r['id']:dict(r) for r in db.execute('SELECT * FROM projects')}
            if ident not in rows or parent and parent not in rows:raise ValueError('Progetto non trovato.')
            current=parent;seen=set()
            while current:
                if current==ident or current in seen:raise ValueError('Un progetto non può contenere se stesso o un suo antenato.')
                seen.add(current);current=rows[current].get('parent')
            db.execute('UPDATE projects SET parent=? WHERE id=?',(parent or None,ident))
        self.sync()
    def chat(self,session,action,**values):
        from session_store import Sessions
        if not Sessions(self.path.parent.parent).path(session).is_file():raise ValueError('Conversazione non trovata.')
        if action not in ('rename','move','delete','restore'):raise ValueError('Azione chat sconosciuta.')
        project=values.get('project') or None
        if action=='move' and project:self.project(project)
        with self.db() as db:
            db.execute('INSERT OR IGNORE INTO chats(session) VALUES(?)',(session,))
            if action=='rename':db.execute('UPDATE chats SET title=? WHERE session=?',(self.name(values.get('name')),session))
            elif action=='move':db.execute('UPDATE chats SET project=? WHERE session=?',(project,session))
            elif action=='delete':db.execute('UPDATE chats SET deleted=? WHERE session=?',(time.time(),session))
            elif action=='restore':db.execute('UPDATE chats SET deleted=NULL WHERE session=?',(session,))
        self.sync()
    def move_many(self,sessions,project):
        from session_store import Sessions
        store=Sessions(self.path.parent.parent)
        if not isinstance(sessions,list) or not 1<=len(sessions)<=200:raise ValueError('Seleziona da una a 200 conversazioni.')
        for sid in sessions:
            if not store.path(sid).is_file():raise ValueError('Conversazione non trovata.')
        self.project(project)
        with self.db() as db:
            for sid in sessions:
                db.execute('INSERT INTO chats(session,project) VALUES(?,?) ON CONFLICT(session) DO UPDATE SET project=excluded.project',(sid,project))
        self.sync()
    def sync(self):
        from vault_library import sync
        self.last_sync=sync(self);return self.last_sync
    def overlay(self,rows,include_deleted=False):
        if not self.path.exists():return [dict(row) for row in rows]
        with self.db() as db:records={r['session']:dict(r) for r in db.execute('SELECT * FROM chats')}
        result=[]
        for source in rows:
            row=dict(source);extra=records.get(row['id'],{})
            row['project_id']=extra.get('project');row['deleted_at']=extra.get('deleted')
            if extra.get('title'):row['title']=extra['title']
            if include_deleted or not row['deleted_at']:result.append(row)
        return result
