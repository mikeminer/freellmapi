import importlib.util,tempfile,unittest
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

if __name__=='__main__':unittest.main()
