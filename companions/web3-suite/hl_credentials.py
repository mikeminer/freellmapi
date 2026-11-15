"""Windows-user-bound credential blobs. No secret CLI arguments or model tool fields."""
import ctypes
import os
from ctypes import wintypes
from reliable_runtime import atomic_bytes

class Blob(ctypes.Structure):
    _fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]

def transform(raw,protect):
    if os.name!='nt':raise RuntimeError('Hyperliquid credential storage requires Windows DPAPI.')
    buf=(ctypes.c_ubyte*len(raw)).from_buffer_copy(raw);source=Blob(len(raw),buf);target=Blob()
    crypt=ctypes.windll.crypt32
    ok=crypt.CryptProtectData(ctypes.byref(source),'freellmapi Hyperliquid',None,None,None,1,ctypes.byref(target)) if protect else crypt.CryptUnprotectData(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target))
    if not ok:raise RuntimeError('DPAPI unavailable for this Windows user; no plaintext fallback.')
    try:return ctypes.string_at(target.data,target.size)
    finally:
        ctypes.memset(target.data,0,target.size)
        ctypes.windll.kernel32.LocalFree.argtypes=[ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(target.data)

def put(path,value):atomic_bytes(path,transform(value.encode('ascii'),True))
def get(path):return transform(path.read_bytes(),False).decode('ascii')
