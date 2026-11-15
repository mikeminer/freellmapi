"""Open a local, self-contained pairing page in the system default browser."""
import base64,html,json,time,webbrowser
from pathlib import Path
from reliable_runtime import atomic_bytes

def open_pair(pair):
    qr=Path(pair['qr']).resolve(strict=True)
    if qr.stat().st_size>2_000_000:raise ValueError('QR locale troppo grande')
    data=qr.read_bytes()
    if not data.startswith(b'\x89PNG\r\n\x1a\n'):raise ValueError('Il QR locale non è PNG')
    expires=max(1,min(600,int(pair['expires_seconds'])))
    esc=lambda x:html.escape(str(x),quote=True)
    page='''<!doctype html><html lang="it"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; script-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>freellmapi Remote · QR di abbinamento</title><style>
*{box-sizing:border-box}body{margin:0;background:#0b101c;color:#e6edff;font:17px system-ui;display:grid;place-items:center;min-height:100vh;padding:24px}
main{background:#141e30;max-width:560px;width:100%;padding:32px;border:1px solid #2c4164;border-radius:24px;text-align:center}
h1{font-size:27px;margin:0 0 10px}p{line-height:1.5;color:#b8c7e5}img{width:min(100%,320px);image-rendering:pixelated;border:16px solid white;border-radius:14px}
code{display:block;font-size:30px;letter-spacing:.15em;margin:16px}small{display:block;overflow-wrap:anywhere;color:#b8c7e5}#ttl{color:#79dfbd}.expired img,.expired code{display:none}</style>
<main><h1>✦ freellmapi Remote</h1><p>Apri FREELLMAPI sul telefono e scansiona il QR per abbinare questo PC.</p>'''
    page+='<img alt="QR di abbinamento freellmapi" src="data:image/png;base64,'+base64.b64encode(data).decode()+'">'
    page+='<code>'+esc(pair['code'])+'</code><small>'+esc(pair['url'])+'</small><p id="ttl"></p>'
    page+='<p>Il codice è monouso. Se il telefono è già abbinato, usa il collegamento salvato nell’app. Questa pagina è locale sul PC.</p></main>'
    page+='<script>const end=Date.now()+'+json.dumps(expires*1000)+''';function tick(){const s=Math.max(0,Math.ceil((end-Date.now())/1000));document.getElementById('ttl').textContent=s?'Codice valido per '+s+' secondi':'Codice scaduto: genera un nuovo abbinamento dal CMD.';if(!s)document.body.classList.add('expired')}tick();setInterval(tick,1000)</script></html>'''
    target=qr.with_name('pair.html');atomic_bytes(target,page.encode('utf-8'))
    return {'page':str(target),'opened':bool(webbrowser.open(target.as_uri(),new=1,autoraise=True))}
