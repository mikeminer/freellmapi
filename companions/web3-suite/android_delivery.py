"""Read-only evidence for APK delivery. Model prose is never an acceptance check."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import zipfile
from work_ledger import load, save


def request(session, text):
    from seeker_gameplay import requested as gameplay_requested
    if gameplay_requested(text):return # Physical gameplay has separate acceptance, not installed-PID completion.
    from request_choices import norm
    value = norm(text)
    if re.search(r'\bnon\s+(?:creare|compilare|realizzare)\s+(?:un[ao]?\s+)?(?:apk|app)\b', value):
        return
    if not (re.search(r'\bapk\b', value) and re.search(r'build|compil|realizza|crea|conclud|install', value)):
        return
    data = load(session)
    goal = data.setdefault('android_delivery', {})
    goal['required'] = True
    goal['status']='active'
    goal['install'] = goal.get('install', False) or bool(re.search(r'install', value))
    goal['launch'] = goal.get('launch', False) or bool(re.search(r'avvial|avvio|launch', value))
    goal['reports'] = goal.get('reports', False) or ('report.md' in value and 'results.json' in value)
    for key, pattern in [('package', r'\bpackage\b[^\n:]{0,65}?\b([A-Za-z][\w]*(?:\.[A-Za-z][\w]*)+)'),
                         ('serial', r'\b(?:seeker|dispositivo|device)(?:\s+autorizzato)?\s+([A-Z0-9]{10,30})\b'),
                         ('marker', r'\b(AUTONOMY-[\w-]+)\b')]:
        match = re.search(pattern, text, re.I)
        if match:
            goal[key] = match[1]
    if data.get('token_mint'):
        goal['mint'] = data['token_mint']
    base=re.search(r'(?i)\b(?:progetto|project)\s+([A-Z]:[\\/][^:\n]+)(?=:)',text)
    if base and Path(base[1].strip()).is_dir():
        goal['base_project']=str(Path(base[1].strip()).resolve())
    goal['bundled_web']=bool('bundled' in data.get('explicit_choices',{}).get('apk_architecture',{}).get('answer','') and goal.get('base_project') and (Path(goal['base_project'])/'package.json').is_file())
    save(session, data)


def goal_for(agent):
    return load(agent.session).get('android_delivery', {}) if getattr(agent, 'session', None) else {}


def physical_ready(observed):
    goal=observed.get('goal',{})
    device=observed.get('device',{})
    return bool(observed.get('apk',{}).get('valid') and
                (not goal.get('install') or device.get('same_apk_installed')) and
                (not goal.get('launch') or str(device.get('pid','')).strip()))


def next_build_action(agent):
    """Choose the next prerequisite from actual staged files, not model prose."""
    goal=goal_for(agent)
    if not goal.get('bundled_web') or getattr(agent,'android_build_attempted',False):return None
    preferred=goal.get('staged_project')
    roots=([Path(preferred)] if preferred and Path(preferred).is_relative_to(agent.root) and Path(preferred).is_dir() else [])
    roots += [Path(agent.root)]+[p for p in Path(agent.root).iterdir() if p.is_dir() and p.name not in ('node_modules','.git') and p not in roots]
    for project in roots:
        config=project/'android/app/build.gradle'
        if not config.is_file() or not (project/'package.json').is_file():continue
        body=config.read_text(encoding='utf-8-sig')
        match=re.search(r'(?m)^\s*applicationId\s+[\"\']([^\"\']+)[\"\'].*$',body)
        if goal.get('package') and match and match[1]!=goal['package']:
            return {'tool':'edit_file','path':str(config.relative_to(agent.root)),
                    'current_unique_line':match[0],'required_application_id':goal['package'],
                    'instruction':'Change only applicationId on this unique line. A debug variant can keep the existing namespace and Java package; no manifest or gameplay rewrite needed.'}
        return {'tool':'android_build','path':str(project.relative_to(agent.root)),
                'instruction':'Identity prerequisite ready. Build bundled web and signed debug APK now with android_build. Preserve staged sources; no further discovery or memory inspection needed before the build.'}
    return None


def recovery_route(agent, payload, excluded):
    """One fresh, compatible alternative after observed lack of progress."""
    from server_integration import eligible
    from model_health import catalog_context
    rows = agent._api('models?execution_status=ready')['data']
    aliases = {name.rsplit('/', 1)[-1].lower() for name in excluded if name}
    observed=getattr(agent,'model_observations',{})
    def score(row):
        entry=next((v for k,v in observed.items() if k.rsplit('/',1)[-1].lower()==row['id'].rsplit('/',1)[-1].lower()),{})
        return (bool(entry.get('tool_probe_passed')),bool(entry.get('tool_calls_observed')))
    import time
    for row in sorted(eligible(rows, payload),key=score,reverse=True):
        name = row['id']
        if name.startswith(('auto', 'fusion')) or name.rsplit('/', 1)[-1].lower() in aliases:
            continue
        if getattr(agent,'cooldowns',{}).get(name,0)>time.time(): continue
        agent.context_limit = catalog_context(rows, name)
        return name
    return None


def tools_dir():
    sdk = Path(os.getenv('ANDROID_SDK_ROOT') or os.getenv('ANDROID_HOME') or Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'Android/Sdk')
    candidates = sorted((sdk / 'build-tools').glob('*'), reverse=True)
    return next((p for p in candidates if (p / 'aapt.exe').is_file() and (p / 'apksigner.bat').is_file()), None)


def run(command):
    env = dict(os.environ)
    if not env.get('JAVA_HOME'):
        candidates = sorted(Path(os.getenv('ProgramFiles', 'C:/Program Files')).glob('Microsoft/jdk-17*'))
        if candidates:
            env['JAVA_HOME'] = str(candidates[-1])
    result = subprocess.run([str(x) for x in command], capture_output=True, text=True,
                            encoding='utf-8', errors='replace', timeout=45, env=env,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return result.returncode, (result.stdout + '\n' + result.stderr).strip()


def inspect(path, goal):
    path = Path(path).resolve()
    result = {'path': str(path), 'valid': False}
    if path.suffix.lower() != '.apk' or not path.is_file():
        return dict(result, error='File APK assente')
    result.update(bytes=path.stat().st_size, sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    directory = tools_dir()
    if not directory:
        return dict(result, error='Android build-tools aapt/apksigner non disponibili')
    code, badging = run([directory / 'aapt.exe', 'dump', 'badging', path])
    match = re.search(r"package: name='([^']+)' versionCode='([^']+)' versionName='([^']+)'", badging)
    if code or not match:
        return dict(result, error='Manifest APK non verificabile', detail=badging[-1000:])
    result.update(package=match[1], version_code=match[2], version_name=match[3])
    code, signature = run([directory / 'apksigner.bat', 'verify', '--print-certs', path])
    cert = re.search(r'certificate SHA-256 digest:\s*([0-9a-fA-F]+)', signature)
    result['signature_verified'] = code == 0 and bool(cert)
    if cert:
        result['certificate_sha256'] = cert[1]
    if not result['signature_verified']:
        return dict(result, error='Firma APK non verificata', detail=signature[-1000:])
    needles = {k: str(goal[k]).encode() for k in ('mint', 'marker') if goal.get(k)}
    found = set()
    total = 0
    with zipfile.ZipFile(path) as archive:
        result['bundled_html']=any(entry.filename.startswith('assets/') and entry.filename.lower().endswith('/index.html') for entry in archive.infolist())
        for entry in archive.infolist():
            if not entry.filename.lower().endswith(('.js', '.json', '.html', '.txt')) or entry.file_size > 8_000_000:
                continue
            total += entry.file_size
            if total > 32_000_000:
                break
            body = archive.read(entry)
            if entry.filename.endswith('/game-config.json'):
                try:
                    config=json.loads(body)
                    result['configured_mint']=config.get('mint') or config.get('tokenMint')
                except (ValueError,AttributeError):pass
            found.update(key for key, needle in needles.items() if needle in body)
    result['embedded'] = {key: key in found for key in needles}
    result['valid'] = (not goal.get('package') or result['package'] == goal['package']) and all(result['embedded'].values()) and (not goal.get('bundled_web') or result['bundled_html'])
    if goal.get('mint') and result.get('configured_mint') and result['configured_mint']!=goal['mint']:
        result['valid']=False
    if not result['valid']:
        result['error'] = 'Package, bundle web, mint o identificativo non corrispondono alla richiesta'
        result['next_action'] = 'Per il gioco bundled: esegui il build web (npm run build) nella copia del progetto, sincronizza dist/ in android/app/src/main/assets/public/ e ricompila il debug APK. Il solo Gradle non include sorgenti Vite/public automaticamente. Mantieni mint e run correnti, non ricreare il progetto.'
        if goal.get('package') and result['package']!=goal['package']:
            result['next_action']='Imposta applicationId in android/app/build.gradle a '+goal['package']+'. namespace e package Java da soli non cambiano l’identità APK. Poi android_build nella stessa copia.'
    return result


def candidates(root):
    root = Path(root)
    found = []
    for pattern in ('*.apk', 'deliverables/*.apk', 'android/app/build/outputs/apk/*/*.apk', '*/android/app/build/outputs/apk/*/*.apk'):
        found.extend(p for p in root.glob(pattern) if p.is_file())
    return sorted(set(found), key=lambda p: p.stat().st_mtime, reverse=True)[:8]


def status(agent, path=None):
    goal = goal_for(agent)
    result = {'required': bool(goal.get('required')), 'goal': goal, 'complete': False, 'missing': []}
    paths = [agent.path(path)] if path else candidates(agent.root)
    previous = goal.get('apk_path')
    if previous and not path and Path(previous).is_file() and Path(previous) not in paths:
        paths.insert(0, Path(previous))
    apk = None
    for candidate in paths:
        try:
            observed = inspect(candidate, goal)
        except (OSError, ValueError, zipfile.BadZipFile, subprocess.TimeoutExpired) as exc:
            observed = {'path': str(candidate), 'valid': False, 'error': type(exc).__name__ + ': ' + str(exc)[:400]}
        result.setdefault('candidates', []).append(observed)
        if observed.get('valid'):
            apk = observed
            break
    if not apk:
        result['missing'].append('APK firmato con package, token e identificativo richiesti')
    else:
        result['apk'] = apk
        if goal.get('required'):
            data = load(agent.session)
            data['android_delivery']['apk_path'] = apk['path']
            save(agent.session, data)
        if goal.get('install') or goal.get('launch'):
            try:
                serial = agent.seeker.target(goal.get('serial'))
                remote = agent.seeker.run(['shell', 'pm', 'path', apk['package']], serial, empty_exit_one=True)
                base = next((line[8:] for line in remote.splitlines() if line.startswith('package:') and line.endswith('/base.apk')), '')
                installed_hash = agent.seeker.run(['shell', 'sha256sum', base], serial).split()[0] if base else ''
                result['device'] = {'serial': serial, 'package': apk['package'], 'installed_sha256': installed_hash,
                                    'same_apk_installed': installed_hash.lower() == apk['sha256']}
                if not result['device']['same_apk_installed']:
                    result['missing'].append('Installazione dello stesso APK verificata sul dispositivo')
                if goal.get('launch'):
                    pid = agent.seeker.run(['shell', 'pidof', apk['package']], serial, empty_exit_one=True)
                    result['device']['pid'] = pid
                    if not pid.strip():
                        result['missing'].append('App avviata (PID sul dispositivo)')
            except (RuntimeError, ValueError, OSError) as exc:
                result['device_error'] = str(exc)
                result['missing'].append('Verifica del dispositivo: ' + str(exc))
    if goal.get('reports'):
        for name in ('REPORT.md', 'results.json'):
            target = Path(agent.root) / name
            try:
                raw = target.read_text(encoding='utf-8-sig')
                if not raw.strip():
                    raise ValueError('vuoto')
                if name.endswith('.json'):
                    parsed = json.loads(raw)
                    if not isinstance(parsed, dict):
                        raise ValueError('oggetto JSON richiesto')
                expected=[goal.get('package'),goal.get('marker'),goal.get('mint')]
                if apk: expected.append(apk['sha256'])
                if any(str(value).lower() not in raw.lower() for value in expected if value):
                    result['missing'].append(name+' con riferimenti al package, token, run e hash APK correnti; un report storico copiato non conta')
            except (OSError, ValueError):
                result['missing'].append(name + ' salvato e leggibile')
    result['complete'] = not result['missing']
    result['instruction'] = ('Consegna verificata.' if result['complete'] else
                             'Completa soltanto le fasi mancanti. Conserva build e decisioni già verificate; non ricreare il progetto. Un impedimento reale va riportato come INCOMPLETE, non come successo.')
    return result
