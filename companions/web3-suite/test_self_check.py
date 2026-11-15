import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from freellmapi import Agent
import self_check
from file_scan import search,ScanLimit


class SelfCheckTests(unittest.TestCase):
    def test_intent_is_narrow(self):
        for text in ('fai dei test su freellmapi','esegui i test di freellmapi','Testa freellmapi!'):
            self.assertTrue(self_check.requested(text))
        for text in ('crea test su freellmapi','fai dei test su freellmapi e installa qualcosa','testa il mio gioco','come posso testare freellmapi?'):
            self.assertFalse(self_check.requested(text))

    def test_resumed_unrelated_history_does_not_trigger_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent=Agent(tmp,'http://localhost','test','auto'); agent.mode='auto'; agent.session=Path(tmp)/'s.json'
            agent.messages=[{'role':'user','content':'quotazione BTC'},{'role':'assistant','content':'vecchia risposta'}]
            with patch.object(agent,'api') as api,patch.object(agent.skill_router,'run') as skills,patch('self_check.run',return_value={'ok':True,'summary':'test pass','log':'test.log'}) as run:
                agent.turn('fai dei test su freellmapi')
            run.assert_called_once(); api.assert_not_called(); skills.assert_not_called()
            self.assertEqual(agent.turn_outcome,'completed'); self.assertEqual(agent.root,Path(tmp).resolve())

    def test_plan_does_not_run_tests(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent=Agent(tmp,'http://localhost','test','auto'); agent.mode='plan'; agent.session=Path(tmp)/'s.json'
            with patch('self_check.run') as run: agent.turn('testa freellmapi')
            run.assert_not_called()

    def test_search_retains_partial_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); path=root/'a.txt'; path.write_text('needle')
            def paths(*args,**kwargs):
                yield path
                raise ScanLimit('Scansione parziale')
            with patch('file_scan.files',paths): result=search(root,root,'needle',set())
            self.assertIn('a.txt:1: needle',result); self.assertIn('Scansione parziale',result)

    def test_search_subdirectory(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); sub=root/'src'; sub.mkdir(); (sub/'yes.txt').write_text('needle'); (root/'no.txt').write_text('needle')
            result=search(root,sub,'needle',set())
            self.assertIn('yes.txt',result); self.assertNotIn('no.txt',result)


if __name__=='__main__': unittest.main()
