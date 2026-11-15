"""Build the branded Windows installer from an already prepared private runtime.

This does not package the developer's Python, APPDATA, wallets or conversations.
The full router/Tailscale provisioning wizard is a separate packaging step.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
ICON = ROOT/'desktop/freellmapi-official.ico'
REQUIRED = ('runtime/python/python.exe', 'runtime/python/pythonw.exe',
            'runtime/python/python313.dll', 'agent/desktop_app.py',
            'agent/runtime_bootstrap.py', 'agent/freellmapi.py',
            'agent/desktop/index.html', 'agent/desktop/freellmapi-official.ico')


def icon_sizes(path):
    raw = Path(path).read_bytes()
    if len(raw) < 6:
        raise ValueError('Icona ICO non valida.')
    reserved, kind, count = struct.unpack_from('<HHH', raw)
    if reserved or kind != 1 or not count or len(raw) < 6 + count*16:
        raise ValueError('Icona ICO non valida.')
    sizes = set()
    for i in range(count):
        width, height, _, _, _, _, length, offset = struct.unpack_from('<BBBBHHII', raw, 6+i*16)
        if not length or offset < 6 + count*16 or offset + length > len(raw):
            raise ValueError('Risorsa ICO incompleta.')
        sizes.add((width or 256, height or 256))
    return sizes


def validate_payload(payload, *, probe=True):
    payload = Path(payload).resolve(strict=True)
    missing = [name for name in REQUIRED if not (payload/name).is_file()]
    if missing:
        raise ValueError('Payload incompleto: '+', '.join(missing))
    for file in payload.rglob('*'):
        if file.is_symlink() or (hasattr(file, 'is_junction') and file.is_junction()):
            raise ValueError('Il payload deve contenere file reali, non link: '+str(file.relative_to(payload)))
        if not file.is_file():
            continue
        if (file.suffix.casefold() == '.dpapi' or file.name.casefold() in
                ('runtime-home.json', '.env', 'freeapi.db', 'devices.json') or
                file.relative_to(payload).as_posix().casefold()=='agent/config.json'):
            raise ValueError('Dati personali o configurazione locale nel payload: '+str(file.relative_to(payload)))
    sizes = icon_sizes(ICON)
    if not {(16,16), (32,32), (48,48), (256,256)} <= sizes:
        raise ValueError('Mancano dimensioni ICO richieste per Windows.')
    if (payload/'agent/desktop/freellmapi-official.ico').read_bytes() != ICON.read_bytes():
        raise ValueError('Il payload deve usare la stessa icona ufficiale del setup.')
    if probe:
        runtime = payload/'runtime/python'
        code = ("import sys,pathlib; "
                "assert pathlib.Path(sys.prefix).resolve()==pathlib.Path(sys.argv[1]).resolve(), 'Python esterno'; "
                "sys.path.insert(0,sys.argv[2]); "
                "sys.path.insert(0,sys.argv[3]); import suite; suite.configure(pathlib.Path(sys.argv[3]).parent); "
                "import freellmapi, desktop_backend, win32api; "
                "from PySide6 import QtWebEngineWidgets; print('private-runtime-ok')")
        result = subprocess.run([str(runtime/'python.exe'), '-I', '-X', 'utf8', '-c', code,
                                 str(runtime), str(payload/'agent'),str(payload/'launcher')], cwd=payload,
                                capture_output=True, text=True, encoding='utf-8', timeout=60)
        if result.returncode or 'private-runtime-ok' not in result.stdout:
            raise ValueError('Il runtime Python privato non supera la verifica delle dipendenze.')
    return {'payload':str(payload), 'icon_sizes':sorted(sizes), 'runtime_probe':probe}


def compiler(explicit=None):
    if explicit:
        path=Path(explicit).resolve(strict=True)
        if not path.is_file():raise ValueError('ISCC deve essere un file.')
        return path
    found=shutil.which('ISCC.exe')
    if found:return Path(found)
    for root in ('ProgramFiles(x86)', 'ProgramFiles'):
        for version in ('7','6'):
            candidate=Path(os.environ.get(root, 'C:/Program Files'))/('Inno Setup '+version)/'ISCC.exe'
            if candidate.is_file():return candidate
    raise ValueError('Compilatore Inno Setup non trovato. Specifica --iscc.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload',required=True,type=Path)
    parser.add_argument('--version',default='0.1.0')
    parser.add_argument('--iscc',type=Path)
    parser.add_argument('--check',action='store_true',help='Verifica runtime e branding senza compilare.')
    args=parser.parse_args()
    if not re.fullmatch(r'\d+\.\d+\.\d+(?:\.\d+)?',args.version):
        parser.error('Versione richiesta: x.y.z oppure x.y.z.w')
    try:
        checked=validate_payload(args.payload)
        if args.check:
            print(json.dumps(checked));return
        subprocess.run([str(compiler(args.iscc)), '/DPayloadRoot='+checked['payload'],
                        '/DSuiteVersion='+args.version, str(HERE/'FREELLMAPI-Suite.iss')],
                       cwd=HERE,check=True)
    except (OSError,ValueError,subprocess.SubprocessError) as error:
        parser.exit(1,'Build fermata: '+str(error)+'\n')


if __name__=='__main__':main()
