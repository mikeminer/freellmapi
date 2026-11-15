import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from freellmapi import Agent
from session_store import Sessions, startup, choose


class SessionTests(unittest.TestCase):
    def test_atomic_metadata_retries_only_transient_permission_failure(self):
        path=self.root/'metadata.json';path.write_text('old',encoding='utf-8');original=Path.replace;calls=[]
        def replace(source,target):
            calls.append(source)
            if len(calls)<3:raise PermissionError('synthetic Windows sharing violation')
            return original(source,target)
        with patch.object(Path,'replace',replace),patch('session_store.time.sleep'):self.store.atomic(path,{'verified':True})
        self.assertEqual(len(calls),3);self.assertEqual(json.loads(path.read_text()),{'verified':True})
    def test_permanent_metadata_failure_is_bounded_and_preserves_original(self):
        path=self.root/'metadata.json';path.write_text('old',encoding='utf-8')
        with patch.object(Path,'replace',side_effect=PermissionError('synthetic locked file')) as replace,patch('session_store.time.sleep'),self.assertRaises(PermissionError):self.store.atomic(path,{'verified':True})
        self.assertEqual(replace.call_count,3);self.assertEqual(path.read_text(),'old');self.assertEqual(list(self.root.glob('metadata.json.*.tmp')),[])
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.store=Sessions(self.root/'home')
        self.a=Agent(self.root,'http://localhost','test','auto')

    def test_new_preserves_previous_and_resume_after_restart(self):
        one=self.store.create(self.root)
        self.store.activate(self.a,one)
        self.a.messages=[{'role':'user','content':'Il mio progetto'}]; self.a.save()
        before=self.store.path(one).read_bytes()
        two=self.store.create(self.root); self.store.activate(self.a,two)
        self.assertEqual(self.a.messages,[]); self.assertNotEqual(one,two)
        self.assertEqual(self.store.path(one).read_bytes(),before)
        b=Agent(self.root,'http://localhost','test','different-model')
        self.store.activate(b,one)
        self.assertEqual(b.messages[0]['content'],'Il mio progetto')
        self.assertEqual(b.model,'different-model')
    def test_custom_title_survives_model_changes_and_resume(self):
        sid=self.store.create(self.root);path=self.store.path(sid)
        self.store.update(path,self.root,[],title='La mia sessione')
        self.store.activate(self.a,sid);self.a.messages=[{'role':'user','content':'Un testo differente'}];self.a.save()
        self.store.activate(self.a,sid)
        self.assertEqual(json.loads(self.store.metadata(sid).read_text(encoding='utf-8'))['title'],'La mia sessione')

    def test_legacy_import_is_idempotent_non_destructive(self):
        legacy=self.store.home/(hashlib.sha256(str(self.root.resolve()).encode()).hexdigest()[:16]+'.json')
        legacy.write_text('[{"role":"user","content":"legacy"}]',encoding='utf-8')
        before=legacy.read_bytes()
        self.store.migrate(self.root); self.store.migrate(self.root)
        self.assertEqual(len(self.store.list()),1); self.assertEqual(before,legacy.read_bytes())
        self.store.activate(self.a,self.store.list()[0]['id'])
        self.assertEqual(self.a.messages[0]['content'],'legacy')

    def test_cross_project_selection_switches_root(self):
        other=self.root/'other'; other.mkdir()
        ident=self.store.create(other,[{'role':'user','content':'Other task'}])
        self.store.activate(self.a,ident)
        self.assertEqual(self.a.root,other.resolve())
        self.assertEqual(len(self.store.list(self.root)),0)

    def test_startup_choices_and_cancel(self):
        ident=self.store.create(self.root,[{'role':'user','content':'Resume me'}])
        with patch('questions.ask',return_value={'answer':'Riprendi ultima sessione'}): startup(self.a,self.store)
        self.assertEqual(self.a.session.stem,ident)
        with patch('questions.ask',return_value={'cancelled':True}): self.assertIsNone(startup(self.a,self.store))
        with patch('questions.ask',return_value={'answer':'Nuova sessione'}): startup(self.a,self.store)
        self.assertNotEqual(self.a.session.stem,ident)

    def test_picker_pagination(self):
        for i in range(7): self.store.create(self.root,[{'role':'user','content':f'Project {i}'}])
        rows=self.store.list()
        pages=iter((True,False))
        def answer(question,options):
            if next(pages): return {'answer':'Pagina successiva'}
            return {'answer':options[0]}
        with patch('questions.ask',side_effect=answer): chosen=choose(self.store)
        self.assertEqual(chosen,rows[5]['id'])

    def test_active_jobs_prevent_switch(self):
        ident=self.store.create(self.root)
        thread=Mock(); thread.is_alive.return_value=True
        self.a.jobs=Mock(jobs={'x':{'thread':thread}})
        with self.assertRaisesRegex(ValueError,'subagenti'): self.store.activate(self.a,ident)

    def test_unfinished_tool_is_not_replayed(self):
        history=[{'role':'assistant','tool_calls':[{'id':'abc','function':{'name':'shell','arguments':'{}'}}]}]
        ident=self.store.create(self.root,history)
        with patch.object(self.a,'tool') as tool:
            self.store.activate(self.a,ident); tool.assert_not_called()
        self.assertEqual(self.a.messages[-1]['tool_call_id'],'abc')
        self.assertIn('Non rieseguire',self.a.messages[-1]['content'])

    def test_corrupt_session_does_not_replace_active(self):
        ident=self.store.create(self.root)
        self.store.path(ident).write_text('{}',encoding='utf-8')
        self.a.messages=[{'role':'user','content':'keep me'}]
        with self.assertRaises(ValueError): self.store.activate(self.a,ident)
        self.assertEqual(self.a.messages[0]['content'],'keep me')

    def test_noninteractive_new_and_explicit_continue(self):
        self.a.interactive=False
        old=self.store.create(self.root,[{'role':'user','content':'old'}])
        startup(self.a,self.store,resume=True); self.assertEqual(self.a.session.stem,old)
        startup(self.a,self.store); self.assertNotEqual(self.a.session.stem,old)


if __name__=='__main__': unittest.main()
