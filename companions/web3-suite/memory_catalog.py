"""Filesystem catalog: skip archives during auto recall, reuse unchanged notes."""
import os


class NoteCatalog:
    def __init__(self):
        self.cache={};self.reads=0;self.hits=0

    def paths(self,root,purpose='explicit'):
        for folder,dirs,files in os.walk(root,followlinks=False):
            dirs[:]=[d for d in dirs if d not in ('.git','__pycache__') and
                     (purpose!='auto' or d not in ('Conversations','Evidence','FREELLMAPI Desktop'))]
            for name in files:
                if name.endswith('.md'):yield type(root)(folder)/name

    def read(self,path):
        stat=path.stat();key=str(path);signature=(stat.st_mtime_ns,stat.st_size)
        cached=self.cache.get(key)
        if cached and cached[0]==signature:self.hits+=1;return cached[1]
        text=path.read_text(encoding='utf-8');self.reads+=1
        self.cache[key]=(signature,text)
        if len(self.cache)>256:self.cache.pop(next(iter(self.cache)))
        return text
