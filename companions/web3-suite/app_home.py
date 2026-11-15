"""One application-data namespace, including Windows MSIX file redirection.

Resolve the existing main configuration first: resolving individual optional
files can otherwise mix redirected credentials with stale unredirected STOPs.
No files, credentials or environment variables are moved or copied here.
"""
import json,os
from pathlib import Path


def effective_home(home):
    home=Path(home)
    anchor=home/'config.json'
    return anchor.resolve(strict=True).parent if anchor.exists() else home.resolve()


def binding_path():return Path(__file__).resolve().with_name('runtime-home.json')

def runtime_home(home):
    natural=Path(home)
    pointer=binding_path()
    if not pointer.exists():return effective_home(natural)
    try:
        value=json.loads(pointer.read_text(encoding='utf-8'))
        if value.get('version')!=1 or not isinstance(value.get('home'),str) or not isinstance(value.get('redirect_from'),list):raise ValueError()
        target=Path(value['home']);aliases=[Path(p) for p in value['redirect_from']]
        if not target.is_absolute() or not aliases or any(not p.is_absolute() for p in aliases):raise ValueError()
        # An explicitly different APPDATA (e.g. isolated QA) keeps its own data.
        normalize=lambda p:os.path.normcase(str(p.absolute()))
        if normalize(natural) not in {normalize(p) for p in aliases}:return effective_home(natural)
        if not (target/'config.json').is_file():raise ValueError()
        return effective_home(target)
    except (OSError,ValueError,TypeError,AttributeError):
        raise RuntimeError('Archivio freellmapi condiviso non disponibile o collegamento non valido. Avvio fermato: nessun secondo archivio o nuova chiave creati.') from None

def default_home():
    override=os.environ.get('FREELLMAPI_HOME')
    if override:return effective_home(override)
    return runtime_home(Path(os.environ.get('APPDATA') or str(Path.home()))/'freellmapi-cli')
