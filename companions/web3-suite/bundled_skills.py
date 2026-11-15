"""Offline suite skills: verified files, managed links, no package execution."""
import hashlib,json,os,re
from pathlib import Path,PurePosixPath
from reliable_runtime import atomic_bytes

MANAGED_BY='freellmapi-suite-builtin-v1'

def suite_root(explicit=None):
    value=explicit or os.environ.get('FREELLMAPI_SUITE_ROOT')
    root=Path(value).resolve() if value else Path(__file__).resolve().parent.parent
    return root if (root/'bundled-skills.json').is_file() else None

def relative(root,value):
    if not isinstance(value,str) or not value or '\\' in value or ':' in value:
        raise ValueError('invalid_bundled_skill_path')
    parts=PurePosixPath(value)
    if parts.is_absolute() or '..' in parts.parts:raise ValueError('invalid_bundled_skill_path')
    target=(root/parts).resolve()
    if not target.is_relative_to(root.resolve()):raise ValueError('bundled_skill_path_escape')
    return target

def validate(root):
    root=Path(root).resolve();manifest=json.loads((root/'bundled-skills.json').read_text(encoding='utf-8'))
    if manifest.get('schema_version')!=1 or not isinstance(manifest.get('packages'),list):raise ValueError('invalid_bundled_skill_manifest')
    identifiers=set()
    for package in manifest['packages']:
        ident=package['id']
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,59}',ident) or ident in identifiers:raise ValueError('invalid_bundled_skill_id')
        identifiers.add(ident);folder=relative(root,package['path'])
        files=package['files'];seen=set()
        if not isinstance(files,dict) or 'SKILL.md' not in files:raise ValueError('bundled_skill_incomplete')
        actual=set()
        for current,dirs,names in os.walk(folder):
            for name in dirs+names:
                p=Path(current)/name
                if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):raise ValueError('bundled_skill_link_not_allowed')
            actual.update((Path(current)/name).relative_to(folder).as_posix() for name in names)
        if actual!=set(files):raise ValueError('bundled_skill_file_set_mismatch: '+ident)
        for name,row in files.items():
            if name.casefold() in seen:raise ValueError('bundled_skill_case_collision')
            seen.add(name.casefold());path=relative(folder,name);raw=path.read_bytes()
            if len(raw)!=row['size'] or hashlib.sha256(raw).hexdigest()!=row['sha256']:
                raise ValueError('bundled_skill_hash_mismatch: '+ident+'/'+name)
        for notice in package.get('notices',[]):
            if hashlib.sha256(relative(root,notice['path']).read_bytes()).hexdigest()!=notice['sha256']:
                raise ValueError('bundled_skill_license_mismatch')
    return manifest

def registered_name(ident):
    return ident+'-'+hashlib.sha256(('freellmapi-suite:'+ident).encode()).hexdigest()[:8]

def register(home,root=None):
    root=suite_root(root)
    if root is None:return []
    manifest=validate(root);home=Path(home);result=[];skill_home=(home/'skills').resolve()
    for package in manifest['packages']:
        name=registered_name(package['id']);target=skill_home/name/'source.json';old={}
        if not target.resolve().is_relative_to(skill_home):raise ValueError('bundled_skill_registry_path_escape')
        if target.exists():
            old=json.loads(target.read_text(encoding='utf-8'))
            if old.get('managed_by')!=MANAGED_BY:
                # An explicitly user-owned row is never overwritten.
                result.append({'id':package['id'],'registered':False,'code':'user_metadata_collision'});continue
        value={'name':name,'description':package['description'],'local_path':str(relative(root,package['path'])),
               'built_in':True,'managed_by':MANAGED_BY,'bundle_id':package['id'],'suite_root':str(root),
               'version':package['version'],'source_commit':package['source_commit'],
               'files':len(package['files']),'tree_sha':package['tree_sha']}
        replacement=old.get('superseded_by','')
        if re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}',replacement) and (skill_home/replacement/'source.json').is_file():
            value['superseded_by']=replacement
        if not target.exists() or json.loads(target.read_text(encoding='utf-8'))!=value:
            target.parent.mkdir(parents=True,exist_ok=True)
            atomic_bytes(target,json.dumps(value,ensure_ascii=False,indent=2).encode('utf-8'))
        result.append({'id':package['id'],'name':name,'registered':True,'version':package['version'],'files':len(package['files'])})
    return result

def unregister(home,root):
    removed=[];root=Path(root).resolve();skill_home=(Path(home)/'skills').resolve()
    for path in skill_home.glob('*/source.json'):
        if path.is_symlink() or not path.resolve().is_relative_to(skill_home):continue
        value=json.loads(path.read_text(encoding='utf-8'))
        if value.get('managed_by')!=MANAGED_BY or Path(value.get('suite_root','')).resolve()!=root:continue
        path.unlink();removed.append(value['name'])
        try:path.parent.rmdir()
        except OSError:pass # Preserve user-added files; never recursively delete skill data.
    return removed
