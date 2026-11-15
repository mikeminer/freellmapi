import csv
import json
from pathlib import Path
import runpy
import tempfile
import unittest


class CSVAcceptance(unittest.TestCase):
    def setUp(self):
        path=Path(__file__).parent/'qa_runs/qa_run_20261112_032427/csv_project/processor.py'
        self.process=runpy.run_path(str(path))['process_csv']
    def calculate(self,rows):
        with tempfile.TemporaryDirectory(prefix='csv spaced ') as tmp:
            source=Path(tmp)/'ordini prova.csv'; target=Path(tmp)/'riepilogo prova.json'
            with source.open('w',newline='',encoding='utf-8') as f:
                writer=csv.writer(f); writer.writerow(['id','prodotto','quantita','prezzo_unitario']); writer.writerows(rows)
            self.process(source,target)
            return json.loads(target.read_text(encoding='utf-8'),parse_constant=lambda x: (_ for _ in ()).throw(ValueError('non JSON '+x)))
    def test_quantity(self): self.assertEqual(self.calculate([['1','A',3,'1.25']])[0]['totale'],3.75)
    def test_decimal_half_up(self): self.assertEqual(self.calculate([['1','A',1,'2.675']])[0]['totale'],2.68)
    def test_blank_rows(self): self.assertEqual(self.calculate([[],['','','',''],['1','A',1,2]]).__len__(),1)
    def test_invalid_nonfinite_prices(self): self.assertEqual(self.calculate([['1','A',1,'NaN'],['2','B',1,'Infinity']]),[])
    def test_unicode_and_paths_with_spaces(self): self.assertEqual(self.calculate([['1','Caffè 日本',1,2]])[0]['prodotto'],'Caffè 日本')
    def test_invalid_quantity_and_price(self): self.assertEqual(self.calculate([['1','A','','abc'],['2','A',-1,2],['3','A',1,-2]]),[])
