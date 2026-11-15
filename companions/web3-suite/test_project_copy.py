import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from project_copy import stage
from loop_guard import LoopGuard


class ProjectCopyTests(unittest.TestCase):
    def test_agent_reuses_staged_project_instead_of_restarting_in_new_directory(self):
        from freellmapi import Agent
        from work_ledger import record_request
        from android_delivery import request
        with tempfile.TemporaryDirectory() as folder:
            parent=Path(folder);source=parent/'source';root=parent/'work';source.mkdir();root.mkdir()
            (source/'package.json').write_text('{}');(source/'android/app').mkdir(parents=True);(source/'android/app/build.gradle').write_text('source')
            a=Agent(root,'http://localhost','test','auto',interactive=False);a.mode='auto';a.session=root/'s.json'
            text="Realizza APK per il Seeker con gioco e risorse inclusi nell'APK. Usa il progetto "+str(source)+': conserva originale.'
            record_request(a.session,text);request(a.session,text)
            a.tool('project_copy',{'source':str(source),'destination':'staged'})
            edited=root/'staged/android/app/build.gradle';edited.write_text('work already done')
            result=json.loads(a.tool('project_copy',{'source':str(source),'destination':'restart'}))
            self.assertTrue(result['reused']);self.assertFalse((root/'restart').exists())
            self.assertEqual(edited.read_text(),'work already done')

    def test_resume_partial_copy_preserves_edits_and_skips_caches_and_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'source';destination=root/'work';source.mkdir();destination.mkdir()
            contents={'package.json':'{}','src/main.js':'game','android/app/build.gradle':'old',
                      'android/.gradle/cache.bin':'cache','android/app/build/old.apk':'cache',
                      'node_modules/lib/index.js':'dependency','.env.local':'secret','release.jks':'secret'}
            for name,text in contents.items():
                path=source/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text)
            edited=destination/'android/app/build.gradle';edited.parent.mkdir(parents=True);edited.write_text('user edit')
            first=stage(source,destination);second=stage(source,destination)
            self.assertEqual(edited.read_text(),'user edit')
            self.assertEqual((destination/'src/main.js').read_text(),'game')
            self.assertEqual((destination/'node_modules/lib/index.js').read_text(),'dependency')
            for name in ('android/.gradle/cache.bin','android/app/build/old.apk','.env.local','release.jks'):
                self.assertFalse((destination/name).exists(),name)
            self.assertEqual(first['conflicts_preserved'],['android\\app\\build.gradle'] if sys.platform=='win32' else ['android/app/build.gradle'])
            self.assertEqual(second['copied'],0)
            self.assertEqual((source/'android/app/build.gradle').read_text(),'old')

    def test_overlapping_trees_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'package.json').write_text('{}')
            with self.assertRaisesRegex(ValueError,'sovrapposte'):stage(root,root/'inside')

    def test_real_file_progress_resets_global_stall_errors(self):
        guard=LoopGuard()
        for i in range(6):guard.observe('operation_'+str(i),{},'Errore: failure',True)
        guard.observe('edit_file',{'path':'actual.js'},json.dumps({'changed':True,'checkpoint':'verified-edit'}))
        self.assertEqual(guard.global_failures,0)
        for i in range(6):guard.observe('other_'+str(i),{},'Errore: failure',True)

    def test_different_recovered_commands_are_not_one_repeated_shell_error(self):
        guard=LoopGuard()
        for i in range(4):
            guard.observe('shell',{'command':'phase '+str(i)},'Errore: Exit code: 1',True)
            guard.observe('shell',{'command':'recovered '+str(i)},'Exit code: 0')

    def test_sdk_adb_is_available_to_shell_when_not_in_global_path(self):
        from seeker_adb import find_adb
        from shell_runner import run
        if not find_adb():self.skipTest('Android SDK not installed')
        with tempfile.TemporaryDirectory() as folder:
            result=run('adb version',folder,folder,10)
        self.assertIn('Android Debug Bridge version',result)

    def test_shell_cannot_wait_on_open_parent_stdin(self):
        with tempfile.TemporaryDirectory() as folder:
            command="$value=[Console]::In.ReadToEnd();Write-Output ('closed:'+$value.Length)"
            code='from shell_runner import run; r=run('+repr(command)+','+repr(folder)+','+repr(folder)+',3); print(r); assert "closed:0" in r'
            parent=subprocess.Popen([sys.executable,'-X','utf8','-c',code],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            try:
                parent.wait(timeout=10)
                output=parent.stdout.read().decode('utf-8',errors='replace')
                self.assertEqual(parent.returncode,0,output)
                self.assertIn('closed:0',output)
            finally:
                if parent.poll() is None:parent.kill();parent.wait()
                parent.stdin.close();parent.stdout.close()


if __name__=='__main__':unittest.main()
