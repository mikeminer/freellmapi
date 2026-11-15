"""Local-user file mailbox for pairing an already running Remote bridge.

No network endpoint, model request, session input or shell execution. The mailbox
lives beside the existing private pairing QR in the user's application data.
"""
import json
import hashlib
import hmac
import http.client
import os
import re
import ssl
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from reliable_runtime import atomic_bytes


def pairing_seconds(value):
    if type(value) is not int or not 120 <= value <= 600:
        raise ValueError('Durata abbinamento: numero intero tra 120 e 600 secondi.')
    return value


def read_small(path,limit=4096):
    with Path(path).open('rb') as stream:
        data = stream.read(limit+1)
    if len(data) > limit:
        raise ValueError('Richiesta locale troppo grande.')
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError('Richiesta locale non valida.')
    return value


class LocalPairing:
    def __init__(self, remote):
        self.remote = remote
        self.instance = remote.instance
        self.root = remote.folder / 'local-pairing'
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.folder = self.root / self.instance
        self.folder.mkdir(mode=0o700)
        self.pointer = remote.folder / 'local-pairing.json'
        self.stop = threading.Event()
        self.worker = threading.Thread(target=self.serve, daemon=True, name='remote-local-pairing')

    def start(self):
        atomic_bytes(self.pointer, json.dumps({'instance': self.instance, 'pid': os.getpid(),
                                             'url': self.remote.url, 'fingerprint': self.remote.fingerprint}).encode())
        self.descriptor=self.remote.folder/'endpoints'/(self.instance+'.json')
        self.descriptor.parent.mkdir(exist_ok=True)
        self.update_session()
        self.worker.start()
        return self

    def update_session(self,session=None):
        if session is None:session=self.remote.snapshot().get('session')
        metadata={'instance':self.instance,'pid':os.getpid(),'url':self.remote.url,'fingerprint':self.remote.fingerprint,'session':session}
        atomic_bytes(self.descriptor,json.dumps(metadata).encode())

    def serve(self):
        while not self.stop.wait(.1):
            for request in list(self.folder.glob('*.request'))[:16]:
                if self.stop.is_set():
                    return
                if not re.fullmatch(r'[a-f0-9]{32}\.request', request.name):
                    continue
                reply = request.with_suffix('.reply')
                claimed = request.with_suffix('.claimed')
                try:
                    request.rename(claimed)
                    body = read_small(claimed,limit=20000)
                    if set(body)=={'seconds'}:
                        seconds = pairing_seconds(body.get('seconds'))
                        pair = self.remote.pair(ttl=seconds)
                        result = {'pair': pair, 'expires_at': time.time() + seconds}
                    elif set(body)=={'operation','device','input'} and body['operation']=='command':
                        device=body['device']
                        if not any(d['id']==device and d['expires']>time.time() for d in self.remote.devices.values()):raise ValueError('Client locale non abbinato.')
                        result={'receipt':self.remote.enqueue(device,body['input'],local=True)}
                    else:raise ValueError('Operazione locale non valida.')
                except FileNotFoundError:
                    continue
                except ValueError:
                    result = {'error': 'Richiesta locale non valida o sessione cambiata; nessuna nuova azione accodata.','not_enqueued':True}
                except OSError:
                    result = {'error': 'Risultato locale non verificato: consulta la ricevuta prima di riprovare.','not_enqueued':False}
                try:
                    atomic_bytes(reply, json.dumps(result).encode())
                except OSError:
                    pass
                finally:
                    claimed.unlink(missing_ok=True)
            # Abandoned replies contain short-lived pairing codes, never device tokens.
            for reply in list(self.folder.glob('*.reply'))[:32]:
                try:
                    if reply.stat().st_mtime < time.time() - 600:
                        reply.unlink()
                except OSError:
                    pass

    def close(self):
        self.stop.set()
        self.worker.join(timeout=2)
        if getattr(self,'descriptor',None):self.descriptor.unlink(missing_ok=True)
        try:
            if read_small(self.pointer).get('instance') == self.instance:
                self.pointer.unlink()
        except (OSError, ValueError):
            pass
        try:
            self.folder.rmdir()
        except OSError:
            pass


def check_instance(metadata):
    """Verify that the network listener is the process receiving local requests.

    Only a public instance nonce is returned; no pair code or auth is sent over
    this probe. For HTTPS the local certificate pin is checked before HTTP.
    """
    parsed = urlsplit(metadata.get('url', ''))
    if parsed.scheme not in ('http', 'https') or not parsed.hostname:
        raise ValueError('Remote locale precedente: riavvia una volta il bridge per aggiornarlo.')
    if parsed.scheme == 'https':
        expected = metadata.get('fingerprint', '')
        if not re.fullmatch(r'[a-f0-9]{64}', expected):
            raise ValueError('Fingerprint Remote locale mancante.')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # Verified against the local pin below, before HTTP.
        connection = http.client.HTTPSConnection(parsed.hostname, parsed.port, timeout=3, context=context)
    else:
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=3)
    try:
        connection.connect()
        if parsed.scheme == 'https':
            actual = hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
            if not hmac.compare_digest(actual, expected):
                raise ValueError('Certificato Remote diverso: nessun codice generato.')
        connection.request('GET', '/v1/state')
        response = connection.getresponse()
        instance = response.getheader('X-Freellmapi-Instance', '')
        response.read(4096)
        if response.status != 401 or instance != metadata.get('instance'):
            raise ValueError('Il server raggiunto non coincide con il processo Remote locale. Possibili istanze duplicate o versione precedente; nessun codice generato.')
    except (OSError, http.client.HTTPException) as exc:
        raise ValueError('Impossibile verificare il server Remote; nessun codice generato.') from exc
    finally:
        connection.close()


def request_pair(home, seconds=120, timeout=8, metadata=None):
    seconds = pairing_seconds(seconds)
    remote = Path(home) / 'remote'
    try:
        metadata = metadata or read_small(remote / 'local-pairing.json')
        instance = metadata.get('instance')
    except (OSError, ValueError) as exc:
        raise ValueError('Remote non attivo o versione precedente: avvia freellmapi --remote-only sul PC.') from exc
    if not isinstance(instance, str) or not re.fullmatch(r'[a-f0-9]{32}', instance):
        raise ValueError('Riferimento Remote locale non valido.')
    folder = remote / 'local-pairing' / instance
    if not folder.is_dir():
        raise ValueError('Remote locale non disponibile.')
    check_instance(metadata)
    ident = uuid.uuid4().hex
    request = folder / (ident + '.request')
    reply = folder / (ident + '.reply')
    atomic_bytes(request, json.dumps({'seconds': seconds}).encode())
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                result = read_small(reply)
            except (FileNotFoundError,PermissionError):
                # Windows scanners can briefly hold a newly replaced reply.
                # Retry this read only, using the same request/nonce.
                time.sleep(.05)
                continue
            if 'error' in result:
                raise ValueError(result['error'])
            if result.get('expires_at', 0) <= time.time():
                raise ValueError('Codice locale già scaduto: ripeti il comando sul PC.')
            return result['pair']
        raise TimeoutError('Remote non risponde al comando locale; nessun messaggio chat o strumento eseguito.')
    finally:
        request.unlink(missing_ok=True)
        reply.unlink(missing_ok=True)
