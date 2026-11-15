"""Windows shortcut shares the Qt window's application identity and official icon."""
import subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
APP_ID='freellmapi.agent.desktop'
def install():
    import pythoncom,win32com.client
    from win32com.shell import shell
    from win32com.propsys import propsys,pscon
    path=Path.home()/'Desktop/FREELLMAPI Desktop.lnk'
    shortcut=win32com.client.Dispatch('WScript.Shell').CreateShortcut(str(path))
    shortcut.TargetPath=str(Path(sys.executable).with_name('pythonw.exe'))
    shortcut.Arguments=subprocess.list2cmdline(['-X','utf8',str(ROOT/'desktop_app.py')])
    shortcut.WorkingDirectory=str(ROOT);shortcut.IconLocation=str(ROOT/'desktop/freellmapi-official.ico')+',0'
    shortcut.Description='FREELLMAPI Desktop';shortcut.Save()
    link=pythoncom.CoCreateInstance(shell.CLSID_ShellLink,None,pythoncom.CLSCTX_INPROC_SERVER,shell.IID_IShellLink)
    persist=link.QueryInterface(pythoncom.IID_IPersistFile);persist.Load(str(path),2)  # STGM_READWRITE
    store=link.QueryInterface(propsys.IID_IPropertyStore)
    store.SetValue(pscon.PKEY_AppUserModel_ID,propsys.PROPVARIANTType(APP_ID));store.Commit();persist.Save(str(path),True)
    return path
if __name__=='__main__':print(install())
