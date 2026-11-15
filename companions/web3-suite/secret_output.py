"""Redact credential-shaped output before terminal, logs and context persistence."""
import json
import re

LABEL=r'(?:chiave\s+privata|private[ _-]?key|secret[ _-]?key|seed[ _-]?phrase|mnemonic|api[ _-]?key|authorization)'
def redact(text):
    text=str(text)
    # Structured tool output must remain valid JSON. Line-based replacement can
    # otherwise consume sibling fields after a harmless "no private key" note.
    if text.lstrip().startswith(('{','[')):
        try:
            original=json.loads(text)
            def scrub(value):
                if isinstance(value,dict):
                    return {key:('[REDACTED]' if re.fullmatch(LABEL+r's?',str(key),re.I) and item is not None and not isinstance(item,bool) else scrub(item)) for key,item in value.items()}
                if isinstance(value,list):
                    if len(value)==64 and all(type(item) is int and 0<=item<=255 for item in value):return '[REDACTED: 64-byte key material]'
                    return [scrub(item) for item in value]
                return redact(value) if isinstance(value,str) else value
            cleaned=scrub(original)
            return text if cleaned==original else json.dumps(cleaned,ensure_ascii=False)
        except (ValueError,TypeError,RecursionError):pass
    text=re.sub(r'(?i)\b(?:freellmapi-|sk-)[a-z0-9_-]{16,}','[REDACTED]',text)
    text=re.sub(r'(?i)\bBearer\s+[a-z0-9._-]{16,}','Bearer [REDACTED]',text)
    text=re.sub(r'(?im)^([^\r\n]*'+LABEL+r'[^\r\n:={}]*[:=]\s*)[^\r\n]+',r'\1[REDACTED]',text)
    text=re.sub(r'(?<!\d)(?:\d{1,3}\s*,\s*){63}\d{1,3}(?!\d)','[REDACTED: 64-byte key material]',text)
    return text

class StreamFilter:
    def __init__(self):self.buffer='';self.array_secret=False
    def feed(self,text,final=False):
        self.buffer+=text;out=[]
        while '\n' in self.buffer or (final and self.buffer):
            line,sep,rest=self.buffer.partition('\n');self.buffer=rest
            if self.array_secret:
                if ']' in line or '}' in line:self.array_secret=False
                continue
            if re.search(LABEL,line,re.I) and '[' in line and ']' not in line:self.array_secret=True
            if re.search(r'Uint8Array\(64\)\s*\[',line):
                self.array_secret=']' not in line;line='[REDACTED: 64-byte key material]'
            out.append(redact(line)+sep)
        if len(self.buffer)>65536:
            out.append('[REDACTED: oversized output line]\n');self.buffer=''
        return ''.join(out)

def collect_to_log(proc,log,stop):
    import codecs
    decoder=codecs.getincrementaldecoder('utf-8')('replace');sanitize=StreamFilter()
    with open(log,'w',encoding='utf-8') as writer:
        try:
            while not stop.is_set():
                raw=proc.stdout.read1(4096)
                if not raw:break
                value=sanitize.feed(decoder.decode(raw))
                if value:writer.write(value);writer.flush()
            value=sanitize.feed(decoder.decode(b'',final=True),final=True)
            if value:writer.write(value);writer.flush()
        finally:proc.stdout.close()
