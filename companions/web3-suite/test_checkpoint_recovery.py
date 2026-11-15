import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from freellmapi import Agent, TOOLS
from memory_vault import Vault
from project_context import CHECKPOINT_KINDS


class CheckpointRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.agent=Agent(self.temp.name,'http://localhost','test','auto')
        self.agent.context_limit=128000
        self.agent.session=Path(self.temp.name)/'session.json'
        self.agent.memory=Vault(Path(self.temp.name)/'vault',self.temp.name)
        self.agent.memory.operational.ingest([{'role':'user','content':'Risorse incluse nell’APK, tutto locale'}],'session')
        self.source=next(iter(self.agent.memory.operational.load()['events']))
        self.valid={'kind':'decision','text':'Risorse incluse nell’APK.','sources':[self.source]}

    def inspect(self):
        return json.loads(self.agent.tool('context_inspect',{}))

    def error(self,args):
        result=self.agent.tool('context_checkpoint',args)
        self.assertTrue(result.startswith('Errore: '),result)
        return json.loads(result.removeprefix('Errore: '))

    def test_schema_constrains_categories_and_ids(self):
        schema=next(t for t in TOOLS if t['function']['name']=='context_checkpoint')['function']['parameters']
        self.assertEqual(schema['properties']['kind']['enum'],list(CHECKPOINT_KINDS))
        self.assertFalse(schema['additionalProperties'])
        self.assertEqual(schema['properties']['text']['maxLength'],3000)
        self.assertEqual(schema['properties']['sources']['minItems'],1)

    def test_inspection_required_and_bad_retry_does_not_write(self):
        self.assertEqual(self.error(self.valid)['code'],'inspection_required')
        self.inspect()
        self.assertEqual(self.error(dict(self.valid,kind='architecture'))['code'],'invalid_kind')
        with patch.object(self.agent.memory.operational,'checkpoint') as write:
            self.assertEqual(self.error(self.valid)['code'],'inspection_required')
            write.assert_not_called()
        self.assertEqual(self.agent.memory.operational.load()['claims'],[])
        self.inspect()
        result=json.loads(self.agent.tool('context_checkpoint',self.valid))
        self.assertTrue(result['ok'])
        self.assertEqual(len(self.agent.memory.operational.load()['claims']),1)

    def test_bad_payloads_are_actionable_and_do_not_crash(self):
        cases=[({'kind':[]},'invalid_kind','kind'),({'text':None},'invalid_text','text'),
               ({'text':'   '},'invalid_text','text'),({'text':'x'*3001},'text_too_long','text'),
               ({'sources':self.source},'invalid_sources','sources'),({'sources':[{}]},'invalid_sources','sources'),
               ({'sources':None},'invalid_sources','sources'),({'sources':[self.source,self.source]},'invalid_sources','sources'),
               ({'sources':['a'*20]},'unknown_sources','sources'),({'replaces':''},'invalid_replaces','replaces'),
               ({'replaces':{}},'invalid_replaces','replaces'),({'replaces':'f'*12},'inactive_replacement','replaces')]
        for changes,code,field in cases:
            with self.subTest(code=code,changes=changes):
                self.inspect(); error=self.error(dict(self.valid,**changes))
                self.assertEqual(error['code'],code); self.assertEqual(error['field'],field)
                self.assertEqual(error['next_tool'],'context_inspect')

    def test_missing_and_extra_fields(self):
        for payload in ({'kind':'state'},dict(self.valid,unknown=True),[],None):
            self.inspect(); self.assertIn(self.error(payload)['code'],('invalid_fields','invalid_arguments'))

    def test_superseded_id_refresh_and_correction(self):
        self.inspect()
        first=json.loads(self.agent.tool('context_checkpoint',self.valid))['claim_id']
        second=json.loads(self.agent.tool('context_checkpoint',dict(self.valid,text='Nuova decisione',replaces=first)))['claim_id']
        self.assertEqual(self.error(dict(self.valid,replaces=first))['code'],'inactive_replacement')
        state=self.inspect()
        self.assertEqual([c['id'] for c in state['claims']],[second])
        self.assertTrue(json.loads(self.agent.tool('context_checkpoint',dict(self.valid,replaces=second)))['ok'])

    def test_request_cannot_become_verified(self):
        self.inspect()
        self.assertEqual(self.error(dict(self.valid,kind='verified'))['code'],'verification_missing')

    def test_model_sequence_recovers_in_real_turn_loop(self):
        def response(name=None,args=None,content=None):
            msg={'role':'assistant','content':content}
            if name:
                msg['tool_calls']=[{'id':str(len(responses))+name,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]
            return {'choices':[{'message':msg}]}
        responses=[]
        for name,args in [('context_inspect',{}),('context_checkpoint',dict(self.valid,kind='architecture')),
                          ('context_checkpoint',self.valid),('context_inspect',{}),('context_checkpoint',self.valid)]:
            responses.append(response(name,args))
        responses.append(response(content='Scelta salvata.'))
        with patch.object(self.agent,'api',side_effect=responses):
            self.agent.turn('Ricorda la scelta dell’APK locale.')
        results=[m['content'] for m in self.agent.messages if m['role']=='tool']
        self.assertIn('invalid_kind',results[1])
        self.assertIn('recent_evidence',json.loads(results[2]))
        self.assertTrue(json.loads(results[3])['ok'])
        self.assertEqual(len(self.agent.memory.operational.load()['claims']),1)

    def test_error_forces_inspection_even_with_another_tool_in_batch(self):
        calls=[{'id':'c','function':{'name':'context_checkpoint','arguments':json.dumps(self.valid)}},
               {'id':'s','function':{'name':'set_plan','arguments':'{"plan":"continue"}'}}]
        responses=[{'choices':[{'message':{'role':'assistant','content':None,'tool_calls':calls}}]},
                   {'choices':[{'message':{'role':'assistant','content':'Stopped'}}]}]
        with patch.object(self.agent,'api',side_effect=responses) as api:
            self.agent.turn('Work')
        payload=api.call_args_list[1].args[1]
        calls={c['id']:c['function']['name'] for m in payload['messages'] for c in m.get('tool_calls',[])}
        self.assertTrue(any(m.get('role')=='tool' and calls.get(m.get('tool_call_id'))=='context_inspect' for m in payload['messages']))


if __name__=='__main__': unittest.main()
