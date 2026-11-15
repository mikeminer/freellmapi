"""Install standalone Markdown skills; never execute downloaded code."""
import hashlib
import json
from pathlib import Path
import re
import urllib.request
import uuid
import shutil
import os
from skill_download import fetch,unpack
from urllib.parse import urlsplit, urlunsplit

def source_url(url):
    if "://" not in url: url="https://"+url
    p=urlsplit(url)
    if p.scheme!='https' or not p.hostname or p.username or p.password:
        raise ValueError('Usa un URL HTTPS pubblico senza credenziali.')
    if p.hostname=='github.com':
        bits=p.path.split('/')
        if len(bits)>5 and bits[3]=='blob':
            return urlunsplit(('https','raw.githubusercontent.com','/'.join(bits[1:3]+bits[4:]),'',''))
        raise ValueError('Indica il link al file SKILL.md, non la pagina del repository.')
    return url

class Skills:
    def __init__(self,home): self.home=Path(home)/'skills'
    def contains(self,path):
        roots=[self.home.resolve()]
        # Windows packaged apps can redirect rename operations into their per-app
        # Roaming store. Accept only the corresponding dedicated skills directory.
        standard=Path.home()/'AppData/Roaming/freellmapi-cli/skills'
        if self.home.absolute()==standard.absolute():
            roots.append((Path.home()/'AppData/Local/Packages/OpenAI.Codex_2p2nqsd0c76g0/LocalCache/Roaming/freellmapi-cli/skills').resolve())
        return any(path.resolve().is_relative_to(root) for root in roots)
    def path(self,name):
        if not re.fullmatch(r'[a-z0-9][a-z0-9_-]{0,79}',name): raise ValueError('Nome skill non valido.')
        p=(self.home/name).resolve()
        if not self.contains(p): raise ValueError('Percorso skill non valido.')
        return p
    def list(self):
        result=[]
        for p in sorted(self.home.glob('*/source.json')):
            if p.is_symlink() or not self.contains(p): continue
            try:
                item=json.loads(p.read_text(encoding='utf-8'))
                if not item.get('superseded_by'): result.append(item)
            except (ValueError,OSError): continue
        return result
    def read(self,name):
        p=self.root(name)/'SKILL.md'
        if p.is_symlink(): raise ValueError('Link simbolici non supportati.')
        return p.read_text(encoding='utf-8')
    def root(self,name):
        p=self.path(name)
        metadata=p/'source.json'
        if metadata.exists():
            source=json.loads(metadata.read_text(encoding='utf-8'))
            if source.get('local_path'): return Path(source['local_path']).resolve()
        return p
    def read_file(self,name,relative,offset=0):
        root=self.root(name); p=(root/relative).resolve()
        if not p.is_relative_to(root) or not p.is_file(): raise ValueError('File fuori dal pacchetto skill o inesistente.')
        if p.stat().st_size>2_000_000: raise ValueError('File troppo grande.')
        text=p.read_text(encoding='utf-8')
        return text[max(0,offset):max(0,offset)+24000]
    def discover(self,query):
        roots=[Path.home()/'.codex/skills',Path.home()/'.agents/skills',self.home]
        words=query.lower().replace('-',' ').split(); found=[]
        for root in roots:
            for p in root.glob('*/SKILL.md'):
                if p.is_symlink(): continue
                text=p.read_text(encoding='utf-8')[:2000]
                haystack=(p.parent.name+' '+text).lower().replace('-',' ')
                if all(w in haystack for w in words): found.append({'name':p.parent.name,'path':str(p),'preview':text[:600]})
        return found[:30]
    def link(self,path,approve):
        root=Path(path).expanduser().resolve()
        if root.is_file(): root=root.parent
        skill=root/'SKILL.md'
        if not skill.is_file(): raise ValueError('La cartella deve contenere SKILL.md.')
        text=skill.read_text(encoding='utf-8')
        name=re.sub('[^a-z0-9_-]+','-',root.name.lower())[:60] or 'local-skill'
        name+='-'+hashlib.sha256(str(root).encode()).hexdigest()[:8]
        if not approve(f'Collegare la skill locale {root}?\nInclude accesso in lettura ai file di supporto.\n'+text): return 'Collegamento annullato.'
        target=self.path(name); target.mkdir(parents=True,exist_ok=True)
        match=re.search(r'^description:\s*(.+)',text,re.M)
        (target/'source.json').write_text(json.dumps({'name':name,'description':match.group(1)[:500] if match else root.name,'local_path':str(root)},indent=2),encoding='utf-8')
        return name
    def install(self,url,approve):
        url=source_url(url)
        resolved,raw=fetch(url,source_url)
        files,body=unpack(raw)
        name_match=re.search(r'^name:\s*[\"\']?([^\n\"\']+)',body,re.M)
        name=re.sub(r'[^a-z0-9_-]+','-',(name_match.group(1) if name_match else 'skill').strip().lower()).strip('-')[:60] or 'skill'
        name+='-'+hashlib.sha256(url.encode()).hexdigest()[:8]
        description_match=re.search(r'^description:\s*(.+)',body,re.M)
        description=description_match.group(1).strip() if description_match else 'Skill Markdown installata da URL'
        preview=body[:12000]
        if len(body)>12000: preview+='\n[Anteprima abbreviata; il documento completo sarà salvato e leggibile a pagine.]'
        if not approve(f'Installare {name} da {resolved}?\n{len(files)} file, {len(raw)} byte scaricati. Nessuno script verrà eseguito.\n\n'+preview): return 'Installazione annullata.'
        self.home.mkdir(parents=True,exist_ok=True)
        stage=self.home/('.staging-'+uuid.uuid4().hex); stage.mkdir()
        path=self.path(name); backup=None
        try:
            for relative,content in files.items():
                target=stage/relative; target.parent.mkdir(parents=True,exist_ok=True); target.write_bytes(content)
            metadata={'name':name,'description':description[:500],'url':url,'resolved_url':resolved,'sha256':hashlib.sha256(raw).hexdigest(),'files':len(files)}
            (stage/'source.json').write_text(json.dumps(metadata,indent=2),encoding='utf-8')
            if path.exists():
                backup=self.home/'.backups'/(name+'-'+uuid.uuid4().hex)
                backup.parent.mkdir(exist_ok=True); path.rename(backup)
            try: stage.rename(path)
            except BaseException:
                if backup is not None: backup.rename(path)
                raise
            base=name.rsplit('-',1)[0]
            for previous in self.list():
                if previous['name']!=name and previous['name'].rsplit('-',1)[0]==base:
                    previous['superseded_by']=name
                    (self.path(previous['name'])/'source.json').write_text(json.dumps(previous,indent=2),encoding='utf-8')
        finally:
            if stage.exists(): shutil.rmtree(stage)
        return 'Skill installata: '+name+f'\nFile installati: {len(files)}. Fonte: {resolved}'
