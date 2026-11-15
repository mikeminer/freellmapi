import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from questions import explicit_choice,wants_questionnaire

class ExplicitChoiceTests(unittest.TestCase):
 def test_quiz_intent(self): self.assertTrue(wants_questionnaire('preparami un test di intelligenza QI a 10 domande e sottoponimele'))
 def test_extract_preserves_question_and_options(self):
  self.assertEqual(explicit_choice('Quale numero?\nA) 10\nB) 20'),{'question':'Quale numero?','options':['10','20']})
 def test_rejects_multiple_questions_or_answer_key(self):
  self.assertIsNone(explicit_choice('Quale? Quale altro?\nA) Uno\nB) Due'))
  self.assertIsNone(explicit_choice('Quale?\nA) Uno\nB) Due\nRisposta corretta: A'))
 def test_native_menu_without_conversion_api_retry(self):
  from freellmapi import Agent
  with tempfile.TemporaryDirectory() as tmp:
   agent=Agent(tmp,'http://localhost','test','auto');agent.session=Path(tmp)/'session.json'
   responses=[{'choices':[{'message':{'role':'assistant','content':'Quale numero?\nA) 10\nB) 20'}}]},{'choices':[{'message':{'role':'assistant','content':'Risposta registrata.'}}]}]
   with patch.object(agent,'api',side_effect=responses) as api,patch('freellmapi.ask_question',return_value={'question':'Quale numero?','answer':'20','cancelled':False}) as ask:
    agent.turn('Sottoponimi un quiz')
   ask.assert_called_once_with('Quale numero?',['10','20']);self.assertEqual(api.call_count,2)
   self.assertIn('20',str(agent.messages))
