import unittest
from unittest.mock import Mock,patch
from unified_web_search import SearchGateway,SearchFailure

CATALOG={'data':[{'id':x,'available':True} for x in ('tavily-search','exa-search','firecrawl-search')]}
def found(engine):return {'engine':engine,'provider':engine.split('-')[0],'results':[{'title':'Test primary source','url':'https://example.org/current','content':'Observed test excerpt','published_date':'2026-11-14'}]}

class UnifiedSearchTests(unittest.TestCase):
    def test_fornai_engines_use_available_catalog_and_hop_once_after_rate_limit(self):
        client=SearchGateway('http://127.0.0.1:31415/v1','do-not-print-key')
        with patch.object(client,'_call',side_effect=[CATALOG,SearchFailure(429,'rate_limit_exceeded'),found('exa-search')]) as call:
            result=client.search('aviation news',news=True)
        self.assertEqual(result['engine'],'exa-search');self.assertEqual(result['attempted_engines'],['tavily-search','exa-search'])
        self.assertEqual(call.call_count,3);self.assertEqual(call.call_args_list[1].args[1]['topic'],'news');self.assertFalse(call.call_args_list[1].args[1]['include_answer'])
        self.assertEqual(result['results'][0]['published'],'2026-11-14')
        with patch.object(client,'_call',return_value=found('exa-search')) as again:
            self.assertEqual(client.search('another question')['attempted_engines'],['exa-search'])
        again.assert_called_once()

    def test_unavailable_engines_are_never_called(self):
        client=SearchGateway('http://localhost:1','synthetic')
        with patch.object(client,'_call',return_value={'data':[{'id':'tavily-search','available':False}]}) as call:r=client.search('query')
        call.assert_called_once();self.assertEqual(r['attempted_engines'],[]);self.assertEqual(r['results'],[])

    def test_all_provider_failures_are_bounded_and_do_not_invent_sources(self):
        client=SearchGateway('http://localhost:1','synthetic')
        with patch.object(client,'_call',side_effect=[CATALOG,SearchFailure(502),SearchFailure(429),SearchFailure(504)]) as call:r=client.search('query')
        self.assertEqual(call.call_count,4);self.assertEqual(len(r['attempted_engines']),3);self.assertEqual(r['results'],[])
        self.assertEqual(len(r['errors']),3)

    def test_wrong_engine_cannot_supply_false_proof(self):
        client=SearchGateway('http://localhost:1','synthetic')
        with patch.object(client,'_call',side_effect=[{'data':[{'id':'tavily-search','available':True}]},found('other-engine')]):r=client.search('query')
        self.assertEqual(r['results'],[]);self.assertIn('non corrispondente',r['errors'][0])

    def test_malformed_provider_urls_fail_over_instead_of_entering_context(self):
        client=SearchGateway('http://localhost:1','synthetic');bad=found('tavily-search');bad['results']=[{'url':'https://user:password@example.org','content':'not a public source'},{'url':'https://[broken','content':'invalid'}]
        with patch.object(client,'_call',side_effect=[CATALOG,bad,found('exa-search')]):r=client.search('query')
        self.assertEqual(r['engine'],'exa-search');self.assertEqual(len(r['results']),1)

if __name__=='__main__':unittest.main()
