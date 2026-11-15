import hashlib,json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from bundled_skills import register,unregister,validate,registered_name,MANAGED_BY
from skills_manager import Skills
from skill_router import catalog

class BundledSkillTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name);self.root=self.base/'suite with spaces';self.home=self.base/'profile'
        folder=self.root/'skills/dgb-test';folder.mkdir(parents=True)
        blobs={'SKILL.md':b'---\nname: devfridge-game-builder\ndescription: Build Three.js games for browser and Seeker\n---\nRead references/seeker-android.md.',
               'references/seeker-android.md':b'APK reference',
               'vendor/studio/SKILL.md':b'Knowledge, not a hook',
               'assets/helper.py':b'raise RuntimeError("MUST NOT EXECUTE")'}
        for name,body in blobs.items():
            path=folder/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(body)
        self.manifest={'schema_version':1,'packages':[{'id':'devfridge-game-builder','version':'1.12.0',
            'description':'Build Three.js games for browser and Seeker','path':'skills/dgb-test',
            'source_commit':'a'*40,'tree_sha':'b'*40,'files':{name:{'size':len(body),'sha256':hashlib.sha256(body).hexdigest()} for name,body in blobs.items()}}]}
        self.save_manifest()
        self.env=patch.dict(os.environ,{'FREELLMAPI_SUITE_ROOT':str(self.root)});self.env.start();self.addCleanup(self.env.stop)
    def save_manifest(self):
        (self.root/'bundled-skills.json').write_text(json.dumps(self.manifest),encoding='utf-8')
    def test_fresh_profile_registered_offline_with_supporting_files_and_no_scripts(self):
        with patch('urllib.request.urlopen',side_effect=AssertionError('Network forbidden')),patch('subprocess.run',side_effect=AssertionError('No scripts')):
            manager=Skills(self.home)
            row=manager.list()[0]
            self.assertEqual(row['version'],'1.12.0');self.assertTrue(row['built_in'])
            self.assertIn('Three.js',manager.read(row['name']))
            self.assertEqual(manager.read_file(row['name'],'references/seeker-android.md'),'APK reference')
            self.assertEqual(len([x for x in catalog(manager) if x['registered']]),1)
            self.assertEqual(manager.discover('devfridge game builder')[0]['name'],row['name'])
    def test_reopen_idempotent_and_manual_skills_preserved(self):
        manager=Skills(self.home);name=manager.list()[0]['name'];source=manager.path(name)/'source.json';before=source.stat().st_mtime_ns
        manual=self.home/'skills/devfridge-game-builder-manual';manual.mkdir()
        raw=json.dumps({'name':manual.name,'description':'Custom','local_path':str(manual)}).encode();(manual/'source.json').write_bytes(raw);(manual/'SKILL.md').write_text('User customized skill')
        Skills(self.home)
        self.assertEqual(source.stat().st_mtime_ns,before)
        self.assertEqual((manual/'source.json').read_bytes(),raw)
        self.assertEqual((manual/'SKILL.md').read_text(),'User customized skill')
    def test_missing_or_modified_support_refuses_registration(self):
        target=self.root/'skills/dgb-test/references/seeker-android.md';target.write_text('changed')
        with self.assertRaisesRegex(ValueError,'hash_mismatch'):register(self.home,self.root)
        self.assertFalse((self.home/'skills').exists())
        target.unlink()
        with self.assertRaisesRegex(ValueError,'file_set_mismatch'):register(self.home,self.root)
    def test_manifest_traversal_cannot_write_or_read_outside_package(self):
        self.manifest['packages'][0]['path']='../outside';self.save_manifest()
        with self.assertRaisesRegex(ValueError,'invalid_bundled_skill_path'):register(self.home,self.root)
        self.assertFalse((self.home/'skills').exists())
    def test_foreign_registration_is_not_overwritten(self):
        p=self.home/'skills'/registered_name('devfridge-game-builder')/'source.json';p.parent.mkdir(parents=True)
        p.write_text('{"name":"user-owned"}');before=p.read_bytes()
        self.assertEqual(register(self.home,self.root)[0]['code'],'user_metadata_collision')
        self.assertEqual(p.read_bytes(),before)
    def test_uninstall_removes_only_owned_registration_and_preserves_data(self):
        rows=register(self.home,self.root);folder=self.home/'skills'/rows[0]['name'];(folder/'my-note.md').write_text('Keep this')
        other=self.home/'skills/manual/source.json';other.parent.mkdir();other.write_text('{"name":"manual"}')
        session=self.home/'session.json';session.write_text('unchanged session')
        self.assertEqual(unregister(self.home,self.root),[rows[0]['name']])
        self.assertTrue(other.exists());self.assertEqual(session.read_text(),'unchanged session');self.assertEqual((folder/'my-note.md').read_text(),'Keep this')
    def test_explicit_skill_request_loads_builtin_without_selection_api(self):
        from freellmapi import Agent
        with patch('freellmapi.HOME',self.home):
            agent=Agent(self.base,'http://127.0.0.1:9','synthetic','auto')
        agent.skills=Skills(self.home)
        with patch.object(agent,'api',side_effect=AssertionError('No selector API needed')):
            agent.skill_router.run(agent,'Usa DevFridge Game Builder per un gioco.',lambda _:None)
        self.assertIn('Three.js',next(iter(agent.active_skills.values())))
    def test_superseding_manual_install_stays_preferred(self):
        manager=Skills(self.home);name=manager.list()[0]['name'];path=manager.path(name)/'source.json';row=json.loads(path.read_text())
        other=self.home/'skills/custom-new';other.mkdir();(other/'source.json').write_text(json.dumps({'name':'custom-new'}))
        row['superseded_by']='custom-new';path.write_text(json.dumps(row))
        manager=Skills(self.home)
        self.assertNotIn(name,[r['name'] for r in manager.list()])
    def test_desktop_game_action_uses_registered_builtin(self):
        from desktop_backend import DesktopBackend
        backend=DesktopBackend(self.home)
        with patch.object(backend,'command',return_value={'ok':True}) as command:
            backend.workspace_action('game','resume')
        self.assertTrue(command.call_args.args[0].startswith('/skill use '+registered_name('devfridge-game-builder')+' '))
    def test_doctor_requires_exact_file_set(self):
        (self.root/'skills/dgb-test/extra.txt').write_text('unlisted')
        with self.assertRaisesRegex(ValueError,'file_set_mismatch'):validate(self.root)

if __name__=='__main__':unittest.main()
