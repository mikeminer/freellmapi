import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from freellmapi import Agent


class EditRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.path=self.root/'NEXT_STEPS.md'
        self.path.write_text('## Fase 3\n- [ ] Test CSV\n\n## Ripresa\nConserva risultati\n')
        self.agent=Agent(self.root,'http://localhost','test','auto'); self.agent.mode='auto'; self.agent.session=self.root/'s.json'
    def test_same_failed_strategy_is_blocked_even_with_different_new(self):
        args={'path':self.path.name,'old':'## Fase 4 inesistente','new':'done'}
        with self.assertRaisesRegex(ValueError,'edit_text_not_unique'): self.agent.tool('edit_file',args)
        with self.assertRaisesRegex(ValueError,'edit_strategy_repeated'): self.agent.tool('edit_file',{**args,'new':'other'})
        self.assertIn('[ ]',self.path.read_text())
        result=json.loads(self.agent.tool('edit_file',{'path':self.path.name,'old':'- [ ] Test CSV','new':'- [x] Test CSV'}))
        self.assertTrue(result['changed'])
    def response(self,old,new='done'):
        return {'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':old,'function':{'name':'edit_file','arguments':json.dumps({'path':self.path.name,'old':old,'new':new})}}]}}]}
    def test_turn_rereads_then_recovers_with_small_exact_edit(self):
        final={'choices':[{'message':{'role':'assistant','content':'Completato.'}}]}
        with patch.object(self.agent,'api',side_effect=[self.response('nonexistent'),self.response('- [ ] Test CSV','- [x] Test CSV'),final]):
            self.agent.turn('Aggiorna fase CSV')
        self.assertEqual(self.agent.turn_outcome,'completed')
        self.assertIn('[x]',self.path.read_text())
        self.assertTrue(any(m['role']=='tool' and '## Ripresa' in m.get('content','') for m in self.agent.messages))
    def test_unresolved_edit_cannot_be_reported_complete(self):
        final={'choices':[{'message':{'role':'assistant','content':'Tutto corretto!'}}]}
        with patch.object(self.agent,'api',side_effect=[self.response('missing'),final,final]),patch('freellmapi.output') as output:
            self.agent.turn('Aggiorna file')
        self.assertEqual(self.agent.turn_outcome,'incomplete')
        self.assertFalse(any('Tutto corretto!' in str(c) for c in output.call_args_list))
