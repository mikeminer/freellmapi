"""Scoped physical WebView QA, real ADB input, and auditable render-commit samples."""
import hashlib,json,os,re,subprocess,time,uuid,urllib.request,math
from pathlib import Path
from datetime import datetime,timezone
from seeker_adb import Seeker
from reliable_runtime import atomic_bytes


def requested(text):
    value=str(text).lower()
    return 'seeker' in value and bool(re.search(r'collaud|gameplay|seeker_playtest',value)) and bool(re.search(r'touch|tocc|prestaz|performance|seeker_playtest',value))


def dodge_target(state):
    """Predict the real smoothed path of input, never modify game state."""
    p=state['ship'];bounds=state.get('controlBounds') or {'x':state['camera']['halfWidth']*.5,'y':state['camera']['halfHeight']*.65}
    choices=[(x*bounds['x'],y*bounds['y']) for x in (-.9,0,.9) for y in (-.9,-.65,-.4,-.2,0,.2,.4,.65,.9)]
    choices.append((p['x'],p['y']))
    threats=[e for e in state['enemies'] if -11<e['z']<3]
    def score(target):
        risk=min(((p['x']+(target[0]-p['x'])*(1-math.exp(-10*t))-e['x'])**2+
                  (p['y']+(target[1]-p['y'])*(1-math.exp(-10*t))-e['y'])**2+
                  (e['z']+e['speed']*t)**2 for e in threats for t in [i*.08 for i in range(1,26)]),default=100)
        travel=(target[0]-p['x'])**2+(target[1]-p['y'])**2
        return min(risk,9)-travel*.012
    return max(choices,key=score)


def prepare(agent,project):
    root=agent.path(project);core=root/'src/core/game.js';main=root/'src/main.js';touch=root/'src/input/touch.js';ship=root/'src/entities/ship.js'
    if not all(p.is_file() for p in (core,main,touch,ship)):raise ValueError('Adapter Space Shooter non applicabile: implementare un osservatore read-only per questo gioco.')
    if not agent.approve('Aggiungere diagnostica read-only del vero stato/render/input alla copia del gioco?'):return {'denied':True}
    before={p.relative_to(root).as_posix():p.read_text(encoding='utf-8') for p in (core,main,touch,ship)}
    folder=Path(agent.root)/'gameplay_qa'/('prepare-'+datetime.now().strftime('%Y%m%d_%H%M%S'));folder.mkdir(parents=True,exist_ok=False)
    for name,content in before.items():
        target=folder/name;target.parent.mkdir(parents=True,exist_ok=True);target.write_text(content,encoding='utf-8')
    text=before['src/core/game.js']
    if "import { createQA }" not in text:
        text="import { createQA } from '../qa.js';\n"+text
        text=text.replace('this.input = new TouchController(this.renderer.domElement);','this.qa = createQA(this);\n    this.input = new TouchController(this.renderer.domElement, (event,state) => this.qa.touch(event,state));')
        text=re.sub(r'    // Performance monitoring[\s\S]*?(?=    // Handle resize)','',text)
        text=text.replace('this.bullets.push(bullet);','this.bullets.push(bullet);\n    this.qa.shot();').replace('this.enemies.push(enemy);','this.enemies.push(enemy);\n    this.qa.spawn();')
        text=text.replace('this.score += 100;','this.score += 100;\n          this.qa.hit();').replace('this.isGameOver = true;','this.isGameOver = true;\n        this.qa.over();')
        text=text.replace('this.renderer.render(this.scene, this.camera);','this.renderer.render(this.scene, this.camera);\n    this.qa.frame();')
        text=re.sub(r'  loop\(\) \{[\s\S]*?\n  \}\n\n  start', '  loop() {\n    this.update();\n    this.render();\n    requestAnimationFrame(() => this.loop());\n  }\n\n  start',text)
        core.write_text(text,encoding='utf-8')
    text=before['src/input/touch.js']
    if 'this.observe' not in text:
        text=text.replace('constructor(domElement) {','constructor(domElement, observe = () => {}) {\n    this.observe = observe;')
        text=text.replace('  handleTouch(e) {','  handleTouch(e) {').replace('      this.isFiring = true;\n    }','      this.isFiring = true;\n    }\n    this.observe(e, this.getState());')
        text=text.replace('      this.isFiring = false;\n    }','      this.isFiring = false;\n    }\n    this.observe(e, this.getState());')
        touch.write_text(text,encoding='utf-8')
    text=before['src/entities/ship.js']
    if 'this.targetPosition' not in text:
        text=text.replace('const targetY = input.y * 3;','const targetY = input.y * 3;\n    this.targetPosition = { x: targetX, y: targetY };')
        ship.write_text(text,encoding='utf-8')
    text=before['src/main.js']
    text=text.replace("document.addEventListener('pointerdown', () => location.reload(), { once: true });","document.addEventListener('pointerdown', () => { game.qa.restart('document'); location.reload(); }, { once: true });")
    text=text.replace("handler.addEventListener('pointerdown', () => {\n        location.reload();","handler.addEventListener('pointerdown', () => {\n        game.qa.restart('overlay');\n        location.reload();")
    main.write_text(text,encoding='utf-8')
    for native in root.glob('android/app/src/main/java/**/MainActivity.java'):
        body=native.read_text(encoding='utf-8')
        if 'WebViewAssetLoader' in body and 'syncGameFocus' not in body:
            backup=folder/native.relative_to(root);backup.parent.mkdir(parents=True,exist_ok=True);backup.write_text(body,encoding='utf-8')
            body=body.replace('                int statusId', '                view.requestFocus(); syncGameFocus();\n                int statusId')
            body=body.replace('    @Override protected void onPause()', '    private void syncGameFocus() { if(web!=null) web.evaluateJavascript("document.documentElement.dataset.nativeForeground=\'"+hasWindowFocus()+"\'",null); }\n    @Override public void onWindowFocusChanged(boolean focused) { super.onWindowFocusChanged(focused); syncGameFocus(); }\n    @Override protected void onPause()')
            native.write_text(body,encoding='utf-8')
        body=native.read_text(encoding='utf-8')
        if 'WebViewAssetLoader' in body and 'syncGameInsets' not in body:
            backup=folder/native.relative_to(root)
            if not backup.is_file():backup.parent.mkdir(parents=True,exist_ok=True);backup.write_text(body,encoding='utf-8')
            body=body.replace('view.requestFocus(); syncGameFocus();','view.requestFocus(); syncGameFocus(); syncGameInsets();')
            body=body.replace('    private void syncGameFocus()', '''    private void syncGameInsets() {
        if(web==null || android.os.Build.VERSION.SDK_INT<30 || web.getRootWindowInsets()==null) return;
        android.graphics.Insets bars=web.getRootWindowInsets().getInsets(android.view.WindowInsets.Type.systemBars() | android.view.WindowInsets.Type.displayCutout());
        String value="{\\\"top\\\":"+bars.top+",\\\"bottom\\\":"+bars.bottom+",\\\"left\\\":"+bars.left+",\\\"right\\\":"+bars.right+"}";
        web.evaluateJavascript("document.documentElement.dataset.nativeInsets='"+value+"'",null);
    }
    private void syncGameFocus()''')
            native.write_text(body,encoding='utf-8')
    observer=Path(__file__).parent/'integrations/seeker-qa-observer.js'
    (root/'src/qa.js').write_bytes(observer.read_bytes())
    build_id='QA-'+uuid.uuid4().hex[:16]
    (root/'public/qa-build.json').write_text(json.dumps({'schemaVersion':1,'buildId':build_id}),encoding='utf-8')
    return {'ok':True,'project':str(root),'build_id':build_id,'backup':str(folder),'instruction':'Solo osservazioni aggiunte, nessun cambio a salute/punteggio/spawn. Ricompila con android_build e installa/avvia, poi seeker_playtest. La diagnostica è disattivata finché il collector non la abilita localmente.'}


class WebView:
    def __init__(self,seeker,serial,package):
        import websocket
        self.seeker=seeker;self.serial=serial;self.package=package;self.events=[];self.counter=0;self.ws=None;self.port=None
        pid=seeker.run(['shell','pidof',package],serial).split()[0]
        self.port=seeker.run(['forward','tcp:0','localabstract:webview_devtools_remote_'+pid],serial)
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            pages=json.loads(opener.open('http://127.0.0.1:'+self.port+'/json/list',timeout=10).read())
            page=next(p for p in pages if p.get('url','').startswith('https://appassets.androidplatform.net/assets/public/index.html'))
            self.ws=websocket.create_connection(page['webSocketDebuggerUrl'].replace('localhost','127.0.0.1'),timeout=12,suppress_origin=True)
            self.call('Runtime.enable',{})
        except BaseException:self.close();raise
    def call(self,method,params):
        self.counter+=1;ident=self.counter;self.ws.send(json.dumps({'id':ident,'method':method,'params':params}))
        while True:
            msg=json.loads(self.ws.recv())
            if msg.get('id')==ident:
                if msg.get('error'):raise RuntimeError(str(msg['error']))
                return msg.get('result',{})
            if msg.get('method')=='Runtime.consoleAPICalled':
                args=msg['params'].get('args',[])
                if args and args[0].get('value')=='FREELLMAPI_QA':
                    try:self.events.append(json.loads(args[1]['value']))
                    except (ValueError,IndexError,KeyError):pass
    def evaluate(self,expression):
        response=self.call('Runtime.evaluate',{'expression':'JSON.stringify('+expression+')','returnByValue':True})
        if response.get('exceptionDetails'):raise RuntimeError('WebView diagnostic evaluation failed')
        return json.loads(response['result']['value'])
    def state(self):return self.evaluate('window.__seekerQA.readState()')
    def close(self):
        if self.ws:self.ws.close();self.ws=None
        if self.port:
            try:self.seeker.run(['forward','--remove','tcp:'+self.port],self.serial)
            except Exception:pass
            self.port=None


def frame_stats(record,warmup_ms,duration_ms):
    frames=record['frames'];start=record['start']+warmup_ms;end=start+duration_ms
    measured=[f for f in frames if start<=f['t']<end];times=[f['t'] for f in frames]
    gaps=[b['t']-a['t'] for a,b in zip(measured,measured[1:])]
    buckets=[sum(start+i*1000<=f['t']<start+(i+1)*1000 for f in measured) for i in range(int(duration_ms//1000))]
    import bisect
    rolling=[bisect.bisect_left(times,t+1000)-bisect.bisect_left(times,t) for t in [start]+[f['t'] for f in measured] if t+1000<=end]
    issues=[]
    if not measured or record['end']<end or not any(t>=end for t in times):issues.append('missing measurement/tail')
    if record.get('interruptions'):issues.append('interruption')
    if any(b['id']!=a['id']+1 for a,b in zip(frames,frames[1:])):issues.append('skipped frame id')
    if any(b['t']<=a['t'] for a,b in zip(frames,frames[1:])):issues.append('non-monotonic frame timestamps')
    if any(f['phase']!='gameplay' or f['visible']!='visible' or not f['foreground'] or f['enemies']<1 for f in measured):issues.append('invalid gameplay/foreground/workload')
    if gaps and max(gaps)>250:issues.append('frame gap >250ms')
    if not buckets or min(buckets)<50 or not rolling or min(rolling)<50:issues.append('below 50 render commits in a complete 1s window')
    ordered=sorted(gaps)
    percentile=lambda p:ordered[min(len(ordered)-1,int(p*(len(ordered)-1)))] if ordered else None
    return {'status':'PASS' if not issues else 'FAIL','issues':issues,'measurement_ms':duration_ms,'warmup_ms':warmup_ms,'frames':len(measured),
            'mean_render_fps':len(measured)/(duration_ms/1000),'min_1s_commits':min(buckets) if buckets else None,'min_rolling_1s_commits':min(rolling) if rolling else None,
            'p95_frame_ms':percentile(.95),'p99_frame_ms':percentile(.99),'max_frame_ms':max(gaps) if gaps else None,'buckets':buckets,
            'limitation':'Render commits after renderer.render, not GPU presentation or manual visual attestation.'}


def run(agent,project,apk_path,serial,package,seconds=60,quick=False):
    """Complete only missing prerequisites; no model retry loop or stale-candidate QA."""
    from android_build import build
    from android_delivery import inspect
    from work_ledger import load
    root=agent.path(project);apk=agent.path(apk_path)
    observer=root/'src/qa.js';resource=Path(__file__).parent/'integrations/seeker-qa-observer.js'
    natives=list(root.glob('android/app/src/main/java/**/MainActivity.java'))
    if not observer.is_file() or not (root/'public/qa-build.json').is_file() or any('syncGameInsets' not in p.read_text(encoding='utf-8') for p in natives):
        prepared=prepare(agent,project)
        if not prepared.get('ok'):return prepared
    elif observer.read_bytes()!=resource.read_bytes():
        if not agent.approve('Aggiornare solo osservatore diagnostico read-only?'):return {'denied':True}
        atomic_bytes(observer,resource.read_bytes())
    source_files=[p for parent in ('src','public','android/app/src/main/java') for p in (root/parent).rglob('*') if p.is_file()]
    needs_build=not apk.is_file() or any(p.stat().st_mtime_ns>apk.stat().st_mtime_ns for p in source_files)
    if needs_build:
        built=build(agent,project,agent.seeker.home)
        if not built.get('ok'):raise RuntimeError('QA prerequisite build failed; preserve successful phases: '+json.dumps(built))
    # Verify the exact local diagnostic identity inside the signed candidate before input.
    import zipfile
    expected=(root/'public/qa-build.json').read_bytes()
    with zipfile.ZipFile(apk) as archive:
        if archive.read('assets/public/qa-build.json')!=expected:raise RuntimeError('Stale diagnostic artifact; rebuild required')
    candidate=inspect(apk,load(agent.session).get('android_delivery',{}))
    if not candidate.get('valid') or candidate.get('package')!=package:raise ValueError('Candidate identity/signature mismatch; not installed: '+json.dumps(candidate))
    serial=agent.seeker.target(serial)
    remote=agent.seeker.run(['shell','pm','path',package],serial,empty_exit_one=True)
    base=next((line[8:] for line in remote.splitlines() if line.startswith('package:') and line.endswith('/base.apk')),None)
    installed=agent.seeker.run(['shell','sha256sum',base],serial).split()[0] if base else None
    sha=hashlib.sha256(apk.read_bytes()).hexdigest()
    tested=getattr(agent,'_qa_tested_artifacts',{})
    key=(sha,bool(quick))
    if key in tested:return dict(tested[key],reused=True,instruction='Identical candidate already tested. Inspect retained failures; change the actual cause before another physical run. No PID/read/wait/force-stop retry loop.')
    if installed!=sha:
        if not agent.approve('Installare lo stesso candidato diagnostico appena compilato sul Seeker?'):return {'denied':True}
        agent.seeker.install(apk,serial)
    result=collect(agent,project,apk_path,serial,package,seconds,quick)
    tested[key]=result;agent._qa_tested_artifacts=tested
    return result


def collect(agent,project,apk_path,serial,package,seconds=60,quick=False):
    from android_delivery import inspect
    from work_ledger import load
    import terminal_ui as ui
    project=agent.path(project);apk=agent.path(apk_path);seconds=max(30,min(180,int(seconds)))
    if not agent.approve('Eseguire input touch reali e collaudo su '+package+' / '+serial+'? Nessuna azione finanziaria.'):
        return {'denied':True}
    seeker=agent.seeker;serial=seeker.target(serial)
    candidate=inspect(apk,load(agent.session).get('android_delivery',{}))
    if not candidate.get('valid'):raise ValueError('Il candidato non supera firma/bundle/identità: '+json.dumps(candidate))
    expected=json.loads((project/'public/qa-build.json').read_text())['buildId']
    folder=Path(agent.root)/'gameplay_qa'/('run-'+datetime.now().strftime('%Y%m%d_%H%M%S')+'-'+uuid.uuid4().hex[:6]);folder.mkdir(parents=True)
    result={'schema_version':1,'classification':'physical_seeker','candidate':candidate,'serial_sha256':hashlib.sha256(serial.encode()).hexdigest(),
            'build_id':expected,'started_utc':datetime.now(timezone.utc).isoformat(),'checks':{},'inputs':[],'state_log':[],
            'limits':['ADB-generated Android touch, not a human finger. Render commits are not GPU presentation.','No Store, payment or wallet certification.'],'evidence_dir':str(folder)}
    view=None;old_stay=None
    def write(name,value):atomic_bytes(folder/name,json.dumps(value,ensure_ascii=False,indent=2).encode())
    def guard():
        policy=seeker.run(['shell','dumpsys','window','policy'],serial)
        if re.search(r'\bshowing=true|\binputRestricted=true',policy):raise RuntimeError('Seeker locked: unlock it; no lock bypass or input outside the app.')
        windows=seeker.run(['shell','dumpsys','window'],serial)
        focus=next((line for line in windows.splitlines() if 'mCurrentFocus=' in line),'')
        if package not in focus:raise RuntimeError('App not focused; no touch sent to another window.')
    def state():
        s=view.state();result['state_log'].append(s);return s
    def wait(predicate,timeout):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            try:
                s=state()
                if predicate(s):return s
            except (RuntimeError,KeyError,TypeError):pass
            time.sleep(.15)
        raise TimeoutError('Real game state did not reach required predicate in '+str(timeout)+'s')
    def check(name,passed,details):
        result['checks'][name]={'status':'PASS' if passed else 'FAIL','evidence':details};write('results-partial.json',result)
        ui.write('Seeker QA · '+name+' · '+result['checks'][name]['status'])
    try:
        if seeker.run(['shell','getprop','ro.kernel.qemu'],serial).strip()=='1':raise RuntimeError('Emulator not accepted as physical Seeker')
        model=seeker.run(['shell','getprop','ro.product.model'],serial);result['device']={'model':model,'android':seeker.run(['shell','getprop','ro.build.version.release'],serial),'fingerprint':seeker.run(['shell','getprop','ro.build.fingerprint'],serial)}
        if 'seeker' not in model.lower():raise RuntimeError('Expected physical Seeker model')
        remote=seeker.run(['shell','pm','path',package],serial).splitlines()
        if len(remote)!=1 or not remote[0].endswith('/base.apk'):raise RuntimeError('Split/unknown installed artifact')
        installed=seeker.run(['shell','sha256sum',remote[0][8:]],serial).split()[0]
        if installed!=candidate['sha256']:raise RuntimeError('Installed artifact hash mismatch')
        if candidate.get('package')!=package:raise RuntimeError('Package differs from inspected artifact')
        seeker.launch(package,serial);guard()
        old_stay=seeker.run(['shell','settings','get','global','stay_on_while_plugged_in'],serial)
        seeker.run(['shell','svc','power','stayon','usb'],serial)
        view=WebView(seeker,serial,package)
        view.evaluate("(localStorage.setItem('freellmapi.qa','1'),true)")
        view.call('Page.reload',{})
        wait(lambda s:s['phase']=='gameplay',10)
        identity=view.evaluate('window.__seekerQA.identity()');result['observer']=identity
        if identity['buildId']!=expected:raise RuntimeError('Wrong diagnostic build identity')
        size=seeker.run(['shell','wm','size'],serial);physical=list(map(int,re.findall(r'(\d+)x(\d+)',size)[-1]));width,height=physical
        # First real tap calibrates Android WebView origin without assuming status bar offsets.
        guard();seeker.run(['shell','input','tap',str(width//2),str(height//2)],serial);time.sleep(.15)
        sample=state();scale=width/sample['viewport']['w'];offset_x=width/2-((sample['input']['x']+1)/2*sample['viewport']['w'])*scale
        offset_y=height/2-((1-sample['input']['y'])/2*sample['viewport']['h'])*scale
        insets=sample['viewport'].get('nativeInsets')
        if not insets:raise RuntimeError('Native system-bar insets missing; refuse touch near Android navigation/status UI.')
        result['calibration']={'physical':physical,'scale':scale,'offset':[offset_x,offset_y],'observed_input':sample['input']}
        def hold(nx,ny,duration=500):
            guard();s=state();v=s['viewport'];x=round(offset_x+(nx+1)/2*v['w']*scale);y=round(offset_y+(1-ny)/2*v['h']*scale)
            x=max(insets['left']+12,min(width-insets['right']-13,x));y=max(insets['top']+12,min(height-insets['bottom']-13,y));stamp=time.time();command=[seeker.adb,'-s',serial,'shell','input','swipe',str(x),str(y),str(x),str(y),str(duration)]
            proc=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
            try:
                time.sleep(min(.2,duration/2000));during=state();proc.wait(timeout=duration/1000+5)
                if proc.returncode:raise RuntimeError('ADB touch failed')
            finally:
                if proc.poll() is None:proc.kill();proc.wait()
                proc.stdout.close()
            time.sleep(.08);after=state();result['inputs'].append({'kind':'adb_swipe_hold','nx':nx,'ny':ny,'xy':[x,y],'duration_ms':duration,'host_epoch':stamp,'generation':s['generation']})
            return during,after
        def aim(x,y,duration=700):
            s=state();inp=s['input'];target=s['ship']['target'] or {};gx=abs(target.get('x',0)/inp['x']) if abs(inp['x'])>.02 else s['camera']['halfWidth']
            gy=abs(target.get('y',0)/inp['y']) if abs(inp['y'])>.02 else 3
            return hold(max(-.9,min(.9,x/max(.1,gx))),max(-.9,min(.9,y/max(.1,gy))),duration)
        def screenshot(name):
            guard()
            image=subprocess.run([seeker.adb,'-s',serial,'exec-out','screencap','-p'],capture_output=True,timeout=10,check=True).stdout
            if not image.startswith(b'\x89PNG'):raise RuntimeError('Invalid native screenshot')
            atomic_bytes(folder/name,image)
            result.setdefault('screenshots',[]).append(name)
        screenshot('initial.png')
        positions=[];visible=True;trusted_before=sample['counts']['trustedTouches']
        for x,y in [(-.7,0),(.7,0),(0,.7),(0,-.7)]:
            during,after=hold(x,y,600);positions.append(after['ship'])
            visible=visible and all(abs(c['x'])<=1.02 and abs(c['y'])<=1.02 for c in after['ship']['corners'])
            screenshot('direction-'+str(len(positions))+'.png')
        final=state();check('touch_movement',positions[0]['x']<positions[1]['x'] and positions[2]['y']>positions[3]['y'] and final['counts']['trustedTouches']>trusted_before,{'positions':positions,'trusted_touches':final['counts']['trustedTouches']})
        check('ship_stays_visible',visible,{'ship_positions':positions})
        before=state();during,after=hold(0,0,1000)
        check('fire_press_release',during['input']['isFiring'] and not after['input']['isFiring'] and after['counts']['shots']>before['counts']['shots'],{'before':before['counts'],'during':during['counts'],'after':after['counts'],'released':after['input']})
        enemy1=wait(lambda s:len(s['enemies'])>0 and s['visibleEnemies']>0,12);time.sleep(.5);enemy2=state();old={e['id']:e for e in enemy1['enemies']}
        check('enemies_spawn_and_move',any(e['id'] in old and e['z']>old[e['id']]['z'] for e in enemy2['enemies']),{'before':enemy1['enemies'],'after':enemy2['enemies']})
        initial=state();deadline=time.monotonic()+18
        while time.monotonic()<deadline and state()['phase']=='gameplay':
            s=state();targets=[e for e in s['enemies'] if -9<e['z']<0 and abs(e['x'])<s['camera']['halfWidth']-.4 and abs(e['y'])<s['camera']['halfHeight']-.4]
            if targets:aim(targets[0]['x'],targets[0]['y'],1000)
            else:time.sleep(.2)
            if state()['counts']['hits']>initial['counts']['hits']:break
        scored=state();check('bullet_hit_and_score',scored['counts']['hits']>initial['counts']['hits'] and scored['score']>initial['score'],{'before':initial['counts'],'after':scored['counts'],'score_before':initial['score'],'score_after':scored['score']})
        if not quick:
            cycles=[]
            for cycle in range(3):
                s=state()
                if s['phase']!='results':
                    # Real move/release, then let actual incoming enemies collide naturally.
                    aim(0,0,60)
                    end=time.monotonic()+45
                    while time.monotonic()<end:
                        s=state()
                        if s['phase']=='results':break
                        reachable=[e for e in s['enemies'] if -3<e['z']<0 and abs(e['x'])<s['camera']['halfWidth']-.5 and abs(e['y'])<min(2.7,s['camera']['halfHeight']-.5)]
                        if reachable:aim(reachable[0]['x'],reachable[0]['y'],30)
                        else:time.sleep(.2)
                    s=state()
                if s['phase']!='results':
                    cycles.append({'status':'FAIL','reason':'No natural game over in bounded interval'});break
                wait(lambda q:q['overlays']>=1,3);screenshot('gameover-'+str(cycle+1)+'.png');old_generation=s['generation'];view.events=[]
                guard();seeker.run(['shell','input','tap',str(width//2),str(height//2)],serial)
                result['inputs'].append({'kind':'adb_restart_tap','xy':[width//2,height//2],'generation':old_generation})
                new=wait(lambda q:q['generation']!=old_generation,8)
                requests=[e for e in view.events if e.get('kind')=='restart_request' and e.get('generation')==old_generation]
                cycles.append({'status':'PASS' if new['score']==0 and new['phase']=='gameplay' and new['overlays']==0 and len(requests)==1 else 'FAIL','old_generation':old_generation,'new_generation':new['generation'],'restart_requests':requests,'new_state':new})
                screenshot('restart-'+str(cycle+1)+'.png')
            check('game_over_restart_3_cycles',len(cycles)==3 and all(c['status']=='PASS' for c in cycles),cycles)
            wait(lambda s:s['phase']=='gameplay' and s['visibleEnemies']>=3,12)
            record_start=view.evaluate('window.__seekerQA.startRecording()');end_t=record_start['start']+5000+seconds*1000+300
            progress=time.monotonic()
            while True:
                s=state()
                if s['t']>=end_t or s['phase']!='gameplay':break
                best=dodge_target(s)
                aim(*best,500)
                if time.monotonic()-progress>=15:
                    ui.write('Seeker QA · performance · '+str(round((s['t']-record_start['start'])/1000))+'s · gameplay reale · nemici '+str(s['visibleEnemies']))
                    progress=time.monotonic()
            record=view.evaluate('window.__seekerQA.stopRecording()');write('frames.json',record)
            performance=frame_stats(record,5000,seconds*1000);result['performance']=performance
            check('performance',performance['status']=='PASS',performance)
        result['console_events']=view.events
        result['memory_pss']=seeker.run(['shell','dumpsys','meminfo',package],serial)
        result['app_log']=seeker.logs(package,serial,300)
    except Exception as exc:
        result['error']=type(exc).__name__+': '+str(exc)
    finally:
        if view:
            try:view.evaluate("(localStorage.removeItem('freellmapi.qa'),true)")
            except Exception:pass
            view.close()
        if old_stay is not None:
            try:seeker.run(['shell','settings','put','global','stay_on_while_plugged_in',old_stay],serial)
            except Exception:pass
        result['finished_utc']=datetime.now(timezone.utc).isoformat()
        required=['touch_movement','ship_stays_visible','fire_press_release','enemies_spawn_and_move','bullet_hit_and_score']+([] if quick else ['game_over_restart_3_cycles','performance'])
        result['status']='PASS' if not result.get('error') and all(result['checks'].get(k,{}).get('status')=='PASS' for k in required) else 'FAIL'
        result['non_verified']=[k for k in required if k not in result['checks']]
        write('results.json',result)
        atomic_bytes(folder/'REPORT.md',('# Collaudo fisico Seeker\n\n'+result['status']+'\n\n'+'\n'.join('- '+k+': '+v['status'] for k,v in result['checks'].items())+'\n\n'+str(result.get('error',''))+'\n\n'+'\n'.join(result['limits'])).encode('utf-8'))
        latest=Path(agent.root)/'gameplay_qa'
        atomic_bytes(latest/'results.json',json.dumps(result,ensure_ascii=False,indent=2).encode('utf-8'));atomic_bytes(latest/'REPORT.md',(folder/'REPORT.md').read_bytes())
    return {'status':result['status'],'checks':{k:v['status'] for k,v in result['checks'].items()},'performance':result.get('performance'),'error':result.get('error'),'non_verified':result['non_verified'],'report':str(folder/'REPORT.md'),'results':str(folder/'results.json'),'instruction':'Esiti di input Android e stato reale; i fallimenti restano nei file. Correggi le cause e ripeti sul nuovo candidato, senza alterare stato o inventare PASS.'}
