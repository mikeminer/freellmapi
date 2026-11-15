import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from seeker_gameplay import frame_stats,prepare,requested
from android_delivery import request
from work_ledger import load
from unittest.mock import patch
from seeker_gameplay import dodge_target

def frames():
    return {'start':0,'end':36200,'interruptions':[], 'frames':[{'id':i+1,'t':i*1000/60,'phase':'gameplay','visible':'visible','foreground':True,'enemies':2} for i in range(2170)]}

class GameplayTests(unittest.TestCase):
    def test_dodge_predicts_path_instead_of_crossing_collision(self):
        state={'ship':{'x':-.73,'y':2.11},'camera':{'halfWidth':1.72,'halfHeight':3.83},'controlBounds':{'x':.81,'y':2.53},'enemies':[{'x':.13,'y':-.3,'z':-1.2,'speed':3.35},{'x':.42,'y':2.42,'z':-3.55,'speed':4.58}]}
        target=dodge_target(state)
        self.assertGreater(target[1],0) # crossing through y=0 would hit the first threat
    def test_one_physical_tool_result_ends_turn_without_model_observation_loop(self):
        from freellmapi import Agent
        for status in ('PASS','FAIL'):
            with tempfile.TemporaryDirectory() as folder:
                agent=Agent(folder,'http://localhost','test','auto',interactive=False);agent.mode='auto';agent.session=Path(folder)/'session.json'
                result={'status':status,'checks':{'touch_movement':status},'report':'REPORT.md','results':'results.json'}
                args={'project_path':folder,'apk_path':folder+'/test.apk','serial':'test','package':'cool.devfridge.game'}
                response={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'physical','type':'function','function':{'name':'seeker_playtest','arguments':json.dumps(args)}}]}}]}
                with patch.object(agent,'api',return_value=response) as api,patch('seeker_gameplay.run',return_value=result),patch('freellmapi.output'):
                    agent.turn('Collaudo gameplay Seeker touch prestazioni: esegui una prova fisica e riporta il risultato.')
                self.assertEqual(api.call_count,1)
                self.assertEqual(agent.turn_outcome,'completed' if status=='PASS' else 'incomplete')
    def test_live_gate_healthy_commits(self):
        self.assertEqual(frame_stats(frames(),5000,30000)['status'],'PASS')
    def test_no_pruning_of_gaps_results_empty_or_hidden(self):
        for field,value in [('phase','results'),('enemies',0),('visible','hidden'),('foreground',False)]:
            record=frames();record['frames'][600][field]=value
            self.assertEqual(frame_stats(record,5000,30000)['status'],'FAIL')
        record=frames();record['frames']=record['frames'][:600]+record['frames'][630:]
        result=frame_stats(record,5000,30000)
        self.assertEqual(result['status'],'FAIL');self.assertIn('skipped frame id',result['issues'])
        record=frames();record['end']=34999
        self.assertEqual(frame_stats(record,5000,30000)['status'],'FAIL')
    def test_gameplay_does_not_reactivate_delivery_pid_completion(self):
        with tempfile.TemporaryDirectory() as folder:
            session=Path(folder)/'s.json'
            text='Collaudo gameplay sul Seeker: touch e prestazioni; ricompila e installa APK se necessario.'
            self.assertTrue(requested(text));request(session,text)
            self.assertFalse(load(session).get('android_delivery',{}).get('required'))
    def test_prepare_windows_paths_and_backup(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'project'
            for name,body in {'core/game.js':'export class Game {}','main.js':'// main','input/touch.js':'// input','entities/ship.js':'// ship'}.items():
                p=root/'src'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(body)
            (root/'public').mkdir()
            agent=SimpleNamespace(root=Path(folder),path=lambda value:root,approve=lambda _:True)
            result=prepare(agent,'.')
            self.assertTrue((Path(result['backup'])/'src/core/game.js').is_file())
            self.assertIn('createQA',(root/'src/core/game.js').read_text())

if __name__=='__main__':unittest.main()
