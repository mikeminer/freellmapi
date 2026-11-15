import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
import remote_access as r

class RemoteCommandTests(unittest.TestCase):
    def test_launcher_pair_inside_chat_is_native_and_not_a_model_request(self):
        agent=SimpleNamespace(api=Mock(),api_state='OK')
        with patch('remote_pairing.request_pair',return_value={'url':'http://127.0.0.1:31418'}) as pair,patch.object(r,'print_pair') as show:
            self.assertTrue(r.command(agent,'freellmapi --remote-pair --pair-seconds 600',Path('.'),[]))
        pair.assert_called_once_with(Path('.'),600);show.assert_called_once();agent.api.assert_not_called()
        self.assertEqual(agent.api_state,'OK')

    def test_unknown_launcher_options_are_not_executed_or_sent_to_model(self):
        with patch('remote_pairing.request_pair') as pair,patch('terminal_ui.write'):
            self.assertTrue(r.command(SimpleNamespace(),'freellmapi --remote-pair ; anything',Path('.'),[]))
        pair.assert_not_called()

    def test_occupied_port_can_reuse_verified_existing_bridge_without_another_listener(self):
        with patch.object(r,'active',return_value=None),patch.object(r,'vpn_address',return_value=None), \
             patch.object(r.Remote,'start',side_effect=ValueError('Porta Remote già occupata: test')) as start, \
             patch('remote_pairing.request_pair',return_value={'url':'http://127.0.0.1:31418'}),patch.object(r,'print_pair'),patch('terminal_ui.write'):
            self.assertTrue(r.command(SimpleNamespace(),' /not-a-command',Path('.'),[]) is False)
            self.assertTrue(r.command(SimpleNamespace(),'/remote start',Path('.'),[]))
        self.assertEqual(start.call_count,1)

    def test_remote_pair_works_from_another_cli_process(self):
        with patch.object(r,'active',return_value=None),patch('remote_pairing.request_pair',return_value={}) as pair,patch.object(r,'print_pair'):
            self.assertTrue(r.command(SimpleNamespace(),'/remote pair',Path('.'),[]))
        pair.assert_called_once_with(Path('.'))

    def test_pair_failure_is_local_without_overwriting_api_status(self):
        agent=SimpleNamespace(api_state='OK')
        with patch('remote_pairing.request_pair',side_effect=ValueError('no verified bridge')),patch('terminal_ui.write') as output:
            self.assertTrue(r.command(agent,'freellmapi --remote-pair',Path('.'),[]))
        self.assertEqual(agent.api_state,'OK');self.assertIn('Nessuna richiesta API',output.call_args[0][0])

    def test_alias_with_real_bridge_preserves_context_and_uses_no_model(self):
        import tempfile
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp);messages=[{'role':'user','content':'PRESERVED-CONTEXT'}]
            remote=r.Remote(home,lambda:{'messages':messages}).start(port=0,private_vpn=True)
            try:
                agent=SimpleNamespace(api=Mock(),api_state='OK')
                with patch.object(r,'print_pair') as show:
                    self.assertTrue(r.command(agent,'freellmapi --remote-pair',home,[]))
                pair=show.call_args[0][0]
                self.assertEqual(pair['url'],remote.url);self.assertTrue(remote.pair_device({'code':pair['code']}))
                agent.api.assert_not_called();self.assertEqual(messages,[{'role':'user','content':'PRESERVED-CONTEXT'}])
            finally:remote.close()
