import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from freellmapi import Agent
from questions import ask,wants_questionnaire

def response(question=None):
 if question: return {'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':question,'type':'function','function':{'name':'ask_user','arguments':json.dumps({'question':question,'options':['A','B']})}}]}}]}
 return {'choices':[{'message':{'role':'assistant','content':'Pronto'}}]}

class QuestionTests(unittest.TestCase):
 def test_other_and_cancel(self):
  with patch('questions.ui.fancy',return_value=False),patch('builtins.input',side_effect=['3','La mia idea']):
   self.assertEqual(ask('Quale?',['A','B'])['answer'],'La mia idea')
  with patch('questions.ui.fancy',return_value=False),patch('builtins.input',return_value=''):
   self.assertTrue(ask('Quale?',['A','B'])['cancelled'])
 def test_router_skips_native_question_request(self):
  a=Agent('.','http://localhost','test','auto')
  prompt='fammi le domande dove posso selezionare una risposta io con le frecce ed invio'
  with patch.object(a,'api') as api:
   a.skill_router.run(a,prompt,lambda _:None); api.assert_not_called()
 def test_question_sequence_waits_for_answers(self):
  with tempfile.TemporaryDirectory() as d:
   a=Agent(d,'http://localhost','test','auto'); a.session=Path(d)/'session.json'; a.context_limit=131072
   with patch.object(a,'api',side_effect=[response('Piattaforma?'),response('Genere?'),response()]) as api,patch('questions.ui.fancy',return_value=False),patch('builtins.input',side_effect=['2','1']):
    a.turn('Fammi le domande con le frecce')
   answers=[json.loads(m['content']) for m in a.messages if m['role']=='tool']
   self.assertEqual([a['answer'] for a in answers],['B','A'])
   self.assertEqual(api.call_args_list[0].args[1]['tool_choice']['function']['name'],'ask_user')
   self.assertIn('B',api.call_args_list[1].args[1]['messages'][3]['content'])
 def test_cancel_stops(self):
  with tempfile.TemporaryDirectory() as d:
   a=Agent(d,'http://localhost','test','auto'); a.session=Path(d)/'s.json'
   with patch.object(a,'api',return_value=response('Scegli')) as api,patch('questions.ui.fancy',return_value=False),patch('builtins.input',return_value=''):
    a.turn('Fammi le domande'); self.assertEqual(api.call_count,1)
 def test_invalid_options(self):
  with self.assertRaises(ValueError): ask('Scegli',['unica'])

if __name__=='__main__': unittest.main()
