import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import repair_lab as lab
from qa_interruption import finalize

REGRESSION='''import unittest
from math_lib import add_strictly_positive
from logic import calculate_total
class Checks(unittest.TestCase):
 def test_zero(self):
  with self.assertRaises(ValueError): add_strictly_positive(0,5)
 def test_negative(self):
  with self.assertRaises(ValueError): add_strictly_positive(-1,5)
 def test_sum(self): self.assertEqual(calculate_total([1,2,3]),6)
 def test_empty(self): self.assertEqual(calculate_total([]),0)
 def test_bad_price(self):
  with self.assertRaises(ValueError): calculate_total([0,2])
'''
FIXED_LOGIC='''from math_lib import add_strictly_positive
def calculate_total(prices):
 values=list(prices)
 if not values: return 0
 total=values[0]
 if total<=0: raise ValueError("Must be positive")
 for p in values[1:]: total=add_strictly_positive(total,p)
 return total
'''

class ProjectRepairTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name)/'qa_runs'/'bench';self.root.mkdir(parents=True);self.home=Path(self.tmp.name)/'home'
  (self.root/'math_lib.py').write_text('def add_strictly_positive(a,b):\n if a<0 or b<0: raise ValueError("Must be positive")\n return a+b\n')
  (self.root/'logic.py').write_text('from math_lib import add_strictly_positive\ndef calculate_total(prices):\n total=0\n for p in prices: total=add_strictly_positive(total,p)\n return total\n')
 def prepared(self):
  row=lab.start(self.home,'zero bug',self.root);ident=row['id'];self.assertEqual(row['source_root'],str(self.root.resolve()))
  lab.edit(self.home,ident,'math_lib.py','a<0 or b<0','a<=0 or b<=0')
  old=(self.root/'logic.py').read_text();lab.edit(self.home,ident,'logic.py',old,FIXED_LOGIC)
  self.assertEqual(lab.verify(self.home,ident,REGRESSION)['state'],'tested');return ident
 def test_project_activation_without_global_root_patch(self):
  original=lab.ROOT;ident=self.prepared();self.assertEqual(lab.promote(self.home,ident)['state'],'activated_restart_required')
  self.assertEqual(lab.ROOT,original);self.assertIn('a<=0',(self.root/'math_lib.py').read_text())
 def test_actual_mid_commit_failure_rolls_back_hashes(self):
  before=lab.manifest(self.root);ident=self.prepared();replace=lab.replace;calls=[0]
  def fail(source,target):
   calls[0]+=1
   if calls[0]==2: raise OSError('injected second replacement failure')
   return replace(source,target)
  with patch.object(lab,'replace',side_effect=fail),self.assertRaises(OSError):lab.promote(self.home,ident)
  self.assertEqual(lab.manifest(self.root),before)
  self.assertEqual(lab.load(self.home,ident)[1]['state'],'rolled_back')
 def test_same_problem_different_projects_do_not_share_id(self):
  a=lab.start(self.home,'same',self.root);other=self.root.parent/'other';other.mkdir()
  b=lab.start(self.home,'same',other);self.assertNotEqual(a['id'],b['id'])
 def test_list_phases_finalized(self):
  p=self.root/'results.json';p.write_text(json.dumps({'phases':[{'name':'rollback','status':'NOT_RUN'}]}))
  finalize(p,'interrupted');self.assertEqual(json.loads(p.read_text())['phases'][0]['status'],'NOT_VERIFIED')
