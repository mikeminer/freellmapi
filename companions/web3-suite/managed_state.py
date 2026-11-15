"""Versioned JSON state owned by the maintenance runtime; declarative migrations."""
import json,re,shutil
from pathlib import Path
import repair_lab as lab

def path(home,name):
    if not re.fullmatch(r'[a-z][a-z0-9_-]{1,50}',name): raise ValueError('Nome stato non valido.')
    return Path(home)/'managed-state'/(name+'.json')

def register(home,name,value):
    target=path(home,name)
    if target.exists(): raise ValueError('Stato già registrato; usa una migrazione.')
    if not isinstance(value,dict): raise ValueError('Lo stato iniziale deve essere un oggetto JSON.')
    target.parent.mkdir(parents=True,exist_ok=True);lab.store(target,{'schema_version':1,'data':value})
    return {'name':name,'path':str(target),'schema_version':1}

def prepare(home,ident,name,to_version,operations):
    folder,record=lab.load(home,ident)
    if record['state'] not in ('staged','edited','failed','tested'): raise ValueError('Release non modificabile.')
    source=path(home,name);old=json.loads(source.read_text(encoding='utf-8'))
    if type(to_version)!=int or to_version!=old['schema_version']+1: raise ValueError('La versione deve avanzare di uno.')
    if not isinstance(operations,list) or not 1<=len(operations)<=50: raise ValueError('Servono 1..50 operazioni dichiarative.')
    value=json.loads(json.dumps(old['data']))
    for op in operations:
        key=op.get('key');kind=op.get('op')
        if not isinstance(key,str) or not key: raise ValueError('Chiave richiesta.')
        if kind=='rename':
            dest=op.get('to')
            if key not in value or not isinstance(dest,str) or not dest or dest in value: raise ValueError('Rename ambiguo o chiave mancante.')
            value[dest]=value.pop(key)
        elif kind=='default': value.setdefault(key,op['value'])
        elif kind=='remove':
            if key not in value: raise ValueError('Chiave da rimuovere assente.')
            del value[key]
        elif kind=='set': value[key]=op['value']
        else: raise ValueError('Operazioni: rename, default, set, remove.')
    stage=folder/'state-stage';baseline=folder/'state-original'
    stage.mkdir(exist_ok=True);baseline.mkdir(exist_ok=True)
    shutil.copy2(source,baseline/source.name)
    lab.store(stage/source.name,{'schema_version':to_version,'data':value})
    item={'name':name,'before':lab.digest(source),'after':lab.digest(stage/source.name),'from_version':old['schema_version'],'to_version':to_version,'operations':operations}
    record.setdefault('migrations',{})[name]=item;record['state']='edited';record.pop('tested_manifest',None)
    lab.store(folder/'record.json',record);return item

def check_plan(home,folder,record):
    for name,item in record.get('migrations',{}).items():
        if lab.digest(path(home,name))!=item['before']: raise ValueError('Stato vivo cambiato durante la preparazione.')
        if lab.digest(folder/'state-original'/(name+'.json'))!=item['before'] or lab.digest(folder/'state-stage'/(name+'.json'))!=item['after']: raise ValueError('Migrazione modificata dopo preparazione.')

def seed_test_home(folder,record,baseline=False):
    target=folder/'test-home'/'freellmapi-cli'/'managed-state';target.mkdir(parents=True,exist_ok=True)
    for name in record.get('migrations',{}): shutil.copy2(folder/('state-original' if baseline else 'state-stage')/(name+'.json'),target/(name+'.json'))
