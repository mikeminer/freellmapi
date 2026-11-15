import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from freellmapi import Agent
from session_store import Sessions


class QuestionDedupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.a=Agent(self.tmp.name,'http://localhost','test','auto')
        self.a.session=Path(self.tmp.name)/'s.json'; self.a.context_limit=131072
        self.args={'question':'Che tipo di segnale preferisci per BTC/USDC?','options':['Breve termine','Medio termine']}

    def reply(self,text=None,question=False):
        m={'role':'assistant','content':text}
        if question: m['tool_calls']=[{'id':'q','function':{'name':'ask_user','arguments':json.dumps(self.args)}}]
        return {'choices':[{'message':m}]}

    def test_duplicate_reuses_real_answer(self):
        with patch('freellmapi.ask_question',return_value={'question':self.args['question'],'answer':'Breve termine','cancelled':False}) as ask:
            first=json.loads(self.a.tool('ask_user',self.args))
            second=json.loads(self.a.tool('ask_user',dict(self.args,question=self.args['question'].upper())))
        ask.assert_called_once(); self.assertEqual(first['answer'],second['answer']); self.assertTrue(second['reused'])

    def test_different_pair_not_reused(self):
        with patch('freellmapi.ask_question',side_effect=[{'answer':'Breve termine','cancelled':False},{'answer':'Medio termine','cancelled':False}]) as ask:
            self.a.tool('ask_user',self.args)
            result=json.loads(self.a.tool('ask_user',dict(self.args,question=self.args['question'].replace('BTC','ETH'))))
        self.assertEqual(ask.call_count,2); self.assertEqual(result['answer'],'Medio termine')

    def test_reported_repair_loop_does_not_reask(self):
        responses=[self.reply(question=True),self.reply('Vuoi un segnale breve o medio?'),self.reply(question=True),self.reply('Analisi basata sulla scelta già ricevuta.')]
        with patch.object(self.a,'api',side_effect=responses) as api,patch('freellmapi.ask_question',return_value={'answer':'Breve termine','cancelled':False}) as ask:
            self.a.turn('Analizza BTC/USDC')
        ask.assert_called_once()
        self.assertNotIn('tool_choice',api.call_args_list[2].args[1])
        self.assertIn('Analisi basata',self.a.messages[-1]['content'])

    def test_answer_survives_compaction_and_restart(self):
        store=Sessions(Path(self.tmp.name)/'home'); ident=store.create(self.tmp.name); store.activate(self.a,ident)
        with patch('freellmapi.ask_question',return_value={'answer':'Breve termine','cancelled':False}): self.a.tool('ask_user',self.args)
        self.a.messages=[{'role':'assistant','content':'Compacted summary'}]; self.a.save()
        b=Agent(self.tmp.name,'http://localhost','test','other-model'); store.activate(b,ident)
        with patch('freellmapi.ask_question') as ask: result=json.loads(b.tool('ask_user',self.args))
        ask.assert_not_called(); self.assertEqual(result['answer'],'Breve termine')
        new=store.create(self.tmp.name); store.activate(b,new)
        self.assertEqual(b.question_answers,{})

    def test_cancel_is_not_a_saved_choice(self):
        with patch('freellmapi.ask_question',return_value={'cancelled':True}) as ask:
            self.a.tool('ask_user',self.args); self.a.tool('ask_user',self.args)
        self.assertEqual(ask.call_count,2)

    def test_endless_duplicates_stop_without_more_input(self):
        self.a.mode='auto'
        with patch.object(self.a,'api',return_value=self.reply(question=True)) as api,patch('freellmapi.ask_question',return_value={'answer':'Breve termine','cancelled':False}) as ask:
            self.a.turn('Analizza BTC/USDC')
        ask.assert_called_once(); self.assertEqual(api.call_count,5)
        self.assertEqual(self.a.turn_outcome,'completed')
        self.assertIn('Breve termine',self.a.messages[-1]['content'])


if __name__=='__main__': unittest.main()
