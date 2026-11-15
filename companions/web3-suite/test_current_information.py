"""Replay the exact Seeker news refusal; no model API, wallet or remote writes."""
import copy,io,json,tempfile,unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
import freellmapi as f
from session_store import Sessions

QUESTION='cosa è successo al sistema aereo statunitense?'
DENIAL='Mi dispiace, non ho accesso a informazioni aggiornate su questo argomento.'
RESULT={'query':'sistema aereo statunitense','engine':'Synthetic News RSS','retrieved_utc':'2026-11-14T22:00:00+00:00','results':[{'title':'Aviazione USA: comunicato di aggiornamento','url':'https://example.org/aviation-update','snippet':'Aggiornamento sintetico per il test, non un fatto di cronaca reale.','published':'Sat, 14 Nov 2026 21:00:00 GMT'}]}

class CurrentInformationTests(unittest.TestCase):
    def run_turn(self,answers,result=RESULT):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);home=Path(t.name)
        with patch.object(f,'HOME',home):
            a=f.Agent(home,'http://127.0.0.1:9','synthetic','auto');a.auto_skills=False;a.streaming=False;a.context_limit=200000
            sessions=Sessions(home);sessions.activate(a,sessions.create(home))
            with patch.object(a,'api',side_effect=[{'choices':[{'message':{'role':'assistant','content':text}}]} for text in answers]) as api,patch('unified_web_search.SearchGateway.search',return_value={'results':[],'errors':[]}),patch('freellmapi.search_web',return_value=copy.deepcopy(result)) as search,patch('hyperliquid_tools.submit',side_effect=AssertionError('No trading')),patch('freellmapi.output'),redirect_stdout(io.StringIO()):
                a.turn(QUESTION)
        return a,api,search

    def test_exact_phone_refusal_performs_search_before_answer_and_stops_after_one_repair(self):
        a,api,search=self.run_turn([DENIAL,DENIAL])
        search.assert_called_once();self.assertEqual(api.call_count,2)
        self.assertIn('https://example.org/aviation-update',a.messages[-1]['content'])
        self.assertNotIn('non ho accesso',a.messages[-1]['content'])
        first=api.call_args_list[0].args[1]
        self.assertTrue(all(t['function']['name']=='ask_user' for t in first['tools']))
        self.assertTrue(any(m.get('role')=='tool' and 'aviation-update' in m.get('content','') for m in first['messages']))
        self.assertEqual(a.messages[0],{'role':'user','content':QUESTION})
        self.assertEqual(sum(m.get('role')=='tool' for m in a.messages),1)

    def test_successful_answer_uses_source_context_without_extra_request(self):
        a,api,search=self.run_turn(['Il comunicato di test è qui: https://example.org/aviation-update'])
        self.assertEqual(api.call_count,1);search.assert_called_once();self.assertEqual(a.turn_outcome,'completed')
        self.assertTrue(any('CURRENT WEB EVIDENCE' in (m.get('content') or '') for m in api.call_args.args[1]['messages']))

    def test_local_commands_and_historical_questions_do_not_trigger_news(self):
        from current_information import request
        from tool_routing import suggested
        self.assertIn('web_search',suggested(QUESTION))
        for q in ('annulla ultima modifica','riprendi ultimo test','cosa è successo nella ultima sessione sul Seeker?','oggi crea un gioco','scrivi un articolo sulle ultime notizie','What happened in 2001?'):
            with self.subTest(question=q):self.assertIsNone(request(q))

    def test_news_feed_retains_publisher_dates_and_rejects_irrelevant_items(self):
        from web_search import search
        xml='<rss><channel><item><title>Sistema aereo statunitense: aggiornamento</title><link>https://example.org/aviation-update</link><pubDate>Sat, 14 Nov 2026 21:00:00 GMT</pubDate><source>Test source</source></item><item><title>Football match</title><link>https://example.org/sports</link></item></channel></rss>'
        with patch('web_search.fetch',return_value=xml) as fetch:r=search('sistema aereo statunitense',news=True)
        fetch.assert_called_once();self.assertEqual(r['engine'],'Google News RSS');self.assertEqual(len(r['results']),1)
        self.assertIn('published',r['results'][0]);self.assertEqual(r['results'][0]['publisher'],'Test source')

    def test_model_repeating_search_after_native_evidence_is_bounded_without_reexecution(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);home=Path(t.name)
        with patch.object(f,'HOME',home):
            a=f.Agent(home,'http://localhost:9','synthetic','auto');a.auto_skills=False;a.streaming=False;a.context_limit=200000
            sessions=Sessions(home);sessions.activate(a,sessions.create(home))
            call={'role':'assistant','tool_calls':[{'id':'repeat-search','type':'function','function':{'name':'web_search','arguments':json.dumps({'query':'another query'})}}]}
            with patch.object(a,'api',return_value={'choices':[{'message':call}]}) as api,patch('unified_web_search.SearchGateway.search',return_value=copy.deepcopy(RESULT)) as search,patch('freellmapi.output'),redirect_stdout(io.StringIO()):a.turn(QUESTION)
        self.assertEqual(api.call_count,2);search.assert_called_once()
        self.assertEqual(sum(m.get('role')=='tool' for m in a.messages),1)
        self.assertIn('https://example.org/aviation-update',a.messages[-1]['content']);self.assertEqual(a.turn_outcome,'incomplete')

    def test_unavailable_sources_report_the_observed_error_without_fake_current_facts(self):
        result={'query':'sistema aereo statunitense','results':[],'errors':['Synthetic provider: timeout']}
        a,api,search=self.run_turn([DENIAL,DENIAL],result)
        search.assert_called_once();self.assertEqual(api.call_count,2)
        self.assertIn('Synthetic provider: timeout',a.messages[-1]['content'])
        self.assertEqual(a.turn_outcome,'incomplete');self.assertNotIn('https://example.org',a.messages[-1]['content'])

if __name__=='__main__':unittest.main()
