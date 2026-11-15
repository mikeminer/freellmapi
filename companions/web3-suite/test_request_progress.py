import io
import json
import unittest
from unittest.mock import patch
from api_stream import consume
from request_progress import Progress, live_progress
from freellmapi import Agent


class ProgressTests(unittest.TestCase):
    def test_mobile_telemetry_reports_only_measured_values(self):
        now=[5.0];p=Progress('auto',lambda:now[0]);now[0]=8.0
        self.assertEqual(p.snapshot(),{'actual_model':None,'elapsed_seconds':3.0,'silence_seconds':3.0,'events':0,'usage':{}})
        p.model('real-model');p.activity({'total_tokens':17});snap=p.snapshot()
        self.assertEqual(snap['actual_model'],'real-model');self.assertEqual(snap['usage'],{'total_tokens':17})
        snap['usage']['total_tokens']=999
        self.assertEqual(p.snapshot()['usage']['total_tokens'],17)
    def test_elapsed_and_silence_without_invented_tokens(self):
        clock=[100.0]
        p=Progress('auto',lambda:clock[0])
        clock[0]=125.2
        text=p.render()
        self.assertIn('25.2s',text); self.assertIn('in attesa del server',text)
        self.assertIn('blocco non accertato',text)
        p.activity(); self.assertIn('ultimo dato 0.0s fa',p.render())
        self.assertNotIn('blocco non accertato',p.render())

    def test_usage_is_cumulative_snapshot_not_added(self):
        p=Progress('auto'); p.model('Qwen'); p.activity({'prompt_tokens':100,'completion_tokens':2})
        p.activity({'prompt_tokens':100,'completion_tokens':5,'total_tokens':105})
        self.assertIn('100/5',p.render()); self.assertIn('totale: 105',p.render())
        self.assertIn('modello effettivo: Qwen',p.render())
        self.assertNotIn('Qwen',Progress('auto').render())

    def test_stream_reports_usage_and_heartbeat(self):
        chunks=[{'model':'Qwen','choices':[{'delta':{'content':'ciao'}}]},
                {'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':3,'total_tokens':13}}]
        raw=': heartbeat\n\n'+''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+'data: [DONE]\n\n'
        p=Progress('auto'); text=[]
        result=consume(io.BytesIO(raw.encode()),text.append,p.model,p.activity)
        self.assertGreater(p.events,2); self.assertEqual(p.usage['total_tokens'],13)
        self.assertEqual(result['usage'],p.usage); self.assertEqual(text,['ciao'])

    def test_progress_cleared_after_failure(self):
        a=Agent('.','http://localhost','test','auto')
        with patch.object(a,'_api',side_effect=RuntimeError('failed')),patch('request_progress.ui.fancy',return_value=False),patch('request_progress.ui.write'):
            with self.assertRaises(RuntimeError): a.api('chat/completions',{'model':'auto'})
        self.assertIsNone(a._request_progress); self.assertIsNone(a._model_progress)

    def test_renderable_rich_live(self):
        with patch('request_progress.ui.fancy',return_value=True),patch('rich.live.Live') as live:
            with live_progress('auto') as p:
                p.activity({'total_tokens':12})
                spinner=live.call_args.kwargs['get_renderable']()
                self.assertIn('totale: 12',str(spinner.text))


if __name__=='__main__': unittest.main()
