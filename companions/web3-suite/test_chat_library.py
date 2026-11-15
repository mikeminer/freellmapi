import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from chat_library import Library
from session_store import Sessions
from desktop_backend import DesktopBackend

class ChatLibraryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.home=Path(temp.name)
        self.sessions=Sessions(self.home);self.library=Library(self.home)
        self.sid=self.sessions.create(self.home,[{'role':'user','content':'Contesto ETH da conservare.'}]);self.path=self.sessions.path(self.sid)
        self.path.with_suffix('.answers.json').write_text('{"Mercato":"ETH"}',encoding='utf-8')
        self.before={p.name:p.read_bytes() for p in self.path.parent.glob(self.sid+'*.json')}
    def unchanged(self):
        self.assertEqual(self.before,{p.name:p.read_bytes() for p in self.path.parent.glob(self.sid+'*.json')})
    def test_create_group_rename_move_delete_and_restore_preserve_all_context_files(self):
        project=self.library.create('Trading',self.home);self.library.chat(self.sid,'rename',name='Analisi ETH')
        self.library.chat(self.sid,'move',project=project['id']);self.library.edit_project(project['id'],name='Mercati')
        row=self.sessions.list()[0];self.assertEqual(row['title'],'Analisi ETH');self.assertEqual(row['project_id'],project['id'])
        self.library.chat(self.sid,'delete');self.assertEqual(self.sessions.list(),[])
        self.assertEqual(len(self.sessions.list(include_deleted=True)),1)
        Library(self.home).chat(self.sid,'restore');self.assertEqual(len(Sessions(self.home).list()),1)
        self.unchanged()
    def test_deleting_project_ungroups_every_chat_without_deleting_history_or_directory(self):
        project=self.library.create('Gruppo',self.home);self.library.move_many([self.sid],project['id'])
        self.library.edit_project(project['id'],delete=True)
        self.assertEqual(self.library.projects(),[]);self.assertIsNone(self.sessions.list()[0]['project_id']);self.unchanged();self.assertTrue(self.home.is_dir())
    def test_cmd_explicit_rename_updates_desktop_alias_without_changing_conversation(self):
        self.library.chat(self.sid,'rename',name='Titolo desktop')
        self.sessions.update(self.path,self.home,[],title='Titolo CMD')
        self.assertEqual(self.sessions.list()[0]['title'],'Titolo CMD')
        self.assertEqual(self.path.read_bytes(),self.before[self.path.name])
    def test_bulk_move_is_atomic_if_any_session_is_invalid(self):
        project=self.library.create('Gruppo',self.home)
        with self.assertRaises(ValueError):self.library.move_many([self.sid,'a'*32],project['id'])
        self.assertFalse(self.sessions.list()[0].get('project_id'));self.unchanged()
    def test_invalid_names_roots_ids_never_modify_history(self):
        for name in ('','a\nb','x'*121):
            with self.assertRaises(ValueError):self.library.create(name,self.home)
        with self.assertRaises(ValueError):self.library.create('X',self.home/'missing')
        with self.assertRaises(ValueError):self.library.chat('../outside','delete')
        self.unchanged()
    def test_active_chat_cannot_be_deleted_while_engine_is_busy(self):
        backend=DesktopBackend(self.home)
        with patch.object(backend.client,'state',return_value={'session':self.sid,'busy':True}),patch.object(backend.client,'send',side_effect=AssertionError('Do not interrupt running work')):
            with self.assertRaisesRegex(ValueError,'fine del turno'):backend.library({'action':'delete','session':self.sid})
        self.assertEqual(len(self.sessions.list()),1);self.unchanged()
    def test_active_chat_switches_to_new_without_replay_before_hiding_old(self):
        backend=DesktopBackend(self.home)
        with patch.object(backend.client,'state',return_value={'session':self.sid}),patch.object(backend,'local_switch',return_value='b'*32) as switch:
            backend.library({'action':'delete','session':self.sid})
        switch.assert_called_once_with('/new');self.assertEqual(self.sessions.list(),[]);self.unchanged()
    def test_new_project_chat_uses_native_folder_switch_and_records_group(self):
        project=self.library.create('P',self.home);backend=DesktopBackend(self.home)
        new=self.sessions.create(self.home)
        with patch.object(backend,'local_switch',return_value=new) as switch:
            result=backend.library({'action':'new_project_chat','project':project['id']})
        self.assertEqual(result['session'],new);switch.assert_called_once_with('/project '+str(self.home.resolve()))
        self.assertEqual(next(r for r in self.sessions.list() if r['id']==new)['project_id'],project['id']);self.unchanged()

if __name__=='__main__':unittest.main()
