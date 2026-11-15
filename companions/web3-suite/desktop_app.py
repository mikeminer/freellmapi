"""FREELLMAPI Desktop: Qt window, local UI, shared coordinator, no Codex dependency."""
import argparse,json,os,sys,threading,time,uuid,wave,hashlib,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent
from app_home import default_home
HOME=default_home();(HOME/'desktop').mkdir(parents=True,exist_ok=True)
if sys.stdout is None:sys.stdout=(HOME/'desktop/app.log').open('a',encoding='utf-8',buffering=1)
if sys.stderr is None:sys.stderr=sys.stdout
from PySide6.QtCore import QObject,Slot,Signal,QUrl,QTimer,QByteArray,QBuffer,QIODevice,Qt
from PySide6.QtWidgets import QApplication,QMainWindow,QFileDialog
from PySide6.QtGui import QIcon,QDesktopServices
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage,QWebEngineSettings,QWebEngineProfile
from PySide6.QtWebChannel import QWebChannel
from concurrent.futures import ThreadPoolExecutor
from desktop_backend import DesktopBackend
from desktop_auth import DesktopAuth,LoginRequired

class LocalPage(QWebEnginePage):
    def acceptNavigationRequest(self,url,kind,main):
        if url.scheme() in ('file','qrc','about'):
            if url.scheme()=='file' and Path(url.toLocalFile()).resolve()!=(ROOT/'desktop/index.html').resolve():return False
            return True
        if kind==QWebEnginePage.NavigationType.NavigationTypeLinkClicked and url.scheme() in ('http','https'):QDesktopServices.openUrl(url)
        return False

class Api(QObject):
    response=Signal(str,str);event=Signal(str);locked=Signal()
    def __init__(self,window,qa=None):
        super().__init__();self.window=window;self.qa_folder=None;self.qa=qa
        # Synthetic UI fixtures must never unlock the real user's backend.
        home=HOME
        if qa is not None and not (os.environ.get('FREELLMAPI_HOME') and HOME.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve())):
            self.qa_folder=tempfile.TemporaryDirectory(prefix='freellmapi-desktop-ui-');home=Path(self.qa_folder.name)
        self.backend=DesktopBackend(home);self.auth=DesktopAuth(home);self.signing_out=threading.Event();self.auth_signature=None
        self.pool=ThreadPoolExecutor(max_workers=4);self.connected=False;self.polling=False;self.recording=None;self.cursor=None;self.closed=False
        self.locked.connect(self.stop_private_media)
        self.timer=QTimer(self);self.timer.timeout.connect(self.poll);self.timer.start(700)
    def reply(self,ident,value=None,error=None):
        if not self.closed:self.response.emit(ident,json.dumps({'ok':error is None,'value':value,'error':str(error) if error else None},ensure_ascii=False))
    def authenticated(self,fresh=False):
        if self.qa is not None:return {'authenticated':True,'state':'signed_in','identity':{'name':'UI test','login':'isolated fixture','provider':'qa-isolated'}}
        return self.auth.status(fresh=fresh)
    def publish_auth(self,value):
        signature=json.dumps(value,ensure_ascii=False)
        if signature!=self.auth_signature and not self.closed:
            self.auth_signature=signature;self.event.emit(json.dumps({'type':'auth','value':value},ensure_ascii=False))
            if not value['authenticated']:self.locked.emit()
    @Slot()
    def stop_private_media(self):
        if self.recording:self.recording[0].stop();self.audio_buffer.close();self.recording=None;self.record_timer.stop()
        if hasattr(self,'tts'):self.tts.stop()
    def detach(self):
        self.connected=False;self.cursor=None
        self.backend.client.token=None;self.backend.client.metadata=None;self.backend.client.last_state={}
    def worker(self,ident,fn,public=False):
        generation=self.auth.generation
        def execute():
            try:
                with self.auth.lock:
                    if not public:
                        if self.signing_out.is_set() or generation!=self.auth.generation:raise LoginRequired('Accesso desktop terminato. Nessuna operazione in coda eseguita.')
                        value=self.authenticated(fresh=True);self.publish_auth(value)
                        if not value['authenticated']:raise LoginRequired(value.get('message') or 'Accedi con Tailscale / Google.')
                    result=fn()
                    if public or (generation==self.auth.generation and not self.signing_out.is_set()):self.reply(ident,result)
                    else:self.reply(ident,error=LoginRequired('Accesso desktop terminato.'))
            except Exception as error:self.reply(ident,error=error)
        self.pool.submit(execute)
    def poll(self):
        if self.polling:return
        self.polling=True
        def work():
            try:
                with self.auth.lock:
                    if self.signing_out.is_set():return
                    access=self.authenticated();self.publish_auth(access)
                    if not access['authenticated']:self.detach();return
                    if self.qa is not None:state=self.qa
                    elif not self.connected:state=self.backend.connect();self.connected=True
                    else:state=self.backend.state()
                    # Logout may be requested while the network read is in flight.
                    if self.signing_out.is_set():return
                    if not self.closed:self.event.emit(json.dumps({'type':'state','value':state},ensure_ascii=False))
                    if self.qa is None and not self.closed:
                        if self.cursor is None or getattr(self,'poll_session',None)!=state.get('session'):self.cursor=state.get('cursor',0)
                        self.poll_session=state.get('session')
                        frame=self.backend.client.request('/v1/events?after='+str(self.cursor));self.cursor=frame['cursor']
                        if frame.get('events') and not self.closed and not self.signing_out.is_set():self.event.emit(json.dumps({'type':'activity','value':frame['events']},ensure_ascii=False))
            except Exception as error:
                self.connected=False
                if not self.closed:self.event.emit(json.dumps({'type':'connection_error','error':str(error)}))
            finally:self.polling=False
        self.pool.submit(work)
    @Slot(str,str,str)
    def rpc(self,ident,method,raw):
        try:
            data=json.loads(raw or '{}')
            if method=='ready':self.auth_signature=None;self.poll();self.reply(ident,{'ready':True});return
            if method=='auth_status':self.worker(ident,lambda:self.authenticated(fresh=True),public=True);return
            if method=='auth_open':self.worker(ident,self.auth.open_tailscale,public=True);return
            if method=='auth_login':
                def login():
                    value=self.auth.login();self.signing_out.clear();self.publish_auth(value);return value
                self.worker(ident,login,public=True);return
            if method=='auth_logout':
                self.signing_out.set()
                self.stop_private_media()
                def logout():
                    value=self.auth.logout();self.detach();self.publish_auth(value);return value
                self.worker(ident,logout,public=True);return
            if method not in ('window','locale'):
                # Guard native UI operations too; workers recheck before execution.
                if self.signing_out.is_set() or (self.qa is None and not (self.auth.cached or {}).get('authenticated')):raise LoginRequired('Accedi con Tailscale / Google.')
            if method=='pick_files':
                paths,_=QFileDialog.getOpenFileNames(self.window,'Allega file al messaggio','', 'Documenti, codice e audio (*.txt *.md *.pdf *.docx *.json *.csv *.py *.js *.ts *.tsx *.html *.css *.java *.kt *.png *.jpg *.jpeg *.webp *.wav *.mp3 *.m4a *.ogg *.flac);;Tutti i file (*)')
                self.worker(ident,lambda:self.backend.add_files(paths));return
            if method=='pick_project':
                path=QFileDialog.getExistingDirectory(self.window,'Scegli il progetto')
                self.worker(ident,lambda:self.backend.command('/project '+path) if path else None);return
            if method=='pick_folder':
                self.reply(ident,QFileDialog.getExistingDirectory(self.window,'Cartella del progetto'));return
            if method=='window':
                action=data.get('action')
                if action=='minimize':self.window.showMinimized()
                elif action=='maximize':self.window.showNormal() if self.window.isMaximized() else self.window.showMaximized()
                elif action=='close':self.window.close()
                self.reply(ident,True);return
            if method=='record':self.record(ident);return
            if method=='speak':
                from PySide6.QtTextToSpeech import QTextToSpeech
                if not hasattr(self,'tts'):self.tts=QTextToSpeech(self)
                self.tts.say(str(data.get('text',''))[:12000]);self.reply(ident,True);return
            if method=='copy':QApplication.clipboard().setText(str(data.get('text','')));self.reply(ident,True);return
            if method=='command':self.worker(ident,lambda:self.backend.command(data.get('text',''),data.get('files',[])));return
            if method=='answer':self.worker(ident,lambda:self.backend.client.answer(data['id'],data.get('answer',''),data.get('cancelled',False)));return
            if method=='check_pending':self.worker(ident,self.backend.client.check_pending);return
            if method=='drop_file':self.worker(ident,lambda:self.backend.drop_file(data['id']));return
            if method=='last_input':self.worker(ident,self.backend.last_input);return
            if method=='connector':self.worker(ident,lambda:self.backend.open_connector(data.get('name')));return
            if method=='library':self.worker(ident,lambda:self.backend.library(data));return
            if method=='snapshot':self.worker(ident,self.backend.state);return
            if method=='workspace_action':self.worker(ident,lambda:self.backend.workspace_action(data.get('area'),data.get('action')));return
            if method=='locale':
                from desktop_locales import load
                self.reply(ident,load(data.get('code')));return
            if method=='cancel':self.worker(ident,lambda:self.backend.client.request('/v1/cancel',{}));return
            raise ValueError('Operazione desktop non disponibile.')
        except Exception as error:self.reply(ident,error=error)
    def record(self,ident):
        if not self.recording:
            from PySide6.QtMultimedia import QMediaDevices,QAudioFormat,QAudioSource
            device=QMediaDevices.defaultAudioInput()
            if device.isNull():raise ValueError('Nessun microfono disponibile sul PC.')
            fmt=QAudioFormat();fmt.setSampleRate(16000);fmt.setChannelCount(1);fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            if not device.isFormatSupported(fmt):fmt=device.preferredFormat()
            self.audio_bytes=QByteArray();self.audio_buffer=QBuffer(self.audio_bytes);self.audio_buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            source=QAudioSource(device,fmt,self);source.start(self.audio_buffer);self.recording=(source,fmt)
            self.record_timer=QTimer(self);self.record_timer.setSingleShot(True);self.record_timer.timeout.connect(self.record_limit);self.record_timer.start(120000)
            self.reply(ident,{'recording':True});return
        self.record_timer.stop()
        source,fmt=self.recording;source.stop();self.audio_buffer.close();self.recording=None
        from PySide6.QtMultimedia import QAudioFormat
        import numpy as np,soundfile as sf
        types={QAudioFormat.SampleFormat.Int16:'<i2',QAudioFormat.SampleFormat.Int32:'<i4',QAudioFormat.SampleFormat.UInt8:'u1',QAudioFormat.SampleFormat.Float:'<f4'}
        audio=np.frombuffer(bytes(self.audio_bytes),dtype=types[fmt.sampleFormat()])
        if not len(audio):raise ValueError('Il microfono non ha restituito audio. Verifica il dispositivo nelle impostazioni Windows.')
        if fmt.sampleFormat()==QAudioFormat.SampleFormat.UInt8:audio=(audio.astype(np.float32)-128)/128
        elif fmt.sampleFormat() in (QAudioFormat.SampleFormat.Int16,QAudioFormat.SampleFormat.Int32):audio=audio.astype(np.float32)/(32768 if fmt.sampleFormat()==QAudioFormat.SampleFormat.Int16 else 2147483648)
        if fmt.channelCount()>1:audio=audio.reshape(-1,fmt.channelCount()).mean(axis=1)
        path=self.backend.home/'desktop'/('audio-'+uuid.uuid4().hex+'.wav');sf.write(path,audio,fmt.sampleRate(),subtype='PCM_16')
        self.worker(ident,lambda:self.backend.transcribe(path))
    def record_limit(self):
        if self.recording:self.event.emit(json.dumps({'type':'record_limit'}))
    def close(self):
        self.closed=True;self.timer.stop()
        if self.recording:self.recording[0].stop()
        self.backend.close();self.pool.shutdown(wait=False,cancel_futures=True)

class Window(QMainWindow):
    def __init__(self,qa=None,open_settings=False):
        super().__init__();self.setWindowTitle('FREELLMAPI Desktop');self.resize(1320,870);self.setMinimumSize(820,580)
        self.setWindowIcon(QIcon(str(ROOT/'desktop/freellmapi-official.ico')))
        self.view=QWebEngineView(self);self.setCentralWidget(self.view)
        self.profile=QWebEngineProfile(self) if qa is not None else QWebEngineProfile('freellmapi-desktop',self)
        if qa is None:
            self.profile.setPersistentStoragePath(str(HOME/'desktop/web-profile'));self.profile.setCachePath(str(HOME/'desktop/web-cache'))
        self.view.setPage(LocalPage(self.profile,self.view))
        self.view.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls,False)
        self.channel=QWebChannel(self.view.page());self.api=Api(self,qa);self.channel.registerObject('bridge',self.api);self.view.page().setWebChannel(self.channel)
        url=QUrl.fromLocalFile(str(ROOT/'desktop/index.html'))
        if open_settings:url.setQuery('settings=1')
        self.view.load(url)
    def closeEvent(self,event):self.api.close();super().closeEvent(event)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--qa-state');parser.add_argument('--snapshot');parser.add_argument('--settings',action='store_true');parser.add_argument('--qa-view',choices=['settings','createProject']);args=parser.parse_args()
    if os.name=='nt':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('freellmapi.agent.desktop')
    app=QApplication(sys.argv);app.setApplicationName('FREELLMAPI Desktop');app.setOrganizationName('FREELLMAPI')
    app.setWindowIcon(QIcon(str(ROOT/'desktop/freellmapi-official.ico')))
    qa=json.loads(Path(args.qa_state).read_text(encoding='utf-8')) if args.qa_state else None
    if qa is None:
        from PySide6.QtNetwork import QLocalServer,QLocalSocket
        from PySide6.QtCore import QLockFile
        server=QLocalServer();key='freellmapi-desktop-'+hashlib.sha256(str(HOME).encode()).hexdigest()[:16]
        # Windows named pipes alone allow multiple listening servers.
        # Probe the previous version, then hold an OS-backed lock for this one.
        socket=QLocalSocket();socket.connectToServer(key)
        if socket.waitForConnected(350):socket.disconnectFromServer();return 0
        instance_lock=QLockFile(str(HOME/'desktop/window.lock'));instance_lock.setStaleLockTime(0)
        if not instance_lock.tryLock(0):
            socket.abort();socket.connectToServer(key)
            if socket.waitForConnected(1500):socket.disconnectFromServer();return 0
            raise RuntimeError('FREELLMAPI Desktop è già in avvio; nessuna seconda finestra creata.')
        QLocalServer.removeServer(key)
        if not server.listen(key):raise RuntimeError('Istanza desktop locale non disponibile.')
    window=Window(qa,args.settings);window.show()
    if qa is None:
        window.instance_server=server
        window.instance_lock=instance_lock
        def raise_existing():
            socket=server.nextPendingConnection()
            if socket:socket.disconnectFromServer();socket.deleteLater()
            window.showNormal();window.raise_();window.activateWindow()
        server.newConnection.connect(raise_existing)
    if args.qa_view and qa:window.view.loadFinished.connect(lambda ok:QTimer.singleShot(900,lambda:window.view.page().runJavaScript(args.qa_view+'()')))
    if args.snapshot:
        def capture():window.grab().save(args.snapshot);window.close();app.quit()
        window.view.loadFinished.connect(lambda ok:QTimer.singleShot(1600,capture))
    return app.exec()

if __name__=='__main__':sys.exit(main())
