"""Reproducible local acceptance audit. No model-generated PASS claims."""
import argparse
from collections import Counter
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import unittest
from contextlib import redirect_stdout,redirect_stderr
from reliable_runtime import atomic_bytes


def write(path,data): atomic_bytes(path,json.dumps(data,ensure_ascii=False,indent=2).encode('utf-8'))


class EvidenceResult(unittest.TextTestResult):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs); self.records={}; self.timings={}
    def startTest(self,test):
        self.timings[test.id()]=time.monotonic()
        self.records[test.id()]={'id':test.id(),'status':'NON_VERIFICATO','reason':'Test iniziato, non completato.'}
        super().startTest(test)
    def record(self,test,status,reason=''):
        self.records[test.id()]={'id':test.id(),'status':status,'reason':reason,'seconds':round(time.monotonic()-self.timings.get(test.id(),time.monotonic()),3)}
        if getattr(self,'progress_path',None):
            write(self.progress_path,{'tests':list(self.records.values()),'complete':False,'scope':'Partial local suite; model APIs simulated.'})
    def addSuccess(self,test): self.record(test,'PASS'); super().addSuccess(test)
    def addFailure(self,test,err): self.record(test,'FAIL',self._exc_info_to_string(err,test)); super().addFailure(test,err)
    def addError(self,test,err): self.record(test,'FAIL',self._exc_info_to_string(err,test)); super().addError(test,err)
    def addSkip(self,test,reason): self.record(test,'NON_VERIFICATO',reason); super().addSkip(test,reason)
    def addExpectedFailure(self,test,err): self.record(test,'NON_VERIFICATO','Fallimento atteso'); super().addExpectedFailure(test,err)
    def addUnexpectedSuccess(self,test): self.record(test,'FAIL','Successo inatteso: test da aggiornare'); super().addUnexpectedSuccess(test)
    def addSubTest(self,test,subtest,err):
        if err: self.record(test,'FAIL',self._exc_info_to_string(err,test))
        super().addSubTest(test,subtest,err)


def execute(folder):
    folder=Path(folder).resolve(); folder.mkdir(parents=True,exist_ok=False)
    root=Path(__file__).resolve().parent; suite=unittest.defaultTestLoader.discover(str(root),pattern='test_*.py')
    expected=suite.countTestCases(); started=time.monotonic(); result=None
    with (folder/'suite.log').open('w',encoding='utf-8') as log:
        captured=[]
        def factory(*args,**kwargs):
            record=EvidenceResult(*args,**kwargs); record.progress_path=folder/'progress.json'; captured.append(record); return record
        runner=unittest.TextTestRunner(stream=log,verbosity=2,resultclass=factory)
        try:
            with redirect_stdout(log),redirect_stderr(log): result=runner.run(suite)
        except KeyboardInterrupt:
            result=captured[0]; log.write('\nINTERRUPTED: evidenza parziale, suite non completata.\n')
    rows=list(result.records.values())
    evidence={'tests':rows,'discovered':expected,'executed':result.testsRun,'duration_seconds':round(time.monotonic()-started,3),'complete':len(rows)==expected and not any(r.get('reason')=='Test iniziato, non completato.' for r in rows)}
    write(folder/'evidence.json',evidence)
    scenarios=[]
    groups={'files':('test_file_scan.','test_search.','test_reliability.','test_edit_recovery.'),
            'csv_application':('test_csv_acceptance.',),
            'processes':('test_shell_runner.','test_reliability.'),
            'questionnaire_simulated':('test_questions.','test_question_policy.','test_question_dedup.'),
            'memory_and_sessions':('test_memory.','test_sessions.','test_project_context.','test_checkpoint_recovery.'),
            'parallel_agents_simulated':('test_jobs.','test_job_coordination.'),
            'browser_local':('test_browser_integration.',),
            'mcp_local_and_simulated':('test_extensions.','test_reliability.'),
            'api_recovery_simulated':('test_runtime.','test_failover_recovery.','test_continuous_mode.','test_tool_autonomy.'),
            'report_validation':('test_qa_evidence.',),'full_suite':('test_',)}
    sha=hashlib.sha256((folder/'evidence.json').read_bytes()).hexdigest()
    for name,prefixes in groups.items():
        selected=[r for r in rows if r['id'].startswith(prefixes)]
        state='FAIL' if any(r['status']=='FAIL' for r in selected) else 'NON_VERIFICATO' if not selected or any(r['status']=='NON_VERIFICATO' for r in selected) or not evidence['complete'] else 'PASS'
        scenarios.append({'id':name,'status':state,'mode':'locale; API modello simulate','test_ids':[r['id'] for r in selected],
                          'reason':'Esecuzione incompleta, test saltati o prerequisiti mancanti.' if state=='NON_VERIFICATO' else '',
                          'evidence':{'path':'evidence.json','sha256':sha}})
    for name,reason in [('questionnaire_human','Tasti frecce/Invio non azionati da un utente in questo runner; copertura automatica separata.'),
                        ('live_multi_model_agents','Nessuna chiamata a provider reali in questa suite; concorrenza e failover verificati con risposte simulate.')]:
        scenarios.append({'id':name,'status':'NON_VERIFICATO','reason':reason})
    counts={state:sum(s['status']==state for s in scenarios) for state in ('PASS','FAIL','NON_VERIFICATO')}
    report={'scenarios':scenarios,'summary':{'counts':counts,'unique_tests':len(rows),'test_counts':dict(Counter(r['status'] for r in rows)),
        'duration_seconds':evidence['duration_seconds'],'provider_api_requests':None,'provider_tokens':None},'checked_utc':datetime.now(timezone.utc).isoformat(),
        'scope':'Runner deterministico. Non è una prova end-to-end della scelta degli strumenti da parte di tutti i modelli.'}
    write(folder/'RESULTS.json',report)
    from qa_evidence import validate
    validation=validate(folder/'RESULTS.json'); write(folder/'validation.json',validation)
    table='\n'.join('| '+s['id']+' | '+s['status']+' | '+s.get('reason','')+' |' for s in scenarios)
    (folder/'REPORT.md').write_text('# Collaudo verificato\n\n'+str(len(rows))+' test distinti. Durata: '+str(evidence['duration_seconds'])+' secondi.\n\n| Scenario | Esito | Limite |\n|---|---|---|\n'+table+'\n\nEvidenze: evidence.json e suite.log. Conteggi dei gruppi sovrapposti non sommabili. API simulate; metriche provider non misurate (null).\n',encoding='utf-8')
    (folder/'NEXT_STEPS.md').write_text('# Stato di ripresa\n\nSuite locale conclusa.\n'+('\n'.join('- '+s['id']+': '+s.get('reason','consultare evidence.json') for s in scenarios if s['status']!='PASS'))+'\nNon ripetere le fasi PASS senza nuove modifiche.\n',encoding='utf-8')
    print(json.dumps({'report':str(folder/'REPORT.md'),'tests':len(rows),'counts':counts,'validation':validation},ensure_ascii=False),flush=True)
    return result.wasSuccessful() and validation['ok'] and evidence['complete']


if __name__=='__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('--output',required=True); args=parser.parse_args()
    raise SystemExit(0 if execute(args.output) else 1)
