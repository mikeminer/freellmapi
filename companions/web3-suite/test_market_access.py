"""Native market discovery and false capability denials, without real trading."""
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch,Mock

import freellmapi as f
import hyperliquid_tools as h
from market_routing import intent
from session_store import Sessions
from tool_routing import suggested,simple_native_request

SNAPSHOT={'venue':'Binance spot','symbol':'BTCUSDC','source':'https://data-api.binance.vision','ticker_24h':{'lastPrice':'68000'},'candles':[]}


class MarketAccessTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.home=Path(temp.name)

    def test_actual_seeker_prompts_enable_needed_tools_without_service_name(self):
        first='trova segnale buono con relativo TP sicuro per btc/usdc . valuta se fare long o short. ed esegui ordine con leva 40 800 usdc'
        self.assertIn('hyperliquid_read',suggested(first));self.assertIn('hyperliquid_preview',suggested(first))
        second='trovami segnale per btc/usdc'
        self.assertIn('market_snapshot',suggested(second));self.assertTrue(simple_native_request(second))

    def test_build_requests_and_other_explicit_venues_are_not_silently_rerouted(self):
        self.assertIsNone(intent('Crea un gioco su BTC/USDC con segnali'))
        self.assertIsNone(intent('Analizza BTC/USDC su Kraken'))
        self.assertFalse(intent('Analizza BTC/USDC, non inviare ordini')['trading'])

    def run_turn(self,prompt,responses,market=SNAPSHOT):
        with patch.object(f,'HOME',self.home):
            agent=f.Agent(self.home,'http://127.0.0.1:9','synthetic-only','auto')
            store=Sessions(self.home);store.activate(agent,store.create(self.home));agent.auto_skills=False;agent.context_limit=200000;agent.streaming=False
            def reply(text):return {'model':'scripted-cohere-regression','choices':[{'message':{'role':'assistant','content':text},'finish_reason':'stop'}]}
            side=market if isinstance(market,Exception) else None
            with patch.object(agent,'api',side_effect=[reply(text) for text in responses]) as api, \
                 patch('market_data.snapshot',side_effect=side,return_value=market) as read, \
                 patch.object(f,'output'),redirect_stdout(io.StringIO()):
                agent.turn(prompt)
            return agent,api,read

    def test_native_read_precedes_model_and_false_denial_is_repaired_once(self):
        agent,api,read=self.run_turn('trovami segnale per btc/usdc',[
            'Mi dispiace, non ho accesso a dati di mercato o strumenti di trading.',
            'Ho letto i dati BTC/USDC da Binance spot; analisi di prova senza ordini.'])
        self.assertEqual(api.call_count,2);read.assert_called_once()
        first=api.call_args_list[0].args[1]
        self.assertTrue(any(m.get('role')=='tool' and '68000' in str(m.get('content')) for m in first['messages']))
        self.assertEqual(agent.turn_outcome,'completed')
        self.assertFalse(any(m.get('role')=='assistant' and 'non ho accesso' in str(m.get('content')) for m in agent.messages))

    def test_repeated_denial_is_bounded_and_native_facts_are_reported_as_incomplete(self):
        denial='Mi dispiace, non ho accesso a dati di mercato o strumenti di trading.'
        agent,api,read=self.run_turn('trovami segnale per btc/usdc',[denial,denial])
        self.assertEqual(api.call_count,2);self.assertEqual(read.call_count,1)
        self.assertEqual(agent.turn_outcome,'incomplete')
        self.assertIn('68000',agent.messages[-1]['content']);self.assertIn('https://data-api.binance.vision',agent.messages[-1]['content'])
        self.assertFalse(any(c['function']['name']=='hyperliquid_submit' for m in agent.messages for c in m.get('tool_calls',[])))

    def test_real_read_error_is_reported_without_inventing_data_or_repeating_reads(self):
        agent,api,read=self.run_turn('Analizza BTC/USDC',['Il controllo API ha restituito HTTP 429: non ho una quotazione verificata.'],RuntimeError('Binance HTTP 429'))
        self.assertEqual(read.call_count,1);self.assertEqual(agent.turn_outcome,'incomplete')
        self.assertTrue(any('Binance HTTP 429' in str(m.get('content')) for m in agent.messages))

    def test_schema_exposes_hyperliquid_group_for_plain_leveraged_order_request(self):
        from advanced_tools import enable_for_prompt
        with patch.object(f,'HOME',self.home):
            agent=f.Agent(self.home,'http://127.0.0.1:9','synthetic','auto')
            enable_for_prompt(agent,'BTC/USDC: esegui ordine con leva 40')
            names={t['function']['name'] for t in agent.tool_definitions()}
        self.assertTrue({'hyperliquid_read','hyperliquid_status','hyperliquid_preview','hyperliquid_submit'}<=names)

    def test_only_database_open_is_retried_before_any_transaction(self):
        original=sqlite3.connect;attempts=[]
        def connect(*args,**kwargs):
            attempts.append(args)
            if len(attempts)==1:raise sqlite3.OperationalError('unable to open database file')
            return original(*args,**kwargs)
        with patch.object(h.sqlite3,'connect',side_effect=connect),patch.object(h.time,'sleep'):
            with h.database(self.home) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM grants').fetchone()[0],0)
        self.assertEqual(len(attempts),2)

    def test_permanent_archive_failure_preserves_profiles_for_read_only_data_access(self):
        h.configure(self.home,'mainnet-master','mainnet','0x'+'1'*40)
        with patch.object(h.sqlite3,'connect',side_effect=sqlite3.OperationalError('unable to open database file')) as connect,patch.object(h.time,'sleep'):
            status=h.status(self.home)
        self.assertEqual(connect.call_count,2)
        self.assertEqual(status['default'],'mainnet-master');self.assertFalse(status['automation_verified'])
        self.assertFalse(status['storage']['available'])

    def test_wal_open_failure_is_retried_before_yield_and_failed_connection_closed(self):
        original=sqlite3.connect;failed=Mock();failed.execute.side_effect=sqlite3.OperationalError('unable to open database file')
        attempts=[]
        def connect(*args,**kwargs):
            attempts.append(args)
            return failed if len(attempts)==1 else original(*args,**kwargs)
        with patch.object(h.sqlite3,'connect',side_effect=connect),patch.object(h.time,'sleep'):
            with h.database(self.home) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM actions').fetchone()[0],0)
        self.assertEqual(len(attempts),2);failed.close.assert_called_once()


if __name__=='__main__':unittest.main()
