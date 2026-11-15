"""Resolve skill landing pages and validate bounded archives before extraction."""
import io
import re
import stat
import zipfile
import urllib.request
from html.parser import HTMLParser
from pathlib import PurePosixPath
from urllib.parse import urljoin,urlsplit

LIMIT=32*1024*1024
class Links(HTMLParser):
    def __init__(self): super().__init__(); self.links=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='a' and a.get('href'): self.links.append(a['href'])

def fetch(url,normalize,visited=None):
    visited=set() if visited is None else visited
    url=normalize(url)
    if url in visited or len(visited)>=4: raise ValueError('Troppi rimandi nella pagina skill.')
    visited.add(url)
    with urllib.request.urlopen(urllib.request.Request(url,headers={'User-Agent':'freellmapi/3'}),timeout=45) as r:
        final=r.url
        if urlsplit(final).scheme!='https': raise ValueError('Redirect non HTTPS rifiutato.')
        raw=r.read(LIMIT+1)
    if len(raw)>LIMIT: raise ValueError('Pacchetto oltre il limite di download di 32 MiB.')
    if raw.lstrip().lower().startswith((b'<!doctype html',b'<html')):
        parser=Links(); parser.feed(raw.decode('utf-8',errors='replace'))
        candidates=[]
        for link in parser.links:
            target=urljoin(final,link); path=urlsplit(target).path.lower()
            if urlsplit(target).scheme=='https' and (path.endswith('/skill.md') or path.endswith(('.zip','.skill'))):
                candidates.append(target)
        candidates=list(dict.fromkeys(candidates))
        candidates.sort(key=lambda u:(urlsplit(u).hostname!=urlsplit(final).hostname,not urlsplit(u).path.lower().endswith(('.zip','.skill'))))
        if not candidates: raise ValueError('Pagina HTML senza link diretto a SKILL.md o a un pacchetto ZIP/.skill.')
        # Do not run page scripts or shell commands. Follow only explicit download links.
        return fetch(candidates[0],normalize,visited)
    return final,raw

def unpack(raw):
    if not zipfile.is_zipfile(io.BytesIO(raw)):
        body=raw.decode('utf-8-sig')
        if not body.strip() or '\x00' in body or '<html' in body[:1000].lower(): raise ValueError('Contenuto non Markdown valido.')
        if len(raw)>2*1024*1024: raise ValueError('SKILL.md oltre 2 MiB.')
        return {'SKILL.md':raw},body
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        infos=z.infolist()
        if len(infos)>10000 or sum(i.file_size for i in infos)>128*1024*1024: raise ValueError('Archivio troppo grande dopo estrazione.')
        files={}; folded=set()
        for info in infos:
            name=info.filename.replace('\\','/'); p=PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or any(':' in part or part.endswith((' ','.')) for part in p.parts): raise ValueError('Percorso archivio non sicuro: '+name)
            if stat.S_ISLNK(info.external_attr>>16): raise ValueError('Link simbolici non consentiti.')
            if info.is_dir(): continue
            if info.file_size>32*1024*1024: raise ValueError('File troppo grande nel pacchetto.')
            if name.casefold() in folded: raise ValueError('Percorsi duplicati nel pacchetto.')
            folded.add(name.casefold()); files[name]=z.read(info)
        skills=[n for n in files if PurePosixPath(n).name=='SKILL.md']
        if not skills: raise ValueError('Il pacchetto non contiene SKILL.md.')
        depth=min(len(PurePosixPath(n).parts) for n in skills)
        roots=[n for n in skills if len(PurePosixPath(n).parts)==depth]
        if len(roots)!=1: raise ValueError('Pacchetto con più skill principali: scegli un pacchetto specifico.')
        root=PurePosixPath(roots[0]).parent
        selected={str(PurePosixPath(n).relative_to(root)):b for n,b in files.items() if PurePosixPath(n).is_relative_to(root)}
        if len(selected['SKILL.md'])>2*1024*1024: raise ValueError('SKILL.md oltre 2 MiB.')
        return selected,selected['SKILL.md'].decode('utf-8-sig')
