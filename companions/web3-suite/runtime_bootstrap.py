"""Stable launcher: recover interrupted releases before importing mutable runtime."""
import json,os,subprocess,sys
from pathlib import Path

def prepare(home):
    """Release recovery, then unfinished QA reports. Neither invents a passing result."""
    from release_transaction import recover
    recover(home)
    from qa_interruption import recover_reports
    return recover_reports(home)

def main():
    from app_home import default_home
    home=default_home()
    from release_transaction import rollback
    prepare(home)
    pointer=home/'runtime-environment.json';root=Path(__file__).resolve().parent
    selected=json.loads(pointer.read_text(encoding='utf-8')) if pointer.exists() else {}
    python=selected.get('python',sys.executable)
    if selected:
        try:
            result=subprocess.run([python,'-X','utf8','-c','import freellmapi'],cwd=root,capture_output=True,timeout=30)
            if result.returncode: raise RuntimeError('Avvio ambiente fallito')
        except (OSError,RuntimeError,subprocess.TimeoutExpired):
            rollback(home,selected['repair_id'])
            selected=json.loads(pointer.read_text(encoding='utf-8')) if pointer.exists() else {}
            python=selected.get('python',sys.executable)
            print('Ambiente non avviabile: ripristinata la versione precedente.')
    command=[python,'-X','utf8',str(root/'freellmapi.py'),*sys.argv[1:]]
    if os.name=='nt':
        child=subprocess.Popen(command)
        while True:
            try: sys.exit(child.wait())
            except KeyboardInterrupt: continue # The console also delivers Ctrl+C to the CLI.
    os.execv(python,command)

if __name__=='__main__':
    try: main()
    except Exception as error:
        print('Avvio sospeso per proteggere lo stato: '+str(error));sys.exit(1)
