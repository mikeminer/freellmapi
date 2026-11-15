"""Bounded, serial-scoped Android diagnostics. No shell interpolation."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading

class DeviceUnavailable(ValueError):
    """A physical-device prerequisite needs user action, not automatic retries."""
    pass


def find_adb():
    candidates = [shutil.which('adb')]
    for root in (os.getenv('ANDROID_SDK_ROOT'), os.getenv('ANDROID_HOME'),
                 str(Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'Android/Sdk')):
        if root:
            candidates.append(str(Path(root) / 'platform-tools/adb.exe'))
    return next((p for p in candidates if p and Path(p).is_file()), None)


class Seeker:
    def __init__(self, home, adb=None):
        self.home = Path(home)
        self.adb = adb or find_adb()
        self.settings = self.home / 'seeker.json'
        try:
            self.config = json.loads(self.settings.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            self.config = {}
        self.summary = 'non verificato' if self.adb else 'ADB assente'
        self.stop_event = threading.Event()
        self.monitor = None

    def run(self, args, serial=None, timeout=20, empty_exit_one=False):
        if not self.adb:
            raise RuntimeError('ADB assente. Installa Android SDK Platform Tools o configura ANDROID_SDK_ROOT.')
        command = [self.adb] + (['-s', serial] if serial else []) + list(args)
        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8',
                                    errors='replace', timeout=timeout,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except subprocess.TimeoutExpired:
            raise RuntimeError('Timeout ADB: verifica il collegamento. Lo stato dell’operazione sul telefono va ricontrollato.') from None
        text = (result.stdout + '\n' + result.stderr).strip()
        if empty_exit_one and result.returncode == 1 and not text:
            return ''
        if result.returncode or re.search(r'^Failure\b|^Error:', text, re.M):
            raise RuntimeError(text[-16000:] or 'Comando ADB fallito.')
        return text

    def devices(self):
        rows = []
        for line in self.run(['devices', '-l']).splitlines():
            parts = line.split()
            if len(parts) < 2 or parts[1] not in ('device', 'offline', 'unauthorized', 'recovery', 'sideload'):
                continue
            row = {'serial': parts[0], 'state': parts[1]}
            row.update(dict(p.split(':', 1) for p in parts[2:] if ':' in p))
            rows.append(row)
        self.summary = ', '.join(f"{r.get('model', r['serial'])}: {r['state']}" for r in rows) if rows else 'scollegato'
        return rows

    def start(self):
        if self.monitor:
            return
        def poll():
            while not self.stop_event.is_set():
                try:
                    self.devices()
                except Exception as exc:
                    self.summary = 'errore ADB: ' + str(exc)[:100]
                self.stop_event.wait(4)
        self.monitor = threading.Thread(target=poll, daemon=True, name='seeker-detection')
        self.monitor.start()

    def close(self):
        self.stop_event.set()

    def target(self, serial=None):
        rows = self.devices()
        selected = serial or self.config.get('serial')
        if selected:
            match = next((r for r in rows if r['serial'] == selected), None)
            if not match:
                raise DeviceUnavailable('Dispositivo selezionato scollegato. Ricollegalo e autorizza Debug USB; poi /retry. Non ricompilare o riavviare ADB automaticamente.')
            if match['state'] != 'device':
                raise DeviceUnavailable('Dispositivo ' + match['state'] + ': sblocca il Seeker e autorizza il debug USB; poi /retry.')
            return selected
        ready = [r for r in rows if r['state'] == 'device']
        seekers = [r for r in ready if 'seeker' in r.get('model', '').lower()]
        candidates = seekers if seekers else ready
        if len(candidates) == 1:
            return candidates[0]['serial']
        raise DeviceUnavailable('Nessun dispositivo autorizzato o più dispositivi disponibili. Apri /seeker; abilita Debug USB e autorizza il PC sul telefono. Poi /retry senza rifare la build.')

    def select(self, serial=None, package=None):
        if serial is not None:
            self.target(serial)
            self.config['serial'] = serial
        if package is not None:
            self.config['package'] = self.package(package)
        self.home.mkdir(parents=True, exist_ok=True)
        self.settings.write_text(json.dumps(self.config, indent=2), encoding='utf-8')

    def package(self, value=None):
        value = value or self.config.get('package', '')
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z][A-Za-z0-9_]*)+', value):
            raise ValueError('Seleziona un package Android valido (es. com.example.game) nel pannello /seeker.')
        return value

    def packages(self, serial=None):
        return self.run(['shell', 'pm', 'list', 'packages', '-3'], self.target(serial))

    def logs(self, package=None, serial=None, lines=300):
        serial = self.target(serial)
        package = self.package(package)
        # pidof returns exit 1 for stopped apps; do not silently show unrelated system logs.
        pids = self.run(['shell', 'pidof', package], serial, empty_exit_one=True).split()
        if not pids:
            return 'App non in esecuzione. Usa i crash/exit-info per gli arresti precedenti oppure avvia l’app.'
        pid = pids[0]
        if not pid.isdigit():
            raise ValueError('PID ADB non valido.')
        return self.run(['logcat', '-d', '-v', 'threadtime', '--pid=' + pid, '-t', str(max(1, min(int(lines), 2000)))], serial) or 'Nessun log disponibile per questo processo.'

    def crashes(self, package=None, serial=None):
        serial = self.target(serial)
        package = self.package(package)
        crash = self.run(['logcat', '-b', 'crash', '-d', '-v', 'threadtime', '-t', '500'], serial)
        try:
            exits = self.run(['shell', 'dumpsys', 'activity', 'exit-info', package], serial)
        except RuntimeError as exc:
            exits = 'Exit-info non disponibile: ' + str(exc)
        return ('APP: ' + package + '\nEXIT-INFO (app selezionata):\n' + exits +
                '\nBUFFER CRASH (globale: può includere altre app; correlare package/PID e timestamp):\n' +
                (crash or 'Nessun crash nel buffer. Il buffer non è una cronologia permanente.'))[-60000:]

    def install(self, path, serial):
        path = Path(path).expanduser().resolve()
        if path.suffix.lower() != '.apk' or not path.is_file():
            raise ValueError('Indica un file APK esistente.')
        result = self.run(['install', '-r', str(path)], serial, timeout=180)
        if not re.search(r'^Success\s*$', result, re.M):
            raise RuntimeError('Installazione non confermata: ' + result)
        return result

    def launch(self, package, serial):
        package = self.package(package)
        resolved = self.run(['shell', 'cmd', 'package', 'resolve-activity', '--brief',
                             '-a', 'android.intent.action.MAIN', '-c', 'android.intent.category.LAUNCHER', package], serial)
        component = next((s.strip() for s in resolved.splitlines() if re.fullmatch(r'[\w.$]+/[\w.$]+', s.strip())), None)
        if not component:
            raise ValueError('Nessuna attività avviabile per ' + package)
        return self.run(['shell', 'am', 'start', '-W', '-n', component], serial)

    def stop(self, package, serial):
        return self.run(['shell', 'am', 'force-stop', self.package(package)], serial) or 'App arrestata.'


def dispatch(agent, name, args):
    seeker = agent.seeker
    if name == 'seeker_devices':
        return json.dumps({'adb': seeker.adb, 'devices': seeker.devices(), 'selection': seeker.config}, ensure_ascii=False)
    serial = seeker.target(args.get('serial'))
    if name == 'seeker_packages':
        return seeker.packages(serial)
    if name == 'seeker_logs':
        return seeker.logs(args.get('package'), serial, args.get('lines', 300))
    if name == 'seeker_crashes':
        return seeker.crashes(args.get('package'), serial)
    if name not in ('seeker_install', 'seeker_launch', 'seeker_stop'):
        raise ValueError('Strumento Seeker sconosciuto.')
    value = str((agent.root / args['path']).resolve()) if name == 'seeker_install' else seeker.package(args.get('package'))
    if not agent.approve(f'{name} · dispositivo {serial} · {value}' + (' (reinstalla mantenendo i dati)' if name == 'seeker_install' else '')):
        return 'Operazione Seeker non autorizzata.'
    import terminal_ui as ui
    with ui.busy('ADB · ' + name):
        return getattr(seeker, name.removeprefix('seeker_'))(value, serial)


def panel(agent):
    import terminal_ui as ui
    from questions import ask
    seeker = agent.seeker
    while True:
        try:
            rows = seeker.devices()
            ui.write('freellmapi · Seeker\n' + seeker.summary + '\nApp: ' + seeker.config.get('package', 'da selezionare'))
            options = ['Dispositivo / app', 'Installa APK', 'Avvia app', 'Arresta app', 'Log app', 'Log in diretta', 'Crash / exit-info', 'Torna alla chat']
            result = ask('Pannello Seeker — scegli un’azione', options)
            choice = result.get('answer')
            if result.get('cancelled') or choice == options[-1]:
                return
            if choice == options[0]:
                ready = [r for r in rows if r['state'] == 'device']
                if not ready:
                    ui.write('Collega il Seeker, abilita Debug USB e accetta la richiesta RSA sul telefono.'); continue
                if len(ready) == 1:
                    serial = ready[0]['serial']
                else:
                    labels = [r['serial'] + ' · ' + r.get('model', '') for r in ready]
                    selected = ask('Quale dispositivo?', labels[:8]).get('answer', '')
                    serial = selected.split(' · ')[0]
                    if not serial: continue
                seeker.select(serial=serial)
                packages = [p.removeprefix('package:') for p in seeker.packages(serial).splitlines() if p.startswith('package:')]
                query = input('Filtra le app per nome (Invio = tutte): ').strip().lower()
                packages = sorted(p for p in packages if query in p.lower())
                page = 0
                while packages:
                    subset = packages[page:page+6]
                    answer = ask('App da diagnosticare (Altro per inserire il package)', subset + ['Pagina successiva', 'Annulla']).get('answer')
                    if answer == 'Pagina successiva': page = (page+6) if page+6 < len(packages) else 0; continue
                    if answer and answer != 'Annulla': seeker.select(package=answer)
                    break
                if not packages:
                    value = input('Nessuna corrispondenza. Package (Invio annulla): ').strip()
                    if value: seeker.select(package=value)
            elif choice == options[1]:
                path = input('Percorso APK (Invio annulla): ').strip().strip('"')
                if path: ui.write(dispatch(agent, 'seeker_install', {'path': path}))
            elif choice in (options[2], options[3], options[4], options[6]):
                name = {options[2]:'launch', options[3]:'stop', options[4]:'logs', options[6]:'crashes'}[choice]
                ui.write(dispatch(agent, 'seeker_' + name, {}))
            elif choice == options[5]:
                live_logs(seeker)
            else:
                ui.write('Seleziona un’azione del pannello. Per richieste libere torna alla chat.')
        except KeyboardInterrupt:
            return
        except Exception as exc:
            ui.write('Seeker: ' + str(exc))
            if not seeker.adb: return


def live_logs(seeker):
    """Stream a fixed PID until Ctrl+C; always reap the local adb client."""
    import terminal_ui as ui
    serial = seeker.target()
    package = seeker.package()
    pids = seeker.run(['shell', 'pidof', package], serial, empty_exit_one=True).split()
    if not pids: raise ValueError('App non in esecuzione. Avviala prima di aprire i log live.')
    pid = pids[0]
    if not pid.isdigit(): raise ValueError('PID non valido.')
    ui.write(f'Log live · {package} · PID {pid} · Ctrl+C torna al pannello. Se riavvii l’app, riapri i log.')
    process = subprocess.Popen([seeker.adb, '-s', serial, 'logcat', '-v', 'threadtime', '--pid='+pid, '-T', '1'],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    # Reader thread keeps Ctrl+C responsive on Windows even if no lines arrive.
    import queue
    lines = queue.Queue(maxsize=1000)
    done = threading.Event()
    def read():
        try:
            for line in process.stdout:
                while not done.is_set():
                    try: lines.put(line, timeout=.2); break
                    except queue.Full: pass
        finally: done.set()
    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    try:
        while not done.is_set() or not lines.empty():
            try: ui.write(lines.get(timeout=.2).rstrip())
            except queue.Empty: pass
    except KeyboardInterrupt:
        pass
    finally:
        done.set()
        if process.poll() is None: process.terminate()
        try: process.wait(timeout=3)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
        thread.join(timeout=3)
        process.stdout.close()
    ui.write('Log live terminati.')
