import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from questions import ask,normalize_question,validate_question
from web_search import search,relevant
from freellmapi import Agent
from memory_vault import Vault


class MarketRequestRegressionTests(unittest.TestCase):
    def test_reported_clarification_shows_once_with_open_answer(self):
        question='Cosa intendi con "segnale per pump/usdc"? Vuoi un bot che monitori nuovi token pump.fun e ti invii alert su USDC, o un’altra cosa?'
        with patch('questions.ui.fancy',return_value=False),patch('builtins.input',side_effect=['3','Analisi tecnica PUMP/USDC, non un bot']):
            result=ask(question,['Analisi della coppia','Bot di monitoraggio'])
        self.assertEqual(result['question'],'Cosa intendi con "segnale per pump/usdc"?')
        self.assertEqual(result['answer'],'Analisi tecnica PUMP/USDC, non un bot')

    def test_independent_questions_still_rejected(self):
        question='Quale mercato? Quale timeframe?'
        self.assertEqual(normalize_question(question),question)
        with self.assertRaises(ValueError): validate_question(question,['A','B'])

    def test_irrelevant_bing_results_are_not_returned(self):
        rss='<rss><channel><item><title>TikTok - Make Your Day</title><link>https://www.tiktok.com/</link><description>trends here</description></item><item><title>Andrea Kimi Antonelli</title><link>https://it.wikipedia.org/wiki/Andrea_Kimi_Antonelli</link></item></channel></rss>'
        with patch('web_search.fetch',side_effect=[RuntimeError('anti-bot'),rss]):
            result=search('pump.usdc trading signal Solana today')
        self.assertEqual(result['results'],[])
        self.assertTrue(any('fuori tema' in e for e in result['errors']))
        self.assertTrue(relevant({'title':'PUMP/USDC market','url':'https://example.com/pump-usdc'},'PUMP USDC signal'))

    def test_simple_pair_request_does_not_discover_skills(self):
        a=Agent('.','http://localhost','test','auto')
        with patch.object(a,'api') as api,patch('skill_router.catalog') as catalog:
            a.skill_router.run(a,'dammi un segnale per pump/usdc',print)
        api.assert_not_called(); catalog.assert_not_called()

    def test_explicit_bot_request_can_route_skills(self):
        a=Agent('.','http://localhost','test','auto')
        with patch.object(a,'api',return_value={'choices':[{'message':{'content':'{"ids":[],"web_query":""}'}}]}) as api,patch('skill_router.catalog',return_value=[]):
            a.skill_router.run(a,'crea un bot di segnali per PUMP/USDC',print)
        api.assert_called_once()

    def test_memory_does_not_match_only_generic_signal_words(self):
        with tempfile.TemporaryDirectory() as d:
            v=Vault(Path(d)/'vault',d)
            v.remember('Segnale di errore nel gioco space shooter.','project')
            self.assertEqual(v.search('segnale PUMP/USDC'),'')
            v.remember('PUMP USDC: preferisco analisi su timeframe orario.')
            self.assertIn('timeframe orario',v.search('segnale PUMP/USDC'))


if __name__=='__main__': unittest.main()
