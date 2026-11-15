"""Packaging boundary tests; no installer or fake Python binary is executed."""
import importlib.util
import hashlib,json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('suite_installer',Path(__file__).parent/'packaging/windows/build_installer.py')
build=importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


class WindowsInstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='suite payload with spaces ')
        self.addCleanup(self.temp.cleanup)
        self.payload=Path(self.temp.name)
        for name in build.REQUIRED:
            path=self.payload/name;path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(b'fixture, not executable')
        shutil.copyfile(build.ICON,self.payload/'agent/desktop/freellmapi-official.ico')
        skill=self.payload/'skills/dgb-test/SKILL.md';skill.parent.mkdir(parents=True)
        raw=b'Synthetic build skill';skill.write_bytes(raw)
        (self.payload/'bundled-skills.json').write_text(json.dumps({'schema_version':1,'packages':[{
            'id':'devfridge-game-builder','path':'skills/dgb-test',
            'files':{'SKILL.md':{'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}}}]}))

    def test_official_icon_has_complete_valid_windows_resources(self):
        self.assertTrue({(16,16),(32,32),(48,48),(64,64),(256,256)}<=build.icon_sizes(build.ICON))
        broken=self.payload/'broken.ico'
        broken.write_bytes(build.ICON.read_bytes()[:-64])
        with self.assertRaisesRegex(ValueError,'incompleta'):
            build.icon_sizes(broken)

    def test_missing_private_python_fails_even_if_system_python_exists(self):
        (self.payload/'runtime/python/python.exe').unlink()
        with patch.object(build.subprocess,'run') as run:
            with self.assertRaisesRegex(ValueError,'Payload incompleto'):
                build.validate_payload(self.payload)
        run.assert_not_called()

    def test_bundled_personal_state_cannot_be_distributed(self):
        for name in ('agent/runtime-home.json','agent/key.dpapi','agent/freeapi.db','agent/config.json'):
            path=self.payload/name;path.write_text('personal fixture',encoding='utf-8')
            with self.subTest(name=name),self.assertRaisesRegex(ValueError,'Dati personali'):
                build.validate_payload(self.payload,probe=False)
            path.unlink()

    def test_runtime_probe_uses_private_paths_with_spaces_and_no_shell(self):
        success=subprocess.CompletedProcess([],0,'private-runtime-ok\n','')
        with patch.object(build.subprocess,'run',return_value=success) as run:
            result=build.validate_payload(self.payload)
        args,kwargs=run.call_args
        self.assertEqual(args[0][0],str(self.payload/'runtime/python/python.exe'))
        self.assertIn('-I',args[0])
        self.assertEqual(args[0][-3:],[str(self.payload/'runtime/python'),str(self.payload/'agent'),str(self.payload/'launcher')])
        self.assertFalse(kwargs.get('shell',False))
        self.assertTrue(result['runtime_probe'])

    def test_missing_qt_or_wrong_runtime_prefix_stops_build(self):
        failure=subprocess.CompletedProcess([],1,'','Missing private runtime dependency')
        with patch.object(build.subprocess,'run',return_value=failure):
            with self.assertRaisesRegex(ValueError,'runtime Python privato'):
                build.validate_payload(self.payload)

    def test_missing_bundled_support_fails_preflight(self):
        (self.payload/'skills/dgb-test/SKILL.md').unlink()
        success=subprocess.CompletedProcess([],0,'private-runtime-ok\n','')
        with patch.object(build.subprocess,'run',return_value=success):
            with self.assertRaisesRegex(ValueError,'file_set_mismatch'):
                build.validate_payload(self.payload)

    def test_wrong_payload_icon_is_rejected(self):
        (self.payload/'agent/desktop/freellmapi-official.ico').write_bytes(b'wrong icon')
        with self.assertRaisesRegex(ValueError,'stessa icona ufficiale'):
            build.validate_payload(self.payload,probe=False)


if __name__=='__main__':unittest.main()
