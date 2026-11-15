"""Send actual pixels transiently; never save base64 to conversation/vault."""
import base64
import io
from pathlib import Path


def attachment(path):
    from PIL import Image
    path=Path(path).resolve()
    if not path.is_file() or path.stat().st_size>20_000_000: raise ValueError('Immagine assente o oltre 20 MB.')
    with Image.open(path) as source:
        if source.width*source.height>40_000_000: raise ValueError('Immagine oltre 40 megapixel.')
        source.thumbnail((1600,1600))
        image=source.convert('RGB'); stream=io.BytesIO(); image.save(stream,format='JPEG',quality=85)
    return {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(stream.getvalue()).decode()}}


def inject(payload,paths):
    # The payload is a copy; persisted user/tool messages only contain local paths.
    payload=dict(payload)
    payload['messages']=list(payload['messages'])+[{'role':'user','content':[
        {'type':'text','text':'Immagini richieste con image_read. Analizza i pixel se supportati; non affermare di aver visto immagini se non puoi leggerle.'},
        *[attachment(p) for p in paths]]}]
    return payload
