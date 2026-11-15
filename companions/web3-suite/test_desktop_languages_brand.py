import hashlib,json,re,unittest
from pathlib import Path
import desktop_locales

class DesktopLanguageBrandTests(unittest.TestCase):
    def test_exact_official_sixty_locales_and_complete_public_ui_catalogs(self):
        manifest=desktop_locales.load();self.assertEqual(len(manifest['locales']),60)
        expected='en zh-CN es fr pt-BR it hi ar bn ru ur id de ja sw mr te tr ta vi ko fa th gu pl uk kn ml or my pa ro nl ms tl ha yo ig am uz az si ne km el cs hu sv he da fi no sk bg hr sr lt zh-TW pt-PT ka'.split()
        self.assertEqual(manifest['locales'],expected)
        labels=set(desktop_locales.load('it')['strings']);self.assertEqual(len(labels),180)
        for code in manifest['locales']:
            with self.subTest(code=code):
                data=desktop_locales.load(code);self.assertTrue(labels<=set(data['strings']))
                self.assertTrue(all(isinstance(v,str) and v.strip() for v in data['strings'].values()))
                self.assertEqual(data['rtl'],code in ('ar','he','fa','ur'))
    def test_locale_paths_are_allowlisted(self):
        for code in ('../key.dpapi','xx','en.json',None):
            if code is not None:
                with self.assertRaises(ValueError):desktop_locales.load(code)
    def test_official_logo_is_exact_upstream_asset_and_same_on_android(self):
        raw=Path('desktop/assets/official-logo.png').read_bytes()
        self.assertEqual(hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),'7f36c8241499306c0b428e9e933cef152fad4e89')
        self.assertEqual(raw,Path('mobile/res/drawable-nodpi/icon_art.png').read_bytes())
        self.assertIn('Tashfeen Ahmed',Path('desktop/third_party/FREELLMAPI-LICENSE.txt').read_text())
    def test_section_visibility_is_local_and_never_stops_a_service(self):
        script=Path('desktop/i18n.js').read_text(encoding='utf-8').split('const SectionVisibility=')[1]
        self.assertIn('localStorage.setItem',script);self.assertIn("['game','DevFridge Game Builder']",script)
        self.assertNotIn('rpc(',script);self.assertNotIn('/v1/',script);self.assertNotIn('command(',script)

if __name__=='__main__':unittest.main()
