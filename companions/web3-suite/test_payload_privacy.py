import hashlib,importlib.util,tempfile,unittest,zipfile
from pathlib import Path
spec=importlib.util.spec_from_file_location('payload_audit',Path(__file__).parent/'packaging/windows/audit_payload.py')
audit=importlib.util.module_from_spec(spec);spec.loader.exec_module(audit)

class PayloadPrivacyTests(unittest.TestCase):
    def test_private_state_is_rejected_without_exposing_its_contents(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'key.dpapi').write_bytes(b'never print this fixture')
            result=audit.audit(p)
            self.assertEqual(result['status'],'FAIL');self.assertNotIn('never print',str(result))
    def test_private_needles_are_detected_in_text_and_binary_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);needle='PRIVATE-TEST-IDENTIFIER'
            (p/'sample.py').write_text(needle);(p/'sample.bin').write_bytes(b'\0'+needle.encode('utf-16-le'))
            result=audit.audit(p,[needle]);self.assertEqual(result['status'],'FAIL');self.assertEqual(len(result['findings']),2)
            self.assertNotIn(needle,str(result))
    def test_source_and_public_certificates_are_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'source.py').write_text('token = load_user_credential()')
            (p/'certificate.pem').write_text('-----BEGIN CERTIFICATE-----\nPUBLIC-TEST\n-----END CERTIFICATE-----')
            self.assertEqual(audit.audit(p)['status'],'PASS')
    def test_runtime_pruning_omits_public_private_key_fixtures_and_preserves_library(self):
        spec=importlib.util.spec_from_file_location('prepare_private_runtime',Path(__file__).parent/'packaging/windows/prepare_runtime.py')
        runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);tests=root/'Lib/site-packages/Crypto/SelfTest';tests.mkdir(parents=True)
            (tests/'example.py').write_text('Public synthetic cryptographic fixture')
            library=tests.parent/'Cipher';library.mkdir();(library/'__init__.py').write_text('Runtime library')
            runtime.prune_nonruntime_fixtures(root)
            self.assertFalse(tests.exists());self.assertTrue((library/'__init__.py').exists())
    def test_tcltk_subset_requires_matching_archive_hash_and_keeps_only_runtime_subset(self):
        spec=importlib.util.spec_from_file_location('prepare_tcltk_runtime',Path(__file__).parent/'packaging/windows/prepare_runtime.py')
        runtime=importlib.util.module_from_spec(spec);spec.loader.exec_module(runtime)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);archive=root/'official-fixture.zip';dest=root/'runtime';dest.mkdir()
            with zipfile.ZipFile(archive,'w') as z:
                z.writestr('Lib/tkinter/__init__.py','fixture');z.writestr('DLLs/_tkinter.pyd','fixture')
                z.writestr('tcl/tcl8.6/init.tcl','fixture');z.writestr('python.exe','must not replace core interpreter')
            with self.assertRaisesRegex(ValueError,'checksum'):runtime.stage_tcltk(dest,archive,'0'*64)
            self.assertEqual(list(dest.iterdir()),[])
            self.assertEqual(runtime.stage_tcltk(dest,archive,hashlib.sha256(archive.read_bytes()).hexdigest()),3)
            self.assertFalse((dest/'python.exe').exists());self.assertTrue((dest/'tcl/tcl8.6/init.tcl').is_file())

if __name__=='__main__':unittest.main()
