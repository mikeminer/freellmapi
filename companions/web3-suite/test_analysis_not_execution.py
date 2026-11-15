import json,time,unittest
from unittest.mock import patch
from financial_completion import execution_verb,requirement
from market_routing import intent
import hyperliquid_tools as h,hyperliquid_protection as protection
import test_hyperliquid_protection as fixtures

class ProposalIntentTests(unittest.TestCase):
    def test_trade_noun_and_direction_labels_are_never_execution_commands(self):
        for text in ('Hyperliquid: hyperliquid analizza sol e formulami un trade con tp sicuro','proponimi un trade SOL long o short','analyze SOL and tell me whether buy or sell','suggest a trade BTC/USDC','trova il migliore trade su Hyperliquid'):
            with self.subTest(text=text):
                self.assertFalse(execution_verb(text));self.assertIsNone(requirement(text,{'venue':'hyperliquid'}))
    def test_real_imperatives_and_mixed_analysis_then_execution_still_work(self):
        for text in ('trade SOL on Hyperliquid','Hyperliquid: buy SOL','please sell SOL','analizza SOL e poi esegui il trade','analyze BTC and buy','fai un trade SOL','trada PUMP','invia l’ordine SOL'):
            with self.subTest(text=text):self.assertTrue(execution_verb(text));self.assertEqual(requirement(text,{'venue':'hyperliquid'}),'order')
    def test_real_prompt_cannot_reach_plan_parsing_keys_or_exchange(self):
        from types import SimpleNamespace
        from hyperliquid_execution import execute
        text='Hyperliquid: hyperliquid analizza sol e formulami un trade con tp sicuro'
        agent=SimpleNamespace(mode='auto',depth=0,messages=[{'role':'user','content':text}],current_market_request=intent(text))
        with patch('hyperliquid_execution.parse_plan',side_effect=AssertionError('Must stop before plan')),patch.object(h,'info',side_effect=AssertionError('No exchange call')),self.assertRaises(h.HLProblem) as error:
            execute(agent,'unused',{'plan':'{}'})
        self.assertEqual(error.exception.code,'execution_not_requested')

class TransientProtectionTests(unittest.TestCase):
    setUp=fixtures.ProtectionTests.setUp;order=fixtures.ProtectionTests.order;stored=fixtures.ProtectionTests.stored;sdk=fixtures.ProtectionTests.sdk;exchange_read=fixtures.ProtectionTests.exchange_read
    response=fixtures.ProtectionTests.response;key=fixtures.ProtectionTests.key;settings=fixtures.ProtectionTests.settings;arm=fixtures.ProtectionTests.arm
    def test_real_shape_filled_entry_two_failed_reads_then_active_exits_no_resend(self):
        row=self.order();saved=self.stored(row,'filled',self.sdk(entry={'filled':{'oid':1,'totalSz':'.001'}},tp='waitingForTrigger',sl='waitingForTrigger'))
        reader=self.exchange_read(row['body'],{'entry':'filled','tp':'open','sl':'open'});seen=set();failed={row['body']['bracket'][n+'_cloid'] for n in ('tp','sl')}
        def transient(p,q):
            if q['oid'] in failed and q['oid'] not in seen:
                seen.add(q['oid']);raise h.HLProblem('info_unavailable','Synthetic HTTP 429',http_status=429,retryable=True)
            return reader(p,q)
        with patch.object(h,'info',side_effect=transient) as read,patch('hl_credentials.get',side_effect=AssertionError('No signing key')),patch('hyperliquid_signer.execute',side_effect=AssertionError('No order send')):
            result=protection.finalize(self.home,{'receipt':saved})
        self.assertEqual(result['protection']['state'],'active');self.assertEqual(read.call_count,5)
        self.assertEqual(result['protection']['read_retries'],{'tp':1,'sl':1});self.assertEqual(result['receipt']['body'],row['body'])

if __name__=='__main__':unittest.main()
