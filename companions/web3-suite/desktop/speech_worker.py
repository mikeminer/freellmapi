"""Local multilingual dictation worker. No model API or financial credentials."""
import json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'vendor'))
import truststore
truststore.inject_into_ssl()
from faster_whisper import WhisperModel

def main():
    try:model=WhisperModel('base',device='cpu',compute_type='int8',cpu_threads=4,download_root=str(ROOT/'models'))
    except Exception as error:
        print(json.dumps({'ready':False,'error':type(error).__name__+': '+str(error)[:500]}),flush=True);raise SystemExit(1)
    if '--download' in sys.argv:print(json.dumps({'ok':True,'model':'Whisper base','device':'cpu/int8'}),flush=True);return
    print(json.dumps({'ready':True}),flush=True)
    for line in sys.stdin:
        try:
            request=json.loads(line);path=Path(request['path']).resolve()
            if not path.is_file() or path.stat().st_size>30_000_000:raise ValueError('Audio assente o oltre 30 MB.')
            segments,info=model.transcribe(str(path),beam_size=3,vad_filter=True,condition_on_previous_text=False)
            text=' '.join(segment.text.strip() for segment in segments).strip()
            print(json.dumps({'id':request.get('id'),'text':text,'language':info.language,'duration_seconds':info.duration},ensure_ascii=False),flush=True)
        except Exception as error:print(json.dumps({'id':request.get('id') if 'request' in locals() else None,'error':type(error).__name__+': '+str(error)[:500]}),flush=True)

if __name__=='__main__':main()
