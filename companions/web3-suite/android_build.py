"""Build a bundled Vite/Gradle debug app, preserving the source project."""
import html
import json
import os
from pathlib import Path
import shutil
import uuid

from android_delivery import goal_for, inspect
from command_environment import environment
from shell_runner import run
from project_copy import is_link


def quote(value):
    return "'"+str(value).replace("'","''")+"'"


def gradle(project):
    wrapper=project/'android/gradlew.bat'
    if wrapper.is_file():return wrapper
    available=shutil.which('gradle')
    if available:return Path(available)
    versions=sorted((Path.home()/'.gradle/wrapper/dists').glob('*/*/gradle-*/bin/gradle.bat'),reverse=True)
    if not versions:raise ValueError('Gradle non disponibile: conserva il build web e configura il wrapper Gradle del progetto.')
    return versions[0]


def sync_assets(project,goal):
    project=Path(project).resolve()
    raw_dist=project/'dist'
    raw_target=project/'android/app/src/main/assets/public'
    if any(is_link(p) for p in [raw_dist,raw_target,*raw_target.parents] if p.is_relative_to(project)):
        raise ValueError('Link nel percorso del bundle/assets: nessuna modifica.')
    dist=raw_dist.resolve()
    target=raw_target.resolve()
    if not dist.is_relative_to(project) or not target.is_relative_to(project) or not (dist/'index.html').is_file():
        raise ValueError('Build web dist/index.html assente o percorso fuori dal progetto. Nessun asset Android sostituito.')
    for node in dist.rglob('*'):
        if is_link(node):raise ValueError('Symlink nel bundle: sincronizzazione rifiutata.')
    parent=target.parent
    parent.mkdir(parents=True,exist_ok=True)
    if any(is_link(p) for p in [parent,*parent.parents] if p.is_relative_to(project)):
        raise ValueError('Link nel percorso degli asset Android: nessuna modifica.')
    staged=parent/('.public-staged-'+uuid.uuid4().hex)
    backup=parent/('.public-previous-'+uuid.uuid4().hex)
    try:
        shutil.copytree(dist,staged)
        if goal.get('marker'):
            # Explicitly requested development build identity; never added to release builds.
            index=staged/'index.html';body=index.read_text(encoding='utf-8')
            label=html.escape(goal['marker']+' · '+goal.get('mint',''))
            watermark='<div id="freellmapi-build-marker" style="position:fixed;top:112px;left:10px;right:10px;z-index:9999;pointer-events:none;font:9px monospace;color:#8cf;background:#071326aa;overflow-wrap:anywhere">'+label+'</div>'
            body=body.replace('</body>',watermark+'</body>') if '</body>' in body else body+watermark
            index.write_text(body,encoding='utf-8')
        if target.exists():target.rename(backup)
        staged.rename(target)
    except BaseException:
        if backup.exists() and not target.exists():backup.rename(target)
        raise
    finally:
        # Both resolved targets are literal children of the verified project assets folder.
        for temporary in (staged,backup):
            if temporary.exists() and temporary.resolve().parent==parent and temporary.name.startswith(('.public-staged-','.public-previous-')):
                shutil.rmtree(temporary)
    return target


def build(agent,path,home):
    project=agent.path(path)
    if not (project/'package.json').is_file() or not (project/'android/app/build.gradle').is_file():
        raise ValueError('android_build richiede la copia del progetto Vite con modulo Android app/build.gradle.')
    if not agent.approve('Compilare web + APK debug in '+str(project)+' e sincronizzare solo gli asset Android generati?'):
        return {'denied':True}
    tool=gradle(project)
    goal=goal_for(agent)
    config=project/'public/game-config.json'
    if config.is_file() and goal.get('mint'):
        values=json.loads(config.read_text(encoding='utf-8-sig'))
        if (values.get('mint') or values.get('tokenMint'))!=goal['mint']:
            return {'ok':False,'phase':'configuration','expected_mint':goal['mint'],'instruction':'Usa il mint richiesto in public/game-config.json prima del build. Nessun token nuovo.'}
    web=run('Set-Location -LiteralPath '+quote(project)+'; & npm.cmd run build; exit $LASTEXITCODE',project,home,180)
    if not web.startswith('Exit code: 0'):
        return {'ok':False,'phase':'web_build','output':web,'instruction':'Correggi il build web, conserva sorgenti e lavoro; nessun APK dichiarato.'}
    assets=sync_assets(project,goal)
    env=environment()
    command='Set-Location -LiteralPath '+quote(project/'android')+'; '
    if env.get('JAVA_HOME'):command+='$env:JAVA_HOME='+quote(env['JAVA_HOME'])+'; '
    if env.get('ANDROID_HOME'):command+='$env:ANDROID_HOME='+quote(env['ANDROID_HOME'])+'; '
    command+='& '+quote(tool)+' --offline --no-daemon --console=plain :app:assembleDebug; exit $LASTEXITCODE'
    compiled=run(command,project,home,300)
    if not compiled.startswith('Exit code: 0'):
        return {'ok':False,'phase':'android_build','assets':str(assets),'output':compiled,
                'instruction':'Build web conservato. Correggi solo l’errore Gradle e riprendi; non ricopiare il progetto né ripristinare metadata vecchi.'}
    apk=project/'android/app/build/outputs/apk/debug/app-debug.apk'
    evidence=inspect(apk,goal)
    from work_ledger import load,save
    from datetime import datetime,timezone
    data=load(agent.session)
    data.setdefault('android_build_receipts',[]).append({'project':str(project),'utc':datetime.now(timezone.utc).isoformat(),'web_exit':0,'gradle_exit':0,'apk':evidence})
    data['android_build_receipts']=data['android_build_receipts'][-8:]
    save(agent.session,data)
    return {'ok':bool(evidence['valid']),'phase':'verified_apk' if evidence['valid'] else 'apk_validation',
            'apk':evidence,'web_output':web[-5000:],'gradle_output':compiled[-7000:],
            'instruction':'Il build comprende web dist e asset bundled. Installa con seeker_install SOLO se ok, poi avvia, verifica android_delivery_status e salva i report con prove reali. Il watermark debug richiesto è inserito nell’HTML generato, non altera il gameplay.'}
