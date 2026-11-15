import unittest
from terminal_ui import ModelPicker

class PickerTests(unittest.TestCase):
 def setUp(self):
  self.p=ModelPicker([{'id':'model-'+str(i).zfill(3),'available':i!=12} for i in range(40)],'model-005')
 def test_current_and_navigation(self):
  self.assertEqual(self.p.key('ENTER'),'model-005')
  self.p.key('DOWN'); self.assertEqual(self.p.key('ENTER'),'model-006')
  self.p.key('PGDOWN',10); self.assertEqual(self.p.key('ENTER'),'model-017')
  self.assertEqual(self.p.key('ESC'),'model-005')
 def test_filter_and_unavailable(self):
  for c in 'model-012': self.p.key(c)
  self.assertIsNone(self.p.key('ENTER'))
  self.p.key('TAB'); self.assertEqual(self.p.key('ENTER'),'model-012')
  self.p.key('CLEAR'); self.assertEqual(self.p.query,'')
 def test_boundaries(self):
  self.p.key('HOME'); self.p.key('UP'); self.assertEqual(self.p.selected,0)
  self.p.key('END'); self.p.key('DOWN'); self.assertEqual(self.p.selected,len(self.p.matches())-1)
 def test_empty_catalog_has_auto(self):
  p=ModelPicker([],'auto'); self.assertEqual(p.key('ENTER'),'auto')

if __name__=='__main__': unittest.main()
