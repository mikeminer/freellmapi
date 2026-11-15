import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from file_scan import files,listing,ScanLimit


class FileScanTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_entry_limit_stops_even_without_matching_files(self):
        for i in range(10): (self.root/f'{i}.txt').write_text('x')
        with self.assertRaises(ScanLimit): list(files(self.root,set(),max_entries=3))

    def test_time_limit(self):
        (self.root/'x').touch()
        with patch('file_scan.time.monotonic',side_effect=[0,6]):
            with self.assertRaises(ScanLimit): list(files(self.root,set(),seconds=5))

    def test_output_stops_generator_early(self):
        def paths(*args):
            yield self.root/'long-file-name.md'
            raise AssertionError('Should stop without exhausting generator')
        with patch('file_scan.files',paths): result=listing(self.root,self.root,'*',set(),max_chars=3)
        self.assertIn('Elenco parziale',result)

    def test_directory_scope_and_ignored_names(self):
        folder=self.root/'src'; folder.mkdir(); (folder/'yes.md').touch()
        (self.root/'outside.md').touch()
        hidden=folder/'NODE_MODULES'; hidden.mkdir(); (hidden/'no.md').touch()
        self.assertEqual(listing(self.root,folder,'*.md',{'node_modules'}),str(Path('src')/'yes.md'))

    def test_partial_scan_is_explicit(self):
        def paths(*args):
            yield self.root/'a.md'
            raise ScanLimit('Scansione parziale')
        with patch('file_scan.files',paths): result=listing(self.root,self.root,'*.md',set())
        self.assertIn('a.md',result); self.assertIn('Scansione parziale',result)

    @unittest.skipUnless(__import__('os').name=='nt','Windows junction')
    def test_windows_junction_not_traversed(self):
        import subprocess
        target=self.root/'target'; target.mkdir(); (target/'x.md').touch()
        link=self.root/'junction'
        subprocess.run(['cmd','/c','mklink','/J',str(link),str(target)],capture_output=True,check=True)
        try:
            found=list(files(self.root,set()))
            self.assertEqual(found,[target/'x.md'])
        finally: link.rmdir()


if __name__=='__main__': unittest.main()
