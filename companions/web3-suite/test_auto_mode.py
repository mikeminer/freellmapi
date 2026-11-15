import json
import tempfile
import unittest
import itertools
from pathlib import Path
from unittest.mock import Mock, patch
from freellmapi import Agent


sequence=itertools.count()
def response(tool=True):
    msg={'role':'assistant','content':None if tool else 'Finito.'}
    if tool: msg['tool_calls']=[{'id':'t','function':{'name':'set_plan','arguments':json.dumps({'plan':'Avanzamento '+str(next(sequence))})}}]
    return {'choices':[{'message':msg}]}


class AutoModeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.a=Agent(self.tmp.name,'http://localhost','test','auto')
        self.a.session=Path(self.tmp.name)/'session.json'

    def test_auto_approves_and_plan_still_denies(self):
        with patch('builtins.input') as ask:
            self.a.mode='auto'; self.assertTrue(self.a.approve('write'))
            self.a.mode='plan'; self.assertFalse(self.a.approve('write'))
            ask.assert_not_called()
        self.a.mode='ask'
        with patch('builtins.input',return_value='n'): self.assertFalse(self.a.approve('write'))

    def test_auto_continues_past_thirty_without_replaying(self):
        self.a.mode='auto'
        with patch.object(self.a,'api',side_effect=[response() for _ in range(31)]+[response(False)]) as api,patch.object(self.a,'compact'),patch.object(self.a,'tool',return_value='ok') as tool,patch('freellmapi.output'):
            self.a.turn('Esegui il lavoro')
        self.assertEqual(api.call_count,32); self.assertEqual(tool.call_count,31)
        self.assertEqual(sum(m['role']=='user' for m in self.a.messages),1)

    def test_manual_retains_limit(self):
        with patch.object(self.a,'api',side_effect=[response() for _ in range(30)]) as api,patch.object(self.a,'compact'),patch.object(self.a,'tool',return_value='ok'),patch('freellmapi.output'):
            self.a.turn('Esegui')
        self.assertEqual(api.call_count,30)

    def test_interrupt_stops_auto(self):
        self.a.mode='auto'
        with patch.object(self.a,'api',return_value=response()) as api,patch.object(self.a,'compact'),patch.object(self.a,'tool',side_effect=KeyboardInterrupt),patch('freellmapi.output'):
            self.a.turn('Esegui')
        self.assertEqual(api.call_count,1)
        self.assertIn('interrotta',self.a.messages[-1]['content'])

    def test_choices_are_not_automatically_answered(self):
        self.a.mode='auto'
        with patch('freellmapi.ask_question',return_value={'cancelled':True}) as ask:
            result=json.loads(self.a.tool('ask_user',{'question':'Piattaforma?','options':['Web','Seeker']}))
        ask.assert_called_once(); self.assertTrue(result['cancelled'])

    def test_auto_services_background_approvals(self):
        self.a.mode='auto'; self.a.jobs=Mock()
        self.a.jobs.jobs={}
        self.a.jobs.delivered=set()
        self.a.jobs.wait.return_value='Completati'
        with patch.object(self.a,'api',return_value=response(False)),patch.object(self.a,'compact'),patch('freellmapi.output'):
            self.a.turn('Esegui')
        self.a.jobs.service.assert_called_once(); self.a.jobs.wait.assert_called_once()


if __name__=='__main__': unittest.main()
