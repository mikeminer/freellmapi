import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from seeker_adb import Seeker, dispatch, panel
from freellmapi import Agent


class SeekerTests(unittest.TestCase):
    def test_disconnection_stops_turn_without_retry_or_followup_action(self):
        from seeker_adb import DeviceUnavailable
        import json
        with tempfile.TemporaryDirectory() as d:
            agent=Agent(d,'http://localhost','test','auto'); agent.mode='auto'
            agent.context_limit=131072; agent.session=Path(d)/'session.json'
            calls=[{'id':'i','function':{'name':'seeker_install','arguments':json.dumps({'path':'app.apk'})}},
                   {'id':'s','function':{'name':'shell','arguments':json.dumps({'command':'build again'})}}]
            response={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':calls}}]}
            with patch.object(agent,'api',return_value=response) as api,patch.object(agent,'tool',side_effect=DeviceUnavailable('Dispositivo scollegato')) as tool:
                agent.turn('Installa APK')
            self.assertEqual(api.call_count,1); self.assertEqual(tool.call_count,1)
            self.assertIn('Azione non eseguita',agent.messages[-1]['content'])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.s = Seeker(self.tmp.name, adb='adb.exe')

    def test_detection_and_authorization(self):
        with patch.object(self.s, 'run', return_value='List of devices attached\ns1 device model:Seeker\np2 unauthorized\n'):
            self.assertEqual(self.s.target(), 's1')
            self.assertIn('unauthorized', self.s.summary)
            with self.assertRaisesRegex(ValueError, 'autorizza'): self.s.target('p2')

    def test_absent_and_ambiguous(self):
        for output in ('List of devices attached\n', 'a device model:Seeker\nb device model:Seeker'):
            with patch.object(self.s, 'run', return_value=output):
                with self.assertRaises(ValueError): self.s.target()

    def test_saved_device_never_falls_back(self):
        self.s.config['serial'] = 'original'
        with patch.object(self.s, 'run', return_value='other device model:Seeker'):
            with self.assertRaisesRegex(ValueError, 'scollegato'): self.s.target()

    def test_persistence(self):
        with patch.object(self.s, 'target', return_value='s1'):
            self.s.select(serial='s1', package='com.example.game')
        self.assertEqual(Seeker(self.tmp.name).config, self.s.config)

    def test_package_injection_rejected(self):
        for value in ('com.game;reboot', 'com.game && id', '-x', 'a/b'):
            with self.assertRaises(ValueError): self.s.package(value)

    def test_scoped_install_argv_and_success(self):
        apk = Path(self.tmp.name) / 'my game.apk'; apk.write_bytes(b'fake')
        with patch('seeker_adb.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'Success\n', '')) as run:
            self.assertEqual(self.s.install(apk, 's1'), 'Success')
            self.assertEqual(run.call_args.args[0], ['adb.exe', '-s', 's1', 'install', '-r', str(apk.resolve())])
        with patch('seeker_adb.subprocess.run', return_value=subprocess.CompletedProcess([], 0, 'Failure [BAD_APK]', '')):
            with self.assertRaises(RuntimeError): self.s.install(apk, 's1')

    def test_timeout(self):
        with patch('seeker_adb.subprocess.run', side_effect=subprocess.TimeoutExpired('adb', 20)):
            with self.assertRaisesRegex(RuntimeError, 'Timeout'): self.s.devices()

    def test_logs_pid_and_bounds(self):
        with patch.object(self.s, 'target', return_value='s1'), patch.object(self.s, 'run', side_effect=['123', 'log']) as run:
            self.assertEqual(self.s.logs('com.example.game', lines=9000), 'log')
            self.assertEqual(run.call_args.args, (['logcat','-d','-v','threadtime','--pid=123','-t','2000'], 's1'))
        with patch.object(self.s, 'target', return_value='s1'), patch.object(self.s, 'run', return_value=''):
            self.assertIn('non in esecuzione', self.s.logs('com.example.game'))
        with patch.object(self.s, 'target', return_value='s1'), patch.object(self.s, 'run', side_effect=RuntimeError('offline')):
            with self.assertRaisesRegex(RuntimeError, 'offline'): self.s.logs('com.example.game')

    def test_pidof_empty_exit_one(self):
        with patch('seeker_adb.subprocess.run', return_value=subprocess.CompletedProcess([],1,'','')):
            self.assertEqual(self.s.run(['shell','pidof','com.game'], 's1', empty_exit_one=True), '')
            with self.assertRaises(RuntimeError): self.s.run(['devices'])

    def test_live_logs_cleanup(self):
        import io
        from seeker_adb import live_logs
        process = Mock(stdout=io.StringIO('sample log\n'))
        process.poll.return_value = None
        self.s.config['package'] = 'com.example.game'
        with patch.object(self.s,'target',return_value='s1'), patch.object(self.s,'run',return_value='42'), patch('seeker_adb.subprocess.Popen', return_value=process):
            live_logs(self.s)
        process.terminate.assert_called_once()
        process.wait.assert_called_once()
        self.assertTrue(process.stdout.closed)

    def test_crashes_label_global_buffer(self):
        with patch.object(self.s, 'target', return_value='s1'), patch.object(self.s, 'run', side_effect=['OTHER APP CRASH','selected exits']):
            result = self.s.crashes('com.example.game')
            self.assertIn('globale', result); self.assertIn('selected exits', result)

    def test_launch_resolves_component(self):
        with patch.object(self.s, 'run', side_effect=['com.example.game/.MainActivity','Status: ok']) as run:
            self.assertIn('ok', self.s.launch('com.example.game','s1'))
            self.assertEqual(run.call_args.args[0][-1], 'com.example.game/.MainActivity')

    def test_denied_mutation_and_plan(self):
        agent = Agent(self.tmp.name, 'http://localhost', 'test', 'auto')
        agent.seeker = self.s
        with patch.object(self.s,'target',return_value='s1'), patch.object(self.s,'stop') as stop, patch.object(agent,'approve',return_value=False):
            self.assertIn('non autorizzata', agent.tool('seeker_stop', {'package':'com.example.game'}))
            stop.assert_not_called()
        agent.mode = 'plan'
        with patch.object(self.s,'target',return_value='s1'), patch.object(self.s,'stop') as stop:
            agent.tool('seeker_stop', {'package':'com.example.game'}); stop.assert_not_called()

    def test_panel_cancel(self):
        with patch.object(self.s, 'devices', return_value=[]), patch('questions.ask', return_value={'cancelled':True}):
            panel(Mock(seeker=self.s))

    def test_native_debug_skips_skill_search(self):
        from skill_router import Router
        agent = Mock(depth=0, interactive=True)
        Router().run(agent, 'Controlla i crash sul Seeker collegato', print)
        agent.api.assert_not_called()


if __name__ == '__main__': unittest.main()
