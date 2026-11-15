"""Durable multi-file/code-state-environment transaction with crash rollback."""
import json,shutil
from pathlib import Path
import repair_lab as lab
import managed_state,dependency_env

def fingerprint(path): return lab.digest(path) if Path(path).exists() else None

def restore(folder,record):
    items=record['transaction']
    for item in items:
        if item['before'] is not None and fingerprint(folder/item['backup'])!=item['before']: raise ValueError('Backup danneggiato: ripristino bloccato.')
        if fingerprint(item['target']) not in (item['before'],item['after']):
            record['state']='recovery_conflict';lab.store(folder/'record.json',record)
            raise ValueError('recovery_conflict: file cambiato da un altro processo. Ripristino bloccato su '+item['target']+'. Le modifiche successive non sono state sovrascritte.')
    for item in reversed(items):
        target=Path(item['target'])
        if item['before'] is None: target.unlink(missing_ok=True)
        else: lab.replace(folder/item['backup'],target)
    hashes_match=all(fingerprint(item['target'])==item['before'] for item in items)
    removed=all(not Path(item['target']).exists() for item in items if item['before'] is None)
    if not hashes_match or not removed:
        record['state']='recovery_conflict';lab.store(folder/'record.json',record)
        raise ValueError('recovery_conflict: ripristino non verificato. Gli hash finali non corrispondono ai backup; nessuna dichiarazione di successo.')
    record['state']='rolled_back';lab.store(folder/'record.json',record)
    lab.store(folder/'rollback.json',{'observed':True,'origin':'repair_runner','state':'rolled_back','hashes_match':hashes_match,'new_files_removed':removed,'ok':True,'files':[{'target':item['target'],'sha256':fingerprint(item['target'])} for item in items]})
    lab.publish_report(folder,record)

def _targets_allowed(record,home,targets):
    root=Path(record['source_root']).resolve(); home=Path(home).resolve()
    for source,target in targets:
        path=Path(target).resolve()
        if not (path.is_relative_to(root) or path.is_relative_to(home)):
            raise ValueError('repair_identity_mismatch: destinazione '+str(path)+' fuori da '+str(root)+'. Attivazione bloccata.')

def promote(home,ident):
    folder,record=lab.load(home,ident)
    lab.assert_identity(folder,ident,record)
    if record['state']=='activated_restart_required': raise ValueError('repair_already_activated: ID '+ident+' già attivato su '+record['source_root']+'. Non ripetere l\'attivazione.')
    root=Path(record['source_root']).resolve()
    if record.get('scope')=='qa_project':
        if 'qa_runs' not in root.parts or root==lab.ROOT.resolve(): raise ValueError('Destinazione di prova non valida.')
        if record.get('dependencies') or record.get('migrations'): raise ValueError('La prova di progetto non può attivare dipendenze o stato del runtime.')
    elif root!=lab.ROOT.resolve(): raise ValueError('La riparazione appartiene a un altro progetto: attivazione bloccata.')
    if record['state']!='tested' or not record['evidence']['suite']['ok']: raise ValueError('Test non superati: attivazione vietata.')
    if lab.manifest(folder/'stage')!=record['tested_manifest']: raise ValueError('Stage cambiato dopo i test: ripeti la verifica.')
    managed_state.check_plan(home,folder,record);python=dependency_env.checked_python(record)
    with lab.deployment_lock(home):
        if any(not (root/p).exists() or lab.digest(root/p)!=h for p,h in record['baseline'].items()): raise ValueError('Codice installato cambiato: crea una nuova riparazione.')
        managed_state.check_plan(home,folder,record)
        if any((root/name).exists() for name in record.get('added',[])): raise ValueError('Un nuovo modulo esiste già nel codice installato.')
        targets=[(folder/'stage'/name,root/name) for name in sorted(lab.changed_files(record))]
        regression=root/('test_autorepair_'+ident+'.py')
        if regression.exists(): raise ValueError('Test di regressione già presente.')
        targets.append((folder/'stage'/'test_repair_regression.py',regression))
        for name in record.get('migrations',{}): targets.append((folder/'state-stage'/(name+'.json'),managed_state.path(home,name)))
        if python:
            pointer=folder/'environment-pointer.json';lab.store(pointer,{'python':python,'repair_id':ident})
            targets.append((pointer,Path(home)/'runtime-environment.json'))
        _targets_allowed(record,home,targets)
        transaction=[]
        for index,(source,target) in enumerate(targets):
            before=fingerprint(target);backup='backup-'+str(index)
            if before is not None: shutil.copy2(target,folder/backup)
            transaction.append({'source':str(source),'target':str(target),'before':before,'after':lab.digest(source),'backup':backup})
        record.update(state='activating',transaction=transaction);lab.store(folder/'record.json',record)
        # Journal is durable before the first change. Bootstrap restores interrupted releases.
        try:
            for item in transaction:
                if fingerprint(item['target'])!=item['before']: raise ValueError('Conflitto durante attivazione.')
                Path(item['target']).parent.mkdir(parents=True,exist_ok=True);lab.replace(item['source'],item['target'])
            managed_state.seed_test_home(folder,record)
            post=lab.run_tests(root,folder,'post_activation',python=python)
            record['post_activation']=post
            if not post['ok']: restore(folder,record)
            else:
                record['state']='activated_restart_required';lab.store(folder/'record.json',record)
                changed=sorted(lab.changed_files(record))
                hashes={name:fingerprint(root/name) for name in changed}
                expected={name:record['tested_manifest'][name] for name in changed}
                regression=root/('test_autorepair_'+ident+'.py')
                hashes_match=hashes==expected and regression.is_file() and fingerprint(regression)==record['tested_manifest'].get('test_repair_regression.py')
                lab.store(folder/'activation.json',{'observed':True,'origin':'repair_runner','state':record['state'],'hashes_match':hashes_match,'hashes':hashes,'exit_code':0 if hashes_match else 1,'ok':hashes_match})
                if not hashes_match:
                    record['state']='activating';lab.store(folder/'record.json',record)
                    raise ValueError('repair_identity_mismatch: gli hash attivati non coincidono con il manifest testato.')
                published=lab.publish_report(folder,record)
        except BaseException:
            restore(folder,record);raise
    return {'state':record['state'],'id':ident,'source_root':record['source_root'],'evidence':record.get('post_activation'),'restart_required':True,'files':len(transaction),'backup':str(folder),'ok':record['state']=='activated_restart_required','report_path':published.get('report_path') if record['state']=='activated_restart_required' else str(folder/'results.json'),'report_error':None if record['state']!='activated_restart_required' else published.get('report_error')}

def rollback(home,ident):
    folder,record=lab.load(home,ident)
    lab.assert_identity(folder,ident,record)
    if record['state'] not in ('activating','activated_restart_required'): raise ValueError('Nessuna attivazione ripristinabile.')
    if 'transaction' not in record: raise ValueError('Release legacy: ripristina il backup precedente con revisione manuale.')
    with lab.deployment_lock(home): restore(folder,record)
    return {'state':record['state'],'id':ident,'ok':record['state']=='rolled_back','source_root':record.get('source_root'),'restart_required':True,'report_path':str(folder/'results.json')}

def recover(home):
    recovered=[]
    with lab.deployment_lock(home):
        for path in (Path(home)/'repair-lab').glob('*/record.json'):
            record=json.loads(path.read_text(encoding='utf-8'))
            if record['state']=='recovery_conflict': raise ValueError('Ripristino in conflitto: '+record['id'])
            if record['state']=='activating':
                if 'transaction' not in record: raise ValueError('Attivazione legacy incompleta: '+record['id'])
                restore(path.parent,record);recovered.append(record['id'])
    return recovered
