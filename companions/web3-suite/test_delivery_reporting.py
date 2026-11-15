import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from delivery_reporting import publish
from android_delivery import physical_ready
from work_ledger import save


class ReportingTests(unittest.TestCase):
    def test_report_uses_actual_file_differences_not_model_claims(self):
        with tempfile.TemporaryDirectory() as folder:
            parent=Path(folder);root=parent/'work';base=parent/'base';root.mkdir();base.mkdir()
            for p in (root,base):
                (p/'android/app').mkdir(parents=True)
                (p/'android/app/build.gradle').write_text('old' if p==base else 'new applicationId')
                java=p/'android/app/src/main/java/MainActivity.java';java.parent.mkdir(parents=True);java.write_text('unchanged Java')
            (root/'REPORT.md').write_text('Model claim: MainActivity was changed')
            session=root/'s.json';save(session,{'android_build_receipts':[{'project':str(root),'web_exit':0,'gradle_exit':0,'apk':{'sha256':'1234'}}]})
            agent=SimpleNamespace(root=root,session=session,approve=Mock(return_value=True),seeker=SimpleNamespace(logs=lambda *a:'PAGE_READY canvas=1'))
            observed={'goal':{'reports':True,'install':True,'launch':True,'base_project':str(base),'mint':'PUBLIC','marker':'RUN'},
                      'apk':{'path':str(root/'android/app/build/outputs/apk/debug/app-debug.apk'),'sha256':'1234','bytes':5,'valid':True,'signature_verified':True,'bundled_html':True,'package':'test.app'},
                      'device':{'serial':'device','same_apk_installed':True,'pid':'123'}}
            result=publish(agent,observed)
            self.assertEqual([r['path'] for r in result['modified_source_files']],['android/app/build.gradle'])
            saved=json.loads((root/'results.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['checks']['web_build'],'PASS')
            self.assertTrue(list(root.glob('REPORT.md.model-draft-*.txt')))
            self.assertNotIn('MainActivity was changed',(root/'REPORT.md').read_text())

    def test_presence_or_pid_does_not_replace_valid_apk_and_matching_install(self):
        self.assertFalse(physical_ready({'apk':{'valid':False},'goal':{'install':True},'device':{'pid':'1','same_apk_installed':True}}))
        self.assertFalse(physical_ready({'apk':{'valid':True},'goal':{'install':True},'device':{'same_apk_installed':False}}))
        self.assertFalse(physical_ready({'apk':{'valid':True},'goal':{'launch':True},'device':{}}))


if __name__=='__main__':unittest.main()
