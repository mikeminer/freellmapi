"""First-run setup for a personal installation. Never configures trading rights."""
import json,os,subprocess,sys,urllib.error,urllib.request
from pathlib import Path


def save_settings(home,base,key,vault):
    from urllib.parse import urlsplit
    from reliable_runtime import atomic_bytes
    from hl_credentials import transform
    home=Path(home);base=base.strip().rstrip('/').removesuffix('/v1')
    target=urlsplit(base)
    if target.scheme not in ('http','https') or not target.hostname or target.username or target.password:
        raise ValueError('Enter an HTTP(S) API base URL without embedded credentials.')
    path=home/'config.json'
    value=json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    if not key and not (home/'key.dpapi').exists():raise ValueError('Paste your unified API key from the official router dashboard.')
    if not str(vault).strip():raise ValueError('Choose a memory vault directory.')
    # Read the existing key only for the explicitly requested API check, never
    # display it or replace it with a new token during an update.
    if key:token=key
    else:token=transform(bytes.fromhex((home/'key.dpapi').read_text().strip()),False).decode('utf-16-le')
    request=urllib.request.Request(base+'/v1/models?execution_status=ready',headers={'Authorization':'Bearer '+token})
    try:
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({})) if target.hostname in ('127.0.0.1','localhost','::1') else urllib.request.build_opener()
        with opener.open(request,timeout=15) as response:catalog=json.loads(response.read(4_000_000))
        if not isinstance(catalog.get('data'),list):raise ValueError('The server did not return an OpenAI-compatible model catalog.')
    except (OSError,ValueError) as error:
        raise ValueError('API check failed. Start the official router and verify the URL and unified key. Existing settings were kept.') from None
    home.mkdir(parents=True,exist_ok=True)
    vault_path=Path(vault).expanduser().resolve();vault_path.mkdir(parents=True,exist_ok=True)
    value.update(base_url=base,model=value.get('model','auto:reliable'),vault_path=str(vault_path))
    if key:atomic_bytes(home/'key.dpapi',transform(key.encode('utf-16-le'),True).hex().encode('ascii'))
    atomic_bytes(path,json.dumps(value,indent=2).encode())
    return len(catalog['data'])


def wizard(root,home):
    from PySide6.QtWidgets import QApplication,QDialog,QVBoxLayout,QLabel,QLineEdit,QPushButton,QFileDialog,QMessageBox,QHBoxLayout
    from PySide6.QtGui import QIcon,QDesktopServices
    from PySide6.QtCore import QUrl
    from concurrent.futures import ThreadPoolExecutor
    from PySide6.QtCore import QObject,Signal
    root=Path(root);home=Path(home)
    app=QApplication.instance() or QApplication(sys.argv)
    app.setWindowIcon(QIcon(str(root/'assets/freellmapi.ico')))
    dialog=QDialog();dialog.setWindowTitle('FREELLMAPI Suite · Setup');dialog.resize(620,500)
    layout=QVBoxLayout(dialog)
    intro=QLabel('Personal Web3 agent suite: CMD · Desktop · Android\nHyperliquid · Solana · Helius · Pump.fun\n\n1. Start the official router and configure your provider accounts.\n2. Sign in to Tailscale on PC and phone with the same account.\n3. Enter the unified API key here. Wallet keys belong only in their local connector panels.')
    intro.setWordWrap(True);layout.addWidget(intro)
    router=QPushButton('Open official FreeLLMAPI router / dashboard')
    router.clicked.connect(lambda:subprocess.Popen([str(root/'router/FreeLLMAPI.exe')],cwd=root/'router'));layout.addWidget(router)
    tailscale=QPushButton('Open Tailscale — complete Google sign-in there')
    def open_tailscale():
        path=Path(os.environ.get('ProgramFiles','C:/Program Files'))/'Tailscale/tailscale-ipn.exe'
        if path.exists():subprocess.Popen([str(path)])
        else:QMessageBox.information(dialog,'Tailscale','Install Tailscale using the included MSI under prerequisites, then open it to sign in.')
    tailscale.clicked.connect(open_tailscale);layout.addWidget(tailscale)
    config=json.loads((home/'config.json').read_text(encoding='utf-8-sig')) if (home/'config.json').exists() else {}
    layout.addWidget(QLabel('API base URL'));base=QLineEdit(config.get('base_url','http://127.0.0.1:31415'));layout.addWidget(base)
    layout.addWidget(QLabel('Unified API key (leave blank to keep an existing key)'));key=QLineEdit();key.setEchoMode(QLineEdit.EchoMode.Password);layout.addWidget(key)
    layout.addWidget(QLabel('Memory vault directory'));vault=QLineEdit(config.get('vault_path',str(Path.home()/'Documents/freellmapivault')));layout.addWidget(vault)
    choose=QPushButton('Choose vault')
    def select():
        selected=QFileDialog.getExistingDirectory(dialog,'Memory vault',vault.text())
        if selected:vault.setText(selected)
    choose.clicked.connect(select);layout.addWidget(choose)
    apk=QPushButton('Install Android APK on one connected USB device')
    def install_apk():
        adb=root/'runtime/adb/platform-tools/adb.exe'
        devices=subprocess.run([str(adb),'devices'],capture_output=True,text=True,timeout=15,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        ids=[line.split()[0] for line in devices.stdout.splitlines()[1:] if line.endswith('\tdevice')]
        if len(ids)!=1:
            QMessageBox.information(dialog,'Android','Connect and unlock exactly one Android phone and approve USB debugging. APK is also available in the mobile folder.');return
        if QMessageBox.question(dialog,'Android','Install/update FREELLMAPI Remote while preserving its data?')!=QMessageBox.StandardButton.Yes:return
        result=subprocess.run([str(adb),'-s',ids[0],'install','-r',str(root/'mobile/freellmapi-remote.apk')],capture_output=True,text=True,timeout=120,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        QMessageBox.information(dialog,'Android','APK installed. Install Tailscale on the phone, sign in with the same account and pair it with the PC.' if result.returncode==0 and 'Success' in result.stdout else 'Installation failed. Verify USB debugging and the app signing certificate; existing app data was not cleared.')
    apk.clicked.connect(install_apk);layout.addWidget(apk)
    docs=QPushButton('Read English installation and Web3 guide');docs.clicked.connect(lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(root/'docs/USER_GUIDE.html'))));layout.addWidget(docs)
    save=QPushButton('Verify API and save');layout.addWidget(save)
    class Worker(QObject):done=Signal(object,object)
    worker=Worker();pool=ThreadPoolExecutor(max_workers=1)
    def finish(count,error):
        save.setEnabled(True)
        if error:QMessageBox.warning(dialog,'Setup',error)
        else:key.clear();QMessageBox.information(dialog,'Setup',f'API reachable: {count} ready catalog entries. Availability and quotas depend on your providers. Settings saved locally.');dialog.accept()
    worker.done.connect(finish)
    def verify():
        save.setEnabled(False);values=(base.text(),key.text(),vault.text())
        def task():
            try:worker.done.emit(save_settings(home,*values),None)
            except Exception as error:worker.done.emit(None,str(error))
        pool.submit(task)
    save.clicked.connect(verify)
    try:return dialog.exec()==QDialog.DialogCode.Accepted
    finally:pool.shutdown(wait=True)
