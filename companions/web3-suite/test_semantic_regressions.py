"""Regressions for bounded embeddings, index identity, and memory actually sent to the model."""
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from memory_vault import Vault
from brain_memory import bind_memory_scope, consolidate, resolve_scope
from freellmapi import Agent, attach_vault
from semantic_memory import EmbeddingBusy, EmbeddingClient, EmbeddingPending, EmbeddingUnavailable, ProviderStatus, SemanticIndex


def good_body(vectors, model='gemini-embedding-001'):
    return {'model':model,'provider':'google','usage':{'prompt_tokens':3,'total_tokens':3},
            'data':[{'object':'embedding','index':i,'embedding':vector} for i,vector in enumerate(vectors)]}


class SilentHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length=int(self.headers.get('Content-Length') or 0)
        self.rfile.read(length)
        self.server.calls+=1
        code,headers,body=self.server.script.pop(0)
        self.send_response(code)
        for key,value in headers.items(): self.send_header(key,value)
        self.send_header('Content-Length',str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self, fmt, *args): return


def serve(script):
    server=ThreadingHTTPServer(('127.0.0.1',0), SilentHandler)
    server.script=list(script); server.calls=0
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    return server


def client(server, **kwargs):
    folder=Path(tempfile.mkdtemp())
    port=server.server_address[1]
    return EmbeddingClient(f'http://127.0.0.1:{port}','test-token',folder/'semantic-status.json',retry_backoff=0,**kwargs), folder


class ClientTests(unittest.TestCase):
    def test_timeout_and_reset_do_not_retry_and_enter_cooldown(self):
        for error in (TimeoutError('timed out'), ConnectionResetError(10054,'reset')):
            calls=[]
            def transport(texts, timeout, error=error):
                calls.append(texts); raise error
            folder=Path(tempfile.mkdtemp())
            embedder=EmbeddingClient('http://127.0.0.1:9','t',folder/'semantic-status.json',transport=transport,retry_backoff=0)
            with self.assertRaises(type(error)): embedder.embed(['synthetic'],wait_s=1)
            with self.assertRaises(EmbeddingUnavailable): embedder.embed(['synthetic'],wait_s=1)
            self.assertEqual(len(calls),1)
            self.assertGreater(embedder.cooldown_until,time.monotonic())
            saved=json.loads((folder/'semantic-status.json').read_text(encoding='utf-8'))
            self.assertEqual(saved['last_error'],type(error).__name__)
            self.assertNotIn('synthetic',json.dumps(saved))

    def test_rate_limit_honors_retry_after_without_inner_retry(self):
        server=serve([(429,{'Retry-After':'30'},b'{"error":"rate"}')])
        self.addCleanup(server.shutdown)
        embedder,folder=client(server)
        with self.assertRaises(ProviderStatus) as caught: embedder.embed(['synthetic'],wait_s=2)
        self.assertEqual(caught.exception.status,429)
        self.assertEqual(server.calls,1)
        self.assertGreater(embedder.observation['retry_after_seconds'],1)
        self.assertEqual(json.loads((folder/'semantic-status.json').read_text(encoding='utf-8'))['http_status'],429)

    def test_bad_gateway_retries_once(self):
        body=json.dumps(good_body([[1,0]])).encode()
        server=serve([(502,{},b'{}'),(200,{},body)])
        self.addCleanup(server.shutdown)
        embedder,_folder=client(server)
        model,vectors=embedder.embed(['synthetic'],wait_s=2)
        self.assertEqual((model,vectors,server.calls),('gemini-embedding-001',[[1,0]],2))

    def test_concurrent_equivalent_requests_share_one_call(self):
        started=threading.Event(); release=threading.Event()
        def transport(texts, timeout):
            started.set(); release.wait(2)
            return {'status':200,'headers':{},'body':good_body([[1,0]]),'elapsed':0.01}
        folder=Path(tempfile.mkdtemp())
        embedder=EmbeddingClient('http://127.0.0.1:9','t',folder/'status.json',transport=transport)
        results=[]
        def run():
            results.append(embedder.embed(['same']))
        first=threading.Thread(target=run); second=threading.Thread(target=run)
        first.start(); self.assertTrue(started.wait(1)); second.start(); time.sleep(0.05); release.set()
        first.join(2); second.join(2)
        self.assertEqual(embedder.calls,1); self.assertEqual(len(results),2)

    def test_different_inflight_payload_is_busy_without_cooldown(self):
        started=threading.Event(); release=threading.Event()
        def transport(texts, timeout):
            started.set(); release.wait(2)
            return {'status':200,'headers':{},'body':good_body([[1,0]]),'elapsed':0.01}
        folder=Path(tempfile.mkdtemp())
        embedder=EmbeddingClient('http://127.0.0.1:9','t',folder/'status.json',transport=transport)
        worker=threading.Thread(target=lambda: embedder.embed(['one'])); worker.start()
        self.assertTrue(started.wait(1))
        with self.assertRaises(EmbeddingBusy): embedder.embed(['two'],wait_s=0.05)
        self.assertEqual(embedder.calls,1); self.assertEqual(embedder.cooldown_until,0)
        release.set(); worker.join(2)

    def test_malformed_response_is_not_retried(self):
        server=serve([(200,{},b'{"model":"gemini-embedding-001","data":[{"index":4,"embedding":[1,0]}]}')])
        self.addCleanup(server.shutdown)
        embedder,_folder=client(server)
        with self.assertRaises(ValueError): embedder.embed(['synthetic'],wait_s=2)
        self.assertEqual(server.calls,1)
        with self.assertRaises(EmbeddingUnavailable): embedder.embed(['synthetic'],wait_s=1)
        self.assertEqual(server.calls,1)

    def test_chat_wait_does_not_abandon_or_cooldown(self):
        calls=[]
        def transport(texts, timeout):
            calls.append(list(texts)); time.sleep(0.25)
            return {'status':200,'headers':{},'body':good_body([[0,1]]),'elapsed':0.25}
        folder=Path(tempfile.mkdtemp())
        embedder=EmbeddingClient('http://127.0.0.1:9','t',folder/'status.json',transport=transport)
        with self.assertRaises(EmbeddingPending): embedder.embed(['synthetic'],wait_s=0.05)
        self.assertEqual(embedder.cooldown_until,0)
        model,vectors=embedder.embed(['synthetic'],wait_s=2)
        self.assertEqual(model,'gemini-embedding-001'); self.assertEqual(vectors,[[0,1]]); self.assertEqual(len(calls),1)


def vectors_for(texts):
    rows=[]
    for text in texts:
        folded=text.casefold()
        if folded.strip()=='sideways': rows.append([1,0,0])
        elif 'weakoverlap' in folded: rows.append([0.2,0.979795897,0])
        elif any(word in folded for word in ('vehicle','garage','automobile')): rows.append([1,0,0])
        elif any(word in folded for word in ('physician','doctor','patient')): rows.append([0,1,0])
        elif any(word in folded for word in ('loaf','bread','bakery','orchid','flower','nursery')): rows.append([0,0,1])
        else: rows.append([0,0,0])
    return 'fixed',rows


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)

    def test_dimension_change_never_scores_old_vectors(self):
        index=SemanticIndex(self.root/'index.db',lambda texts:('same',[[1,0] for _ in texts]))
        index.search('q',[('note','body')])
        index.embed=lambda texts:('same',[[0,1,0] for _ in texts])
        rows=index.search('q',[('note','body')])
        self.assertEqual(rows[0]['path'],'note'); self.assertEqual(index.status['dimensions'],3)
        db=sqlite3.connect(self.root/'index.db')
        try: self.assertEqual(db.execute('SELECT COUNT(*) FROM vectors WHERE dimensions=2').fetchone()[0],0)
        finally: db.close()

    def test_modified_note_drops_the_old_chunk(self):
        calls=[]
        def embed(texts):
            calls.append(list(texts)); return vectors_for(texts)
        index=SemanticIndex(self.root/'index.db',embed)
        old=('Projects/memory-a.md','OLDTOKEN vehicle parked in the garage')
        index.search('automobile',[old])
        new=('Projects/memory-a.md','NEWTOKEN telescope firmware')
        rows=index.search('automobile',[new])
        self.assertTrue(rows==[] or all('OLDTOKEN' not in row['text'] for row in rows))
        old_id=hashlib.sha256((old[0]+'\0'+old[1]).encode()).hexdigest()
        db=sqlite3.connect(self.root/'index.db')
        try: self.assertEqual(db.execute('SELECT COUNT(*) FROM vectors WHERE id=?',(old_id,)).fetchone()[0],0)
        finally: db.close()
        self.assertTrue(any('NEWTOKEN' in text for batch in calls for text in batch))

    def test_excluded_note_is_not_retrievable(self):
        vault=Vault(self.root/'vault',self.root/'alpha')
        calls=[]
        def embed(texts):
            calls.append(list(texts)); return 'fixed',[[1,0] if 'orchid' in t.casefold() or 'flower' in t.casefold() else [0,1] for t in texts]
        vault.semantic=SemanticIndex(self.root/'index.db',embed)
        name=vault.remember('The greenhouse shelters a rare orchid.','project')
        found=vault.semantic_search('flower',purpose='auto',turn_token='one')
        self.assertTrue(any('orchid' in row['text'].casefold() for row in found))
        vault.operational.exclude(name)
        again=vault.semantic_search('flower',purpose='auto',turn_token='two')
        self.assertFalse(any('orchid' in row['text'].casefold() for row in again))
        db=sqlite3.connect(self.root/'index.db')
        try: self.assertEqual(db.execute('SELECT COUNT(*) FROM vectors WHERE path=?',(name,)).fetchone()[0],0)
        finally: db.close()

    def test_projects_do_not_share_results(self):
        shared=self.root/'vault'
        alpha=Vault(shared,self.root/'alpha-yard'); beta=Vault(shared,self.root/'beta-nursery')
        index=SemanticIndex(self.root/'index.db',vectors_for)
        alpha.semantic=beta.semantic=index
        alpha.remember('Spare road vehicle parked in the garage. ALPHA-GARAGE','project')
        beta.remember('A rare orchid blooms in the nursery. BETA-ORCHID','project')
        alpha_rows=alpha.semantic_search('automobile',purpose='auto',turn_token='a')
        beta_rows=beta.semantic_search('flower',purpose='auto',turn_token='b')
        self.assertTrue(any('ALPHA-GARAGE' in row['text'] for row in alpha_rows))
        self.assertFalse(any('BETA-ORCHID' in row['text'] for row in alpha_rows))
        self.assertTrue(any('BETA-ORCHID' in row['text'] for row in beta_rows))
        flower=alpha.semantic_search('flower',purpose='auto',turn_token='c')
        self.assertFalse(any('BETA-ORCHID' in row['text'] for row in flower))

    def test_restart_reuses_vectors_and_cooldown_persists(self):
        script=r'''
import json,sys
from pathlib import Path
from semantic_memory import SemanticIndex
calls=[]
def embed(texts):
    calls.append(list(texts))
    rows=[]
    for text in texts:
        rows.append([1,0] if ("vehicle" in text or text=="automobile") else [0,1])
    return "fixed",rows
index=SemanticIndex(Path(sys.argv[1]),embed)
rows=index.search("automobile",[("car","vehicle")])
print(json.dumps({"calls":calls,"path":rows[0]["path"] if rows else None}))
'''
        database=self.root/'restart.sqlite'
        repo=Path(__file__).resolve().parent
        def run():
            return subprocess.run([sys.executable,'-c',script,str(database)],cwd=repo,capture_output=True,text=True,timeout=30,check=True)
        first=json.loads(run().stdout)
        second=json.loads(run().stdout)
        self.assertEqual(first['path'],'car'); self.assertIn('vehicle',first['calls'][0])
        self.assertEqual(second['calls'],[['automobile']]); self.assertEqual(second['path'],'car')
        (self.root/'semantic-status.json').write_text(json.dumps({'cooldown_until_epoch':time.time()+120,'last_error':'TimeoutError'}),encoding='utf-8')
        calls=[]
        index=SemanticIndex(self.root/'cool.sqlite',lambda texts: calls.append(texts) or ('m',[[1,0]]))
        self.assertEqual(index.search('q',[('note','body')]),[])
        self.assertEqual(calls,[]); self.assertEqual(index.status['mode'],'lexical_fallback')

    def test_old_schema_is_backed_up_and_not_scored(self):
        path=self.root/'legacy.sqlite'
        db=sqlite3.connect(path)
        try:
            db.execute('CREATE TABLE vectors(id TEXT PRIMARY KEY, model TEXT, vector TEXT)')
            db.execute('INSERT INTO vectors VALUES(?,?,?)',('legacy','old-model','[1,0]'))
            db.commit()
        finally: db.close()
        index=SemanticIndex(path,lambda texts:('new-model',[[0,1] for _ in texts]))
        rows=index.search('q',[('note','body')])
        self.assertEqual(rows[0]['path'],'note')
        self.assertTrue(list(self.root.glob('legacy.sqlite.bak-*')))
        db=sqlite3.connect(path)
        try: backends={row[0] for row in db.execute('SELECT backend FROM vectors')}
        finally: db.close()
        self.assertIn('freellmapi-http',backends)
        self.assertNotEqual(index.status['model'],'old-model')

    def test_partial_index_follows_up_once(self):
        calls=[]
        def embed(texts):
            calls.append(list(texts)); return 'm',[[1,0] for _ in texts]
        index=SemanticIndex(self.root/'wide.sqlite',embed)
        docs=[(f'n{i}',f'fragment {i}') for i in range(9)]
        index.search('q',docs,wait_s=1)
        self.assertEqual(len(calls[0]),9); self.assertEqual(index.status['index_state'],'partial')
        for _ in range(50):
            if not index._followup and len(calls)>=2: break
            time.sleep(0.05)
        self.assertEqual(len(calls),2); self.assertEqual(len(calls[1]),1)
        index.search('q',docs,wait_s=1)
        self.assertEqual(calls[-1],['q']); self.assertEqual(index.status['index_state'],'ready')

    def test_same_turn_rebuild_does_not_embed_twice_and_weak_score_is_not_inserted(self):
        vault=Vault(self.root/'vault',self.root/'alpha')
        calls=[]
        def embed(texts):
            calls.append(list(texts)); return vectors_for(texts)
        vault.semantic=SemanticIndex(self.root/'index.db',embed)
        vault.remember('The spare road vehicle stays in the garage. ALPHA-GARAGE','project')
        vault.remember('Unrelated weakoverlap calibration log.','project')
        vault.remember('The physician treated the patient. ALPHA-CLINIC','project')
        vault.remember('The bakery holds the uncooked loaf. ALPHA-OVEN','project')
        first=vault.operational.snapshot('Where is the automobile parked?',turn_token='turn')
        second=vault.operational.snapshot('Where is the automobile parked?',turn_token='turn')
        self.assertEqual(len(calls),1)
        self.assertEqual(first['text'],second['text'])
        semantic=[item for item in first['inserted'] if item['reason'].startswith('similarità semantica')]
        self.assertTrue(semantic)
        self.assertIn('ALPHA-GARAGE',first['text'])
        self.assertNotIn('ALPHA-CLINIC',first['text']); self.assertNotIn('ALPHA-OVEN',first['text'])
        self.assertNotIn('weakoverlap',first['text'])
        self.assertTrue(any(item['mode']=='semantic' and item['path'].startswith('Projects/memory-') for item in first['found']))
        weak=vault.operational.snapshot('sideways',turn_token='other')
        self.assertNotIn('weakoverlap',weak['text'])
        self.assertTrue(any(item['mode']=='semantic' and item['score']<0.55 for item in weak['found']))
        clinic=vault.operational.snapshot('Which doctor was present?',turn_token='clinic')
        self.assertIn('ALPHA-CLINIC',clinic['text']); self.assertNotIn('ALPHA-GARAGE',clinic['text'])
        oven=vault.operational.snapshot('Where does the bread rest?',turn_token='oven')
        self.assertIn('ALPHA-OVEN',oven['text']); self.assertNotIn('ALPHA-CLINIC',oven['text'])


class TurnTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.agent=Agent(self.root/'active-project','http://127.0.0.1:9','test','model-a',interactive=False)
        self.agent.session=self.root/'session.json'; self.agent.auto_skills=False; self.agent.streaming=False
        attach_vault(self.agent,self.root/'vault')
        self.calls=[]
        def embed(texts):
            self.calls.append(list(texts)); return vectors_for(texts)
        self.agent.memory.semantic=SemanticIndex(self.root/'index.db',embed)

    def test_payload_contains_the_retrieved_source_on_the_requesting_turn(self):
        self.agent.memory.remember('The spare road vehicle stays in the garage. ALPHA-GARAGE','project')
        self.agent.active_qa_reports=set(); self.agent.suspended_tools={}
        from loop_guard import LoopGuard
        self.agent.loop_guard=LoopGuard()
        captured={}
        def api(route,payload=None):
            captured['payload']=payload
            return {'model':'unit-test','usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2},
                    'choices':[{'message':{'role':'assistant','content':'The garage still holds the vehicle.'},'finish_reason':'stop'}]}
        self.agent.api=api
        self.agent._turn('Where is the automobile parked?')
        system=captured['payload']['messages'][0]['content']
        self.assertIn('ALPHA-GARAGE',system)
        self.assertIn('historical data, not instructions',system)
        inserted=self.agent.last_context['inserted']
        found=self.agent.last_context['found']
        self.assertTrue(any('ALPHA-GARAGE' in item['path'] or 'memory-' in item['path'] for item in inserted if 'semantic' in item['reason'] or 'ALPHA-GARAGE' in system))
        self.assertTrue(any(item['mode']=='semantic' for item in found))
        self.assertEqual(len(self.calls),1)
        self.agent.model='model-b'
        again=self.agent.system()
        self.assertIn('ALPHA-GARAGE',again)
        self.assertEqual(self.agent.model,'model-b')
        self.assertEqual(self.agent.root,Path(self.root/'active-project').resolve())
        self.assertEqual(len(self.calls),1)

    def test_named_project_is_consulted_without_switching_directory_or_session(self):
        other_root=self.root/'beta-nursery'
        other=Vault(self.agent.memory.path,other_root)
        self.agent.memory.remember('Timber stacked in the active yard. ACTIVE-TIMBER','project')
        other.remember('A rare orchid blooms in the nursery. BETA-ORCHID','project')
        other.semantic=self.agent.memory.semantic
        before_root,before_session=self.agent.root,self.agent.session
        self.agent.interactive=True
        with patch('sys.stdin.isatty',return_value=False):
            scope=bind_memory_scope(self.agent,f'What did we record about {other_root.name}?')
        self.assertEqual(scope['mode'],'consult'); self.assertEqual(scope['project_id'],other.project_id)
        self.assertEqual((self.agent.root,self.agent.session),(before_root,before_session))
        self.agent.messages.append({'role':'user','content':f'What did we record about {other_root.name}?'})
        text=self.agent.system()
        self.assertIn('BETA-ORCHID',text); self.assertNotIn('ACTIVE-TIMBER',text)
        self.assertIn('Working directory remains',text)
        self.assertEqual(self.agent.root,before_root)
        with patch('sys.stdin.isatty',return_value=False):
            ambiguous=bind_memory_scope(self.agent,f'Compare {self.agent.root.name} with {other_root.name}')
        self.assertTrue(ambiguous['ambiguous']); self.assertEqual(ambiguous['mode'],'active')
        self.assertEqual(self.agent.root,before_root)
        asked=[]
        def fake_ask(question,options):
            asked.append(question); return {'question':question,'answer':options[0],'cancelled':False}
        with patch('sys.stdin.isatty',return_value=True),patch('questions.ask',fake_ask):
            chosen=bind_memory_scope(self.agent,f'Compare {self.agent.root.name} with {other_root.name}')
        self.assertEqual(len(asked),1)
        self.assertIn('Directory e sessione restano invariate',asked[0])
        self.assertIn(chosen['project_id'],(self.agent.memory.project_id,other.project_id))
        self.assertEqual((self.agent.root,self.agent.session),(before_root,before_session))

    def test_consolidation_keeps_history_and_does_not_promote_requests(self):
        old=self.agent.memory.operational.checkpoint('state','Old plan',user=True)
        self.agent.memory.operational.checkpoint('state','Current plan',replaces=old,user=True)
        self.agent.memory.operational.checkpoint('issue','Port still blocked',user=True)
        self.agent.memory.operational.ingest([{'role':'user','content':'Build the bridge'}],self.agent.session)
        text=Path(consolidate(self.agent.memory)['path']).read_text(encoding='utf-8')
        self.assertEqual(text.count('## Problemi aperti'),1)
        self.assertIn('Port still blocked',text); self.assertIn('Nessun prossimo passo registrato.',text)
        self.assertIn('Old plan',text); self.assertIn('Current plan',text)
        self.assertIn('Storico sostituzioni',text)
        self.assertIn('Build the bridge',text.split('## Ultime richieste')[1])
        self.assertNotIn('Build the bridge',text.split('## Verifiche dichiarate con fonti')[1].split('##')[0])
        self.assertIn('user',text); self.assertNotIn('model interpretation',text.split('Current plan')[0])

    def test_resolve_scope_does_not_mix_when_no_choice_is_available(self):
        other=Vault(self.agent.memory.path,self.root/'beta-nursery')
        scope=resolve_scope(self.agent.memory,f'{self.agent.root.name} and {other.project.name}',choose=None)
        self.assertTrue(scope['ambiguous']); self.assertEqual(scope['project_id'],self.agent.memory.project_id)


if __name__=='__main__':
    unittest.main()
