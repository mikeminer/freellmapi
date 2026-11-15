from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace
from android_build import sync_assets,build


class AndroidBuildTests(unittest.TestCase):
    def test_asset_sync_replaces_generated_only_and_includes_visible_debug_identity(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);dist=root/'dist';dist.mkdir();(dist/'index.html').write_text('<body>game</body>')
            source=root/'src/game.js';source.parent.mkdir();source.write_text('source intact')
            target=root/'android/app/src/main/assets/public';target.mkdir(parents=True);(target/'stale.js').write_text('old')
            sync_assets(root,{'marker':'RUN-42','mint':'PUBLIC-MINT'})
            self.assertFalse((target/'stale.js').exists())
            self.assertIn('freellmapi-build-marker',(target/'index.html').read_text())
            self.assertIn('RUN-42',(target/'index.html').read_text())
            self.assertEqual((dist/'index.html').read_text(),'<body>game</body>')
            self.assertEqual(source.read_text(),'source intact')

    def test_missing_or_failed_web_build_preserves_previous_assets(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);target=root/'android/app/src/main/assets/public';target.mkdir(parents=True);(target/'index.html').write_text('previous')
            with self.assertRaisesRegex(ValueError,'assente'):sync_assets(root,{})
            self.assertEqual((target/'index.html').read_text(),'previous')
            (root/'dist').mkdir();(root/'dist/index.html').write_text('candidate')
            with patch('android_build.shutil.copytree',side_effect=OSError('copy failed')),self.assertRaises(OSError):sync_assets(root,{})
            self.assertEqual((target/'index.html').read_text(),'previous')

    def test_gradle_failure_keeps_successful_web_work_for_resume(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'package.json').write_text('{}');(root/'android/app').mkdir(parents=True);(root/'android/app/build.gradle').write_text('android')
            (root/'dist').mkdir();(root/'dist/index.html').write_text('<body>current web</body>')
            agent=SimpleNamespace(path=lambda path:root,session=root/'s.json',approve=Mock(return_value=True))
            with patch('android_build.gradle',return_value=Path('gradle.bat')),patch('android_build.run',side_effect=['Exit code: 0\nweb built','Errore: Gradle source error']):
                result=build(agent,'.',root)
            self.assertFalse(result['ok']);self.assertEqual(result['phase'],'android_build')
            self.assertIn('current web',(root/'android/app/src/main/assets/public/index.html').read_text())
            self.assertEqual((root/'android/app/build.gradle').read_text(),'android')


if __name__=='__main__':unittest.main()
