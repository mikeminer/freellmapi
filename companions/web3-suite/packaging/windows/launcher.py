"""Installed suite entry point. No account keys, order replay or system Python."""
import argparse,json,os,runpy,subprocess,sys
from pathlib import Path


def configure(root):
    root=Path(root).resolve()
    bins=['runtime/python','runtime/node','runtime/powershell','runtime/git/cmd','runtime/adb/platform-tools']
    os.environ['PATH']=os.pathsep.join(str(root/name) for name in bins)+os.pathsep+os.environ.get('PATH','')
    os.environ['PLAYWRIGHT_BROWSERS_PATH']=str(root/'runtime/browsers')
    os.environ['FREELLMAPI_BROWSER_CHANNEL']='chromium'
    os.environ['FREELLMAPI_SUITE_ROOT']=str(root)
    os.environ.setdefault('NODE_USE_SYSTEM_CA','1')
    sys.path.insert(0,str(root/'agent'))
    site=root/'runtime/python/Lib/site-packages'
    if os.name=='nt' and (site/'pywin32_system32').is_dir():
        # Embedded Python does not process .pth files when site-packages is
        # explicitly listed in python313._pth. Load pywin32's DLL directory.
        sys.path.extend(str(site/name) for name in ('win32','win32/lib','Pythonwin'))
        os.environ['PATH']=str(site/'pywin32_system32')+os.pathsep+os.environ['PATH']
        global _win32_dlls
        _win32_dlls=os.add_dll_directory(str(site/'pywin32_system32'))
    return root


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=('desktop','cmd','setup','router','mobile','doctor'))
    args,extra=parser.parse_known_args()
    root=configure(Path(__file__).resolve().parents[1])
    if args.mode=='doctor':
        import importlib
        for name in ('freellmapi','desktop_backend','PySide6.QtWebEngineWidgets','win32api','solders','faster_whisper'):
            importlib.import_module(name)
        versions=json.loads((root/'suite-manifest.json').read_text(encoding='utf-8'))['components']
        print(json.dumps({'ok':True,'python':sys.version.split()[0],'private_prefix':str(Path(sys.prefix).resolve())==str(root/'runtime/python'),
                          'components':{k:v['version'] for k,v in versions.items()},'scope':'imports only; no model, wallet or trading call'}))
        return 0
    if args.mode=='router':
        subprocess.Popen([str(root/'router/FreeLLMAPI.exe')],cwd=root/'router')
        return 0
    from app_home import default_home
    home=default_home()
    from suite_setup import wizard
    if args.mode in ('setup','mobile') or not (home/'config.json').exists() or not (home/'key.dpapi').exists():
        if not wizard(root,home):return 1
        if args.mode in ('setup','mobile'):return 0
        # The setup dialog owns a QApplication; start the selected client in a
        # fresh process rather than constructing a second QApplication.
        os.execv(sys.executable,[sys.executable,str(Path(__file__).resolve()),args.mode,*extra])
    sys.argv=[str(root/'agent'/('desktop_app.py' if args.mode=='desktop' else 'runtime_bootstrap.py')),*extra]
    runpy.run_path(sys.argv[0],run_name='__main__')
    return 0


if __name__=='__main__':sys.exit(main())
