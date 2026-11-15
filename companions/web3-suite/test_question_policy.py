import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from freellmapi import Agent
from questions import prose_choice, validate_question


def reply(text=None,calls=None):
    message={'role':'assistant','content':text}
    if calls: message['tool_calls']=calls
    return {'choices':[{'message':message}]}


def call(name='ask_user',question='Quale piattaforma?',ident='q'):
    args={'question':question,'options':['Browser','Seeker']} if name=='ask_user' else {'path':'x','content':'bad'}
    return {'id':ident,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}


class QuestionPolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.a=Agent(self.temp.name,'http://localhost','test','auto')
        self.a.session=Path(self.temp.name)/'session.json'; self.a.context_limit=128000

    def test_prose_converted_without_display_or_archive(self):
        draft='RPC pubblico o privato?\nQuale genere preferisci?\nQuali prezzi?'
        responses=[reply(draft),reply(calls=[call()]),reply('Scelta ricevuta.')]
        with patch.object(self.a,'api',side_effect=responses) as api, patch('freellmapi.ask_question',return_value={'answer':'Seeker','cancelled':False}),patch('freellmapi.output') as out:
            self.a.turn('Prepariamo il gioco')
        self.assertFalse(any(draft in str(c) for c in out.call_args_list))
        self.assertFalse(any(draft in str(m) for m in self.a.messages))
        self.assertEqual(api.call_args_list[1].args[1]['tool_choice']['function']['name'],'ask_user')
        self.assertIn('Seeker',str(api.call_args_list[2].args[1]['messages']))

    def test_batch_defers_other_questions_and_mutations(self):
        calls=[call('write_file'),call(ident='q1'),call(question='Quale genere?',ident='q2')]
        with patch.object(self.a,'api',side_effect=[reply('Quale piattaforma? Quale genere?',calls),reply('Va bene.')]),patch('freellmapi.ask_question',return_value={'answer':'Browser','cancelled':False}) as ask:
            self.a.turn('Configura il gioco')
        ask.assert_called_once()
        self.assertFalse((Path(self.temp.name)/'x').exists())
        tools=[m['content'] for m in self.a.messages if m['role']=='tool']
        self.assertIn('rinviata',tools[0]); self.assertIn('rinviata',tools[2])

    def test_bad_native_question_is_repaired(self):
        responses=[reply(calls=[call(question='RPC pubblico? Quale genere?')]),reply('Ricevuto.')]
        with patch.object(self.a,'api',side_effect=responses) as api,patch('questions.ui.fancy',return_value=False),patch('builtins.input',return_value='1'):
            self.a.turn('Configura')
        self.assertEqual(api.call_count,2)
        self.assertNotIn('Errore:',''.join(m.get('content') or '' for m in self.a.messages))
        self.assertIn('Browser',self.a.messages[2]['content'])
        self.assertIn('Ricevuto.',self.a.messages[-1]['content'])
        self.assertEqual(self.a.turn_outcome,'completed')

    def test_repair_attempts_are_bounded(self):
        with patch.object(self.a,'api',return_value=reply('Quale genere vuoi?')) as api,patch('freellmapi.output') as out:
            self.a.turn('Prepara un gioco')
        self.assertEqual(api.call_count,3)
        self.assertIn('Quale genere vuoi?',self.a.messages[-1]['content'])
        self.assertEqual(self.a.turn_outcome,'completed')
        self.assertTrue(any('completo il task' in str(c) for c in out.call_args_list))
        self.assertFalse(any('non ha prodotto' in str(c) for c in out.call_args_list))

    def test_unsalvageable_question_still_delivers_the_task(self):
        bad={'id':'q','type':'function','function':{'name':'ask_user','arguments':json.dumps({'question':'Quale genere?','options':['Browser','Altro']})}}
        responses=[reply(calls=[bad]),reply(calls=[bad]),reply(calls=[bad]),reply('Gioco pronto, genere non scelto.')]
        with patch.object(self.a,'api',side_effect=responses),patch('freellmapi.ask_question') as ask:
            self.a.turn('Prepara un gioco')
        ask.assert_not_called()
        self.assertEqual(self.a.operational_tool_errors,0)
        self.assertIn('Gioco pronto',self.a.messages[-1]['content'])
        self.assertEqual(self.a.turn_outcome,'completed')

    def test_finished_answer_with_one_question_is_delivered(self):
        text='Ho completato la modifica del parser e verificato il caso vuoto. '+('Il comportamento resta stabile. '*12)+'Resta un dubbio sul nome del file?'
        self.assertGreater(len(text),240)
        self.assertFalse(prose_choice(text))
        with patch.object(self.a,'api',return_value=reply(text)) as api:
            self.a.turn('Sistema il parser')
        self.assertEqual(api.call_count,1)
        self.assertEqual(self.a.turn_outcome,'completed')
        self.assertIn('parser',self.a.messages[-1]['content'])

    def test_cancel_after_repair_stops(self):
        with patch.object(self.a,'api',side_effect=[reply('Scegli Browser oppure Seeker'),reply(calls=[call()])]) as api,patch('freellmapi.ask_question',return_value={'cancelled':True}):
            self.a.turn('Configura')
        self.assertEqual(api.call_count,2)

    def test_open_answer_and_duplicate_other_validation(self):
        from questions import ask
        with patch('questions.ui.fancy',return_value=False),patch('builtins.input',side_effect=['3','La mia alternativa']):
            self.assertEqual(ask('Piattaforma?',['Browser','Seeker'])['answer'],'La mia alternativa')
        with self.assertRaises(ValueError): validate_question('Piattaforma?',['Browser','Altro'])
        with self.assertRaises(ValueError): validate_question('1. RPC\n2. Genere',['A','B'])

    def test_sequence_blank_is_not_a_second_question(self):
        from questions import ask
        question='Domanda 4: Quale numero manca? 3, 6, 12, 24, ?, 96'
        validate_question(question,['36','48','72','60'])
        with patch('questions.ui.fancy',return_value=False),patch('builtins.input',return_value='2'):
            result=ask(question,['36','48','72','60'])
        self.assertEqual(result['answer'],'48')
        self.assertIn('?,',result['question'])
        with self.assertRaises(ValueError): validate_question('Quale mercato? Quale timeframe?',['A','B'])
        with patch('questions.ui.fancy',return_value=False),patch('builtins.input',return_value='1'):
            kept=ask('Quale mercato? Quale timeframe?',['Spot','Futures'])
        self.assertEqual(kept['question'],'Quale mercato?')
        self.assertEqual(kept['answer'],'Spot')

    def test_normal_answer_and_code_not_intercepted(self):
        text='Endpoint: https://example.com/?q=1\n```js\nconst a = ok ? 1 : 0;\n```'
        self.assertFalse(prose_choice(text))
        with patch.object(self.a,'api',return_value=reply(text)) as api:
            self.a.turn('Mostra il codice')
        self.assertEqual(api.call_count,1)

    def test_stream_is_not_rendered_before_validation(self):
        raw='data: '+json.dumps({'choices':[{'delta':{'content':'RPC pubblico? Genere?'},'finish_reason':'stop'}]})+'\n\ndata: [DONE]\n\n'
        response=io.BytesIO(raw.encode()); response.headers={'Content-Type':'text/event-stream'}
        self.a._hold_question_text=True
        with patch('urllib.request.build_opener') as opener,patch('freellmapi.ui.stream_text') as render:
            opener.return_value.open.return_value=response
            result=self.a._api('chat/completions',{'stream':True})
        render.assert_not_called(); self.assertFalse(result['_streamed'])
        self.assertIn('Genere?',result['choices'][0]['message']['content'])

    def test_memory_error_does_not_disable_question_gate(self):
        from memory_vault import Vault
        self.a.memory=Vault(Path(self.temp.name)/'vault',self.temp.name)
        checkpoint={'id':'c','type':'function','function':{'name':'context_checkpoint','arguments':'{}'}}
        responses=[reply(calls=[checkpoint]),reply('RPC pubblico? Quale genere?'),reply(calls=[call()]),reply('Ricevuto.')]
        with patch.object(self.a,'api',side_effect=responses) as api,patch('freellmapi.ask_question',return_value={'answer':'Seeker','cancelled':False}):
            self.a.turn('Sviluppa il gioco')
        self.assertEqual(api.call_args_list[2].args[1]['tool_choice']['function']['name'],'ask_user')
        self.assertIn('inspection_required',self.a.messages[2]['content'])


if __name__=='__main__': unittest.main()
