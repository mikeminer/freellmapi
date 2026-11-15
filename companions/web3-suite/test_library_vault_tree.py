import hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from chat_library import Library
from session_store import Sessions
from memory_vault import Vault
from memory_catalog import NoteCatalog
from remote_client import RemoteClient
from desktop_backend import DesktopBackend

class VaultTreeTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.home=Path(temp.name)/'home';self.home.mkdir()
        self.vault=Path(temp.name)/'vault';self.vault.mkdir();(self.home/'config.json').write_text(json.dumps({'vault_path':str(self.vault)}))
        self.store=Sessions(self.home);self.sid=self.store.create(self.home,[{'role':'user','content':'Scelta ETH conservata'}]);self.session=self.store.path(self.sid);self.original=self.session.read_bytes()
        self.memory=Vault(self.vault/'freellmapi-memory',self.home);self.memory.record(json.loads(self.original),self.session)
        self.archive={p.name:p.read_bytes() for p in (self.memory.path/'Conversations').glob('*.md')}
        self.library=Library(self.home);self.base=self.memory.path/'FREELLMAPI Desktop'
    def test_tree_moves_generate_real_bidirectional_links_without_rewriting_history(self):
        root=self.library.create('Trading',self.home);child=self.library.create('Solana',self.home,parent=root['id']);self.library.chat(self.sid,'move',project=child['id'])
        parent_note=self.base/'Projects'/(root['id']+'.md');child_note=self.base/'Projects'/(child['id']+'.md');chat_note=self.base/'Chats'/(self.sid+'.md')
        self.assertIn(child['id'],parent_note.read_text(encoding='utf-8'));self.assertIn(root['id'],child_note.read_text(encoding='utf-8'));self.assertIn(child['id'],chat_note.read_text(encoding='utf-8'))
        self.assertTrue(any(ident[:-3] in chat_note.read_text(encoding='utf-8') for ident in self.archive))
        child_note.write_text(child_note.read_text(encoding='utf-8')+'\nAnnotazione manuale da conservare.\n',encoding='utf-8')
        self.library.edit_project(child['id'],name='Solana e Pump.fun');self.assertIn('Annotazione manuale',child_note.read_text(encoding='utf-8'));self.assertIn('Solana e Pump.fun',parent_note.read_text(encoding='utf-8'))
        self.library.move_project(child['id'],None);self.assertIsNone(self.library.project(child['id'])['parent'])
        self.library.chat(self.sid,'delete');self.library.chat(self.sid,'restore')
        self.assertEqual(self.original,self.session.read_bytes());self.assertEqual(self.archive,{p.name:p.read_bytes() for p in (self.memory.path/'Conversations').glob('*.md')})
    def test_cycles_and_self_parent_are_rejected_before_graph_change(self):
        a=self.library.create('A',self.home);b=self.library.create('B',self.home,parent=a['id']);before=(self.base/'Index.md').read_bytes()
        for child,parent in ((a['id'],a['id']),(a['id'],b['id'])):
            with self.assertRaises(ValueError):self.library.move_project(child,parent)
        self.assertIsNone(self.library.project(a['id'])['parent']);self.assertEqual(before,(self.base/'Index.md').read_bytes())
    def test_auto_recall_excludes_organization_graph_and_manual_notes_survive_conflict(self):
        p=self.library.create('Progetto X',self.home)
        paths=list(NoteCatalog().paths(self.memory.path,'auto'))
        self.assertFalse(any('FREELLMAPI Desktop' in str(path) for path in paths))
        note=self.base/'Projects'/(p['id']+'.md');note.write_text('Nota manuale senza blocco gestito.',encoding='utf-8');before=note.read_bytes()
        result=self.library.sync();self.assertEqual(result['state'],'conflict');self.assertEqual(before,note.read_bytes())
    def test_legacy_catalog_migration_keeps_projects_and_adds_parent(self):
        other=self.home/'legacy';(other/'sessions').mkdir(parents=True)
        with sqlite3.connect(other/'sessions/library.sqlite3') as db:
            db.execute('CREATE TABLE projects(id TEXT PRIMARY KEY,name TEXT NOT NULL,root TEXT NOT NULL,created REAL NOT NULL)');db.execute('INSERT INTO projects VALUES(?,?,?,?)',('old','Vecchio',str(self.home),1))
        db.close()
        library=Library(other);self.assertEqual(library.projects()[0]['name'],'Vecchio');self.assertIsNone(library.projects()[0]['parent'])
    def test_network_failure_never_rotates_a_valid_remote_device_token(self):
        c=RemoteClient(self.home,'network-test')
        with patch.object(c,'descriptors',return_value=[{'url':'http://127.0.0.1:1'}]),patch('hl_credentials.get',return_value=json.dumps({'token':'synthetic-token','device':'synthetic-device'})),patch.object(c,'state',side_effect=OSError('temporary outage')),patch('remote_client.request_pair',side_effect=AssertionError('No token rotation')):
            with self.assertRaises(OSError):c.connect()
    def test_workspace_actions_use_current_installed_builder_and_no_secret_inputs(self):
        from skills_manager import Skills
        folder=self.home/'skills/devfridge-game-builder-test';folder.mkdir(parents=True);(folder/'source.json').write_text(json.dumps({'name':'devfridge-game-builder-test'}))
        backend=DesktopBackend(self.home)
        with patch.object(backend,'command',return_value={'state':'queued'}) as command:
            backend.workspace_action('game','resume');self.assertTrue(command.call_args.args[0].startswith('/skill use devfridge-game-builder-test '))
            backend.workspace_action('solana','balance');self.assertIn('nessuna transazione',command.call_args.args[0].lower())
        with self.assertRaises(ValueError):backend.workspace_action('arbitrary','shell')

if __name__=='__main__':unittest.main()
