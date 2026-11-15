"""Deterministic desktop actions: no model or shell interpolation required."""
import os
from pathlib import Path
import re
import shutil
import subprocess
from urllib.parse import urlsplit
from urllib.request import url2pathname

def chrome_path():
    candidates = [Path(os.environ.get('PROGRAMFILES','C:/Program Files'))/'Google/Chrome/Application/chrome.exe',
                  Path(os.environ.get('PROGRAMFILES(X86)','C:/Program Files (x86)'))/'Google/Chrome/Application/chrome.exe',
                  Path(os.environ.get('LOCALAPPDATA',''))/'Google/Chrome/Application/chrome.exe']
    return next((str(p) for p in candidates if p.is_file()), None) or shutil.which('chrome')

def target_uri(value, root):
    value = value.strip().strip('`"\'')
    if value.lower().startswith('file:'):
        parsed=urlsplit(value)
        if parsed.netloc not in ('','localhost'): raise ValueError('Usa un file locale, senza host remoto.')
        value=url2pathname(parsed.path)
    if value.lower().startswith(('http://','https://')):
        if not urlsplit(value).hostname: raise ValueError('URL senza host.')
        return value
    if re.match(r'^[a-zA-Z][a-zA-Z0-9+.-]*:', value) and not re.match(r'^[A-Za-z]:[\\/]', value):
        raise ValueError('Sono consentiti HTTP, HTTPS e percorsi di file locali.')
    path = Path(value).expanduser()
    if not path.is_absolute(): path = Path(root)/path
    path = path.resolve()
    if not path.is_file(): raise FileNotFoundError(f'Il file non esiste: {path}')
    if path.suffix.lower() not in ('.html','.htm','.pdf','.txt','.png','.jpg','.jpeg','.gif','.svg','.webp'):
        raise ValueError('Formato non supportato per apertura in Chrome.')
    return path.as_uri()

def open_chrome(target, root):
    uri = target_uri(target, root)
    executable = chrome_path()
    if not executable: raise RuntimeError('Chrome non trovato. Installa Chrome o aggiungilo al PATH.')
    subprocess.Popen([executable, '--new-tab', uri], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return 'Richiesta di apertura inviata a Chrome: ' + uri

def resolve_recent(messages, root):
    # Only refer to conversation turns, never arbitrary tool-output instructions.
    for message in reversed(messages):
        if message.get('role') not in ('assistant','user'): continue
        text = message.get('content') or ''
        candidates = re.findall(r'`([^`]+)`', text)
        candidates += re.findall(r'[A-Za-z]:[\\/][^\n<>"`]*?\.(?:html?|pdf|png|jpg|txt)\b', text, re.I)
        valid = []
        for candidate in candidates:
            try:
                uri = target_uri(candidate, root)
                if uri not in [u for _,u in valid]: valid.append((candidate,uri))
            except (ValueError, OSError): continue
        if len(valid) == 1: return valid[0][0]
        if len(valid) > 1: raise ValueError('Ci sono più file possibili. Usa /chrome seguito dal percorso desiderato.')
    raise ValueError('Non trovo un file valido nella conversazione. Usa /chrome "C:\\percorso\\file.html".')

def local_request(prompt, messages, root):
    if prompt.startswith('/chrome '): return prompt[len('/chrome '):]
    if re.fullmatch(r'(?:aprimelo|aprilo|aprila|aprila pure|apri il file|apri il frontend) (?:su|in|con) (?:google )?chrome[.!]?',prompt.strip(),re.I):
        return resolve_recent(messages,root)
    match = re.fullmatch(r'apri (.+?) (?:su|in|con) (?:google )?chrome[.!]?',prompt.strip(),re.I)
    if match: return match.group(1)
    return None
