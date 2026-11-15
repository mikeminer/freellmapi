"""Attach to the verified existing writer; never start a second session agent."""
import re
from pathlib import Path
from urllib.parse import urlsplit
from remote_pairing import read_small,check_instance

def owner(home,session,current=None):
    if not isinstance(session,str) or not re.fullmatch(r'[a-f0-9]{32}',session):raise ValueError('ID sessione non valido.')
    candidates=[]
    for path in (Path(home)/'remote/endpoints').glob('*.json'):
        try:
            meta=read_small(path)
            if meta.get('session')!=session or meta.get('instance')==current:continue
            check_instance(meta);candidates.append(meta)
        except (OSError,ValueError):continue
    if len(candidates)>1:raise ValueError('Più coordinatori dichiarano la stessa sessione: nessuna esecuzione o trasferimento automatico.')
    return candidates[0] if candidates else None

def public(meta):return {k:meta.get(k,'') for k in ('url','fingerprint','instance','session')}

def same_host(current,target):
    before=urlsplit(current);after=urlsplit(target)
    return before.scheme==after.scheme and before.hostname==after.hostname and not after.username and not after.query and not after.fragment and after.path in ('','/')
