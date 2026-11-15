import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch,Mock
from freellmapi import Agent


class IntentContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.agent=Agent(self.tmp.name,'http://localhost','test','auto'); self.agent.mode='auto'; self.agent.session=Path(self.tmp.name)/'s.json'
    def test_two_agents_cannot_be_replaced_with_plain_answer(self):
        response={'choices':[{'message':{'role':'assistant','content':'Ho letto io entrambi i file.'}}]}
        with patch.object(self.agent,'api',return_value=response) as api,patch('freellmapi.output') as out:
            self.agent.turn('Usa due assistenti in parallelo per leggere alpha.txt e beta.txt')
        self.assertEqual(self.agent.turn_outcome,'incomplete')
        self.assertEqual(api.call_args.args[1]['tool_choice']['function']['name'],'agent_start')
        self.assertFalse(any('Ho letto io entrambi' in str(c) for c in out.call_args_list))
    def test_connected_resources_are_discovered_before_model(self):
        client=Mock(); client.request.return_value={'resources':[{'uri':'note://one','name':'Nota'}]}
        self.agent.extensions.clients['local']=client
        response={'choices':[{'message':{'role':'assistant','content':'Risorsa individuata.'}}]}
        with patch.object(self.agent,'api',return_value=response) as api:
            self.agent.turn('Leggi la nota esposta dal server locale')
        client.request.assert_called_once_with('resources/list',{})
        self.assertTrue(any(m.get('role')=='tool' and 'note://one' in m.get('content','') for m in api.call_args.args[1]['messages']))
