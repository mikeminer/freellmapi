"""Bounded project traversal. Never follow Windows junctions or symlinks."""
import os
from pathlib import Path
import time


class ScanLimit(RuntimeError): pass


def files(root,ignore,seconds=5,max_entries=20000):
    deadline=time.monotonic()+seconds; visited=0; pending=[Path(root)]
    while pending:
        directory=pending.pop()
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    visited+=1
                    if visited>max_entries or time.monotonic()>=deadline:
                        raise ScanLimit('Scansione parziale: limite di tempo o elementi raggiunto. Restringi path a una sottocartella del progetto.')
                    if entry.is_symlink() or (hasattr(entry,'is_junction') and entry.is_junction()): continue
                    # DirEntry.is_junction is not available on every supported Python.
                    if getattr(entry.stat(follow_symlinks=False),'st_file_attributes',0)&0x400: continue
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name.casefold() not in ignore: pending.append(Path(entry.path))
                    elif entry.is_file(follow_symlinks=False): yield Path(entry.path)
        except OSError:
            continue


def listing(root,base,pattern,ignore,max_chars=24000):
    lines=[]; size=0
    try:
        for path in files(base,ignore):
            if not path.match(pattern): continue
            line=str(path.relative_to(root)); size+=len(line)+1
            if size>max_chars:
                lines.append('[Elenco parziale: limite output raggiunto. Restringi path o pattern.]'); break
            lines.append(line)
    except ScanLimit as exc: lines.append('['+str(exc)+']')
    return '\n'.join(lines) or 'Nessun file corrispondente nelle cartelle accessibili.'


def search(root,base,text,ignore,seconds=5,max_chars=24000):
    found=[]; size=0; deadline=time.monotonic()+seconds
    try:
        for path in files(base,ignore,seconds=seconds):
            if time.monotonic()>=deadline: raise ScanLimit('Ricerca parziale: tempo esaurito. Restringi path.')
            try:
                if path.stat().st_size>1_000_000: continue
                for n,line in enumerate(path.read_text(encoding='utf-8').splitlines(),1):
                    if time.monotonic()>=deadline: raise ScanLimit('Ricerca parziale: tempo esaurito. Restringi path.')
                    if text.casefold() not in line.casefold(): continue
                    row=f'{path.relative_to(root)}:{n}: {line[:500]}'
                    if len(found)>=150 or size+len(row)>max_chars: raise ScanLimit('Ricerca parziale: limite risultati raggiunto. Restringi path o testo.')
                    found.append(row); size+=len(row)+1
            except (UnicodeError,OSError): continue
    except ScanLimit as exc: found.append('['+str(exc)+']')
    return '\n'.join(found) or 'Nessun risultato nelle cartelle accessibili.'
