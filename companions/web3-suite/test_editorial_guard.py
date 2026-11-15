import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from freellmapi import Agent
import editorial_guard
import social_tools

def response(text=None,call=None):
    msg={'role':'assistant','content':text}
    if call: msg['tool_calls']=[{'id':'save1','type':'function','function':{'name':'social_save_draft','arguments':json.dumps(call)}}]
    return {'choices':[{'message':msg}]}

class EditorialTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)
        self.agent=Agent(self.home,'http://localhost','test','auto')
        self.agent.session=self.home/'session.json';self.agent.context_limit=128000
    def test_exact_user_request_routes_natively(self):
        from tool_routing import suggested,simple_native_request
        prompt='Prepara un thread per il mio account X e salvalo come bozza @anonimocommando riguardo al mio gioco Seek & Strike'
        self.assertIn('social_save_draft',suggested(prompt));self.assertTrue(simple_native_request(prompt))
        self.assertTrue(editorial_guard.requested(prompt,[]))
    def test_translation_inherits_editorial_task(self):
        history=[{'role':'user','content':'Prepara un thread per il mio account X e salvalo come bozza'}]
        self.assertTrue(editorial_guard.requested('make it all i english',history))
        self.assertFalse(editorial_guard.requested('translate this Python function',[]))
    def test_respects_no_save_and_separate_publish_flow(self):
        self.assertFalse(editorial_guard.requested('Scrivi un tweet ma non salvare',[]))
        self.assertFalse(editorial_guard.requested('Scrivi e pubblica un tweet',[]))
        self.assertTrue(editorial_guard.requested('Prepara una bozza per account X ma non pubblicare',[]))
    def test_fake_save_repaired_and_marketing_not_question(self):
        args={'account':'anonimocommando','text':'Choose one of five classes.\n---\nBugs? Contact welcome@devfridge.cool.'}
        replies=[response('Certo, salvato come bozza. Choose your class!'),response('Choose your class!',args),response('Would you like me to publish?')]
        with patch('freellmapi.HOME',self.home),patch.object(self.agent,'api',side_effect=replies) as api,patch.object(self.agent.skill_router,'run') as skills,patch('freellmapi.output') as out:
            self.agent.turn('Prepara un thread per il mio account X e salvalo come bozza @anonimocommando')
        skills.assert_not_called()
        self.assertEqual(api.call_args_list[1].args[1]['tool_choice']['function']['name'],'social_save_draft')
        self.assertEqual(social_tools.drafts(self.home)[0]['text'],args['text'])
        self.assertFalse(any('Preparo una domanda' in str(c) for c in out.call_args_list))
        self.assertTrue(any('salvata e verificata' in str(c) for c in out.call_args_list))
    def test_no_save_cannot_claim_success_bounded(self):
        with patch('freellmapi.HOME',self.home),patch.object(self.agent,'api',return_value=response('Salvato!')) as api,patch('freellmapi.output') as out:
            self.agent.turn('Scrivi e salva una bozza per account X @alice')
        self.assertEqual(api.call_count,3)
        self.assertNotEqual(self.agent.turn_outcome,'completed')
        self.assertTrue(any('Bozza non salvata' in str(c) for c in out.call_args_list))
    def test_old_draft_not_evidence_for_translation(self):
        self.agent.messages=[{'role':'user','content':'Prepara e salva una bozza per account X @alice'}]
        social_tools.save(self.home,'Vecchio testo','alice')
        with patch('freellmapi.HOME',self.home),patch.object(self.agent,'api',return_value=response('Now in English. Saved.')) as api,patch('freellmapi.output'):
            self.agent.turn('Make it all in English')
        self.assertEqual(api.call_count,3);self.assertNotEqual(self.agent.turn_outcome,'completed')

if __name__=='__main__': unittest.main()
