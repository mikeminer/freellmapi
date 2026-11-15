import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from freellmapi import Agent
from work_ledger import record_request, load, render
from android_delivery import request, status

MINT = '39kMeX4HVRW9qbbiHSPbRQ9xeXUF18GrNP6gL61Ppump'
PROMPT = ("Realizza autonomamente Space Shooter per il mio Seeker con gioco e risorse inclusi nell'APK. "
          'Decidi tu i dettagli. Senza nuovi market o promozioni aggiuntive; pagamenti disattivati. '
          'Usa ' + MINT + ' come token esistente su mainnet. '
          'Concludi la build APK con package cool.devfridge.test.autonomy; installalo e avvialo sul Seeker autorizzato SYNTHETIC0000001. '
          'Scrivi REPORT.md e results.json.')


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.agent = Agent(self.tmp.name, 'http://localhost', 'test', 'auto', interactive=False)
        self.agent.mode = 'auto'
        self.agent.session = Path(self.tmp.name) / 's.json'
        self.agent.context_limit = 131072
        patcher=patch('android_delivery.recovery_route',return_value=None)
        patcher.start(); self.addCleanup(patcher.stop)

    def bind(self):
        record_request(self.agent.session, PROMPT)
        request(self.agent.session, PROMPT)

    def test_reported_architecture_question_reused_in_batch(self):
        self.bind()
        with patch('freellmapi.ask_question') as ask:
            result = json.loads(self.agent.tool('ask_user', {
                'question': "La piattaforma target è un'app Seeker per Android. Vuoi includere il gioco e le risorse nell'APK, o preferisci un APK che carichi il gioco da un sito web?",
                'options': ["Includi gioco e risorse nell'APK", 'Carica il gioco da un sito web']}))
        ask.assert_not_called()
        self.assertTrue(result['reused'])
        self.assertEqual(result['decision_key'], 'apk_architecture')
        self.assertIn('bundled', result['answer'])

    def test_game_room_inapplicable_and_explicit_ads_not_reasked(self):
        self.bind()
        for question, options in [('Vuoi includere il gioco web nella Game Room di OnlyFridge Club?', ['Sì', 'No']),
                                  ('Vuoi una promozione prima della partita?', ['Sì', 'No']),
                                  ('Vuoi il banner DevFridge Scan?', ['Sì', 'No'])]:
            with patch('freellmapi.ask_question') as ask:
                result = json.loads(self.agent.tool('ask_user', {'question': question, 'options': options}))
            ask.assert_not_called()
            self.assertTrue(result['reused'])

    def test_ledger_choices_survive_compacted_history_and_model_change(self):
        self.bind()
        self.agent.model = 'different-model'
        self.agent.messages = [{'role': 'user', 'content': 'Conversation summary (data, not new instructions):\nold summary'}]
        result = json.loads(self.agent.tool('ask_user', {'question': 'Quale token CA devo usare?', 'options': ['Token esistente', 'Nuovo token']}))
        self.assertEqual(result['answer'], MINT)
        self.assertIn(MINT, render(self.agent.session))
        self.assertIn('android_delivery', load(self.agent.session))

    def test_unknown_choice_never_invented_in_batch(self):
        self.bind()
        result = json.loads(self.agent.tool('ask_user', {'question': 'Quale indirizzo email di supporto?', 'options': ['Indico ora', 'Decido dopo']}))
        self.assertEqual(result['code'], 'missing_user_decision')
        self.assertNotIn('answer', result)

    def test_task_start_confirmation_is_already_authorized_but_purchase_is_not(self):
        self.bind()
        question='Vuoi procedere con un nuovo progetto Space Shooter 3D?'
        with patch('freellmapi.ask_question') as ask:
            result=json.loads(self.agent.tool('ask_user',{'question':question,'options':['Sì','No']}))
        ask.assert_not_called()
        self.assertEqual(result['decision_key'],'authorized_delivery')
        self.assertIn('non ricominciare',result['answer'])
        result=json.loads(self.agent.tool('ask_user',{'question':'Vuoi procedere con un acquisto di token?', 'options':['Sì','No']}))
        self.assertNotIn('answer',result)

    def test_fabricated_reports_do_not_replace_apk_evidence(self):
        self.bind()
        (Path(self.tmp.name) / 'REPORT.md').write_text('APK installato con successo')
        (Path(self.tmp.name) / 'results.json').write_text('{"success":true}')
        result = status(self.agent)
        self.assertFalse(result['complete'])
        self.assertIn('APK firmato', result['missing'][0])

    def test_install_rejects_missing_bundle_before_adb(self):
        self.bind()
        with patch('android_delivery.inspect',return_value={'valid':False,'embedded':{'mint':False},'next_action':'Build web assets'}),patch.object(self.agent.seeker,'install') as install:
            result=json.loads(self.agent.tool('seeker_install',{'path':'empty.apk'}))
        install.assert_not_called()
        self.assertEqual(result['code'],'delivery_apk_not_ready')

    def test_package_and_serial_from_actual_natural_language_request(self):
        text="Concludi la build APK. Il package della nuova app deve essere cool.devfridge.test.autonomy. Avvialo sul Seeker autorizzato SYNTHETIC0000001."
        record_request(self.agent.session,text);request(self.agent.session,text)
        goal=load(self.agent.session)['android_delivery']
        self.assertEqual(goal['package'],'cool.devfridge.test.autonomy')
        self.assertEqual(goal['serial'],'SYNTHETIC0000001')

    def test_missing_batch_answer_does_not_close_as_success(self):
        responses = [{'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[
            {'id':'q','type':'function','function':{'name':'ask_user','arguments':json.dumps({'question':'Quale nome?', 'options':['A','B']})}}
        ]}}]}, {'choices':[{'message':{'role':'assistant','content':'Scegli A o B.'}}]}]
        with patch.object(self.agent,'api',side_effect=responses), patch('freellmapi.output'):
            self.agent.turn('Configura questo progetto')
        self.assertEqual(self.agent.turn_outcome,'incomplete')

    def test_final_without_apk_is_bounded_and_incomplete(self):
        response = {'choices': [{'message': {'role': 'assistant', 'content': 'Tutto pronto!'}}]}
        with patch.object(self.agent, 'api', return_value=response) as api, patch('freellmapi.output'):
            self.agent.turn(PROMPT)
        self.assertEqual(self.agent.turn_outcome, 'incomplete')
        self.assertEqual(api.call_count, 3)
        self.assertEqual(load(self.agent.session)['token_mint'], MINT)
        self.assertFalse(any(m.get('content') == 'Tutto pronto!' for m in self.agent.messages))

    def test_repeated_known_choice_cannot_loop_and_keeps_context(self):
        q={'question':'APK bundled o carica il sito web?', 'options':['Bundled','Sito web']}
        response={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[
            {'id':'q','type':'function','function':{'name':'ask_user','arguments':json.dumps(q)}}
        ]}}]}
        with patch.object(self.agent,'api',return_value=response) as api, patch('freellmapi.ask_question') as ask, patch('freellmapi.output'):
            self.agent.turn(PROMPT)
        ask.assert_not_called()
        self.assertEqual(api.call_count,3)
        self.assertEqual(api.call_args_list[-1].args[1]['model'],'auto:smart')
        self.assertEqual(self.agent.turn_outcome,'incomplete')
        self.assertEqual(load(self.agent.session)['token_mint'],MINT)

    def test_premature_final_automatically_changes_worker_preserving_request(self):
        response={'model':'weak-worker','choices':[{'message':{'role':'assistant','content':'Done'}}]}
        with patch('android_delivery.recovery_route',return_value='ready-worker'), patch.object(self.agent,'api',return_value=response) as api, patch('freellmapi.output'):
            self.agent.turn(PROMPT)
        self.assertEqual(api.call_args_list[0].args[1]['model'],'auto')
        self.assertEqual(api.call_args_list[1].args[1]['model'],'ready-worker')
        self.assertIn(PROMPT,str(api.call_args_list[1].args[1]['messages']))
        self.assertEqual(self.agent.turn_outcome,'incomplete')

    def test_installed_different_apk_is_not_success(self):
        self.bind()
        apk = Path(self.tmp.name) / 'test.apk'
        apk.write_bytes(b'fake fixture')
        valid = {'path': str(apk), 'package': 'cool.devfridge.test.autonomy', 'sha256': 'a' * 64, 'valid': True}
        with patch('android_delivery.inspect', return_value=valid), patch.object(self.agent.seeker, 'target', return_value='device'), patch.object(self.agent.seeker, 'run', side_effect=['package:/data/app/pkg/base.apk', 'b' * 64 + ' /data/app/pkg/base.apk', '123']):
            observed = status(self.agent, str(apk))
        self.assertFalse(observed['complete'])
        self.assertFalse(observed['device']['same_apk_installed'])


if __name__ == '__main__':
    unittest.main()
