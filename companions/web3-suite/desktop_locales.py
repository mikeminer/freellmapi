"""Local, allowlisted UI dictionaries. No provider request on language change."""
import json
from pathlib import Path
FOLDER=Path(__file__).resolve().parent/'desktop/locales'
def load(code=None):
    manifest=json.loads((FOLDER/'manifest.json').read_text(encoding='utf-8'))
    if code is None:return manifest
    if code not in manifest['locales']:raise ValueError('Lingua non supportata.')
    return {'code':code,'rtl':code in manifest['rtl'],'strings':json.loads((FOLDER/(code+'.json')).read_text(encoding='utf-8'))}
