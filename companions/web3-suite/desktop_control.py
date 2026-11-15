"""Windows foreground control with fresh screenshot references and focus checks."""
import ctypes
from ctypes import wintypes as W
from pathlib import Path
import time
import uuid


class Desktop:
    def __init__(self,home):
        self.home=Path(home)/'screenshots'; self.frame=None

    def api(self):
        user=ctypes.windll.user32
        # Keep screenshot pixels, window rectangles and cursor coordinates in the
        # same physical-pixel coordinate space on scaled/multiple displays.
        user.SetThreadDpiAwarenessContext.argtypes=[ctypes.c_void_p]
        user.SetThreadDpiAwarenessContext.restype=ctypes.c_void_p
        user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        user.GetForegroundWindow.restype=W.HWND
        user.IsWindow.argtypes=[W.HWND]
        user.SetForegroundWindow.argtypes=[W.HWND]
        user.GetWindowRect.argtypes=[W.HWND,ctypes.POINTER(W.RECT)]
        user.GetWindowTextLengthW.argtypes=[W.HWND]
        user.GetWindowTextW.argtypes=[W.HWND,W.LPWSTR,ctypes.c_int]
        return user

    def windows(self):
        user=self.api(); rows=[]
        user.IsWindowVisible.argtypes=[W.HWND]
        @ctypes.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
        def visit(hwnd,param):
            size=user.GetWindowTextLengthW(hwnd)
            if size and user.IsWindowVisible(hwnd):
                title=ctypes.create_unicode_buffer(size+1); user.GetWindowTextW(hwnd,title,size+1)
                rows.append({'id':int(hwnd),'title':title.value})
            return True
        user.EnumWindows(visit,0); return rows

    def focus(self,ident):
        if ident not in [r['id'] for r in self.windows()]: raise ValueError('Finestra non presente: usa desktop_windows.')
        user=self.api(); user.SetForegroundWindow(ident); time.sleep(.2)
        if user.GetForegroundWindow()!=ident: raise RuntimeError('Windows ha impedito il cambio focus. Seleziona manualmente la finestra.')
        return self.snapshot()

    def snapshot(self):
        from PIL import ImageGrab
        user=self.api(); hwnd=user.GetForegroundWindow(); rect=W.RECT()
        if not hwnd or not user.GetWindowRect(hwnd,ctypes.byref(rect)): raise RuntimeError('Nessuna finestra attiva.')
        bounds=(rect.left,rect.top,rect.right,rect.bottom)
        self.home.mkdir(parents=True,exist_ok=True); ident=uuid.uuid4().hex[:12]; path=self.home/(ident+'.png')
        shot=ImageGrab.grab(bbox=bounds,all_screens=True); shot.save(path)
        self.frame={'ref':ident,'window':int(hwnd),'bounds':bounds,'path':str(path),'width':shot.width,'height':shot.height,'at':time.monotonic()}
        return {k:v for k,v in self.frame.items() if k!='at'}

    def validate(self,ref):
        frame=self.frame
        if not frame or ref!=frame['ref'] or time.monotonic()-frame['at']>60: raise ValueError('Screenshot scaduto o sconosciuto: usa desktop_snapshot e image_read.')
        user=self.api(); rect=W.RECT()
        if user.GetForegroundWindow()!=frame['window']: raise ValueError('Focus cambiato: acquisisci un nuovo screenshot.')
        user.GetWindowRect(frame['window'],ctypes.byref(rect))
        if (rect.left,rect.top,rect.right,rect.bottom)!=tuple(frame['bounds']): raise ValueError('Finestra spostata: acquisisci un nuovo screenshot.')
        return frame

    def click(self,ref,x,y):
        frame=self.validate(ref)
        if not 0<=x<frame['width'] or not 0<=y<frame['height']: raise ValueError('Coordinate fuori screenshot.')
        user=self.api(); user.SetCursorPos(frame['bounds'][0]+x,frame['bounds'][1]+y)
        user.mouse_event(2,0,0,0,0); user.mouse_event(4,0,0,0,0)
        self.frame=None; return {'clicked':True,'note':'Acquisisci un nuovo screenshot per verificare il risultato.'}

    def type(self,ref,text):
        self.validate(ref)
        if len(text)>5000: raise ValueError('Testo oltre 5000 caratteri.')
        class KEY(ctypes.Structure):
            _fields_=[('vk',W.WORD),('scan',W.WORD),('flags',W.DWORD),('time',W.DWORD),('extra',ctypes.c_size_t)]
        class MOUSE(ctypes.Structure):
            _fields_=[('dx',W.LONG),('dy',W.LONG),('data',W.DWORD),('flags',W.DWORD),('time',W.DWORD),('extra',ctypes.c_size_t)]
        class UNION(ctypes.Union): _fields_=[('key',KEY),('mouse',MOUSE)]
        class INPUT(ctypes.Structure): _fields_=[('type',W.DWORD),('value',UNION)]
        raw=text.encode('utf-16-le'); user=self.api()
        for i in range(0,len(raw),2):
            code=int.from_bytes(raw[i:i+2],'little')
            for flags in (4,6):
                inp=INPUT(1,UNION(key=KEY(0,code,flags,0,0)))
                if user.SendInput(1,ctypes.byref(inp),ctypes.sizeof(inp))!=1: raise RuntimeError('Input bloccato da Windows.')
        self.frame=None; return {'typed_characters':len(text),'submitted':False}

    def hotkey(self,ref,keys):
        self.validate(ref)
        mapping={'CTRL':17,'ALT':18,'SHIFT':16,'ENTER':13,'TAB':9,'ESC':27,'BACKSPACE':8,'DELETE':46,'UP':38,'DOWN':40,'LEFT':37,'RIGHT':39,'HOME':36,'END':35}
        names=[s.strip().upper() for s in keys.split('+')]
        if not 1<=len(names)<=4: raise ValueError('Da 1 a 4 tasti.')
        codes=[]
        for name in names:
            if name in mapping: codes.append(mapping[name])
            elif len(name)==1 and name.isascii() and name.isalnum(): codes.append(ord(name))
            else: raise ValueError('Tasto non supportato: '+name)
        user=self.api()
        for code in codes: user.keybd_event(code,0,0,0)
        for code in reversed(codes): user.keybd_event(code,0,2,0)
        self.frame=None; return {'pressed':names}
