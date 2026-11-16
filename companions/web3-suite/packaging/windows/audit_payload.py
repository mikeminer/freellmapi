"""Audit distributable files; optional private needles stay outside the payload."""
import argparse,hashlib,json,re
from pathlib import Path

EXCLUDED={'__pycache__','.links','__dirlock'}
STATE_NAMES={'runtime-home.json','.env','freeapi.db','operations.db','devices.json','requests.json','pair.png','pair.html'}
SECRET_SUFFIXES={'.dpapi','.jks','.keystore','.pfx','.p12'}
TEXT_SUFFIXES={'.py','.js','.mjs','.json','.md','.txt','.html','.xml','.java','.yml','.yaml','.ini','.cfg','.pem','.key'}

def audit(payload,needles=()):
    root=Path(payload).resolve(strict=True);findings=[];count=0;size=0
    encoded=[v.encode('utf-8') for v in needles if isinstance(v,str) and len(v)>=6]
    encoded.extend(v.decode().encode('utf-16-le') for v in list(encoded))
    for file in root.rglob('*'):
        if not file.is_file() or EXCLUDED.intersection(file.relative_to(root).parts):continue
        name=file.relative_to(root).as_posix();count+=1;size+=file.stat().st_size
        if file.name.lower() in STATE_NAMES or file.suffix.lower() in SECRET_SUFFIXES or name.lower()=='agent/config.json':
            findings.append({'file':name,'kind':'personal_state_or_signer_file'});continue
        overlap=max([len(v) for v in encoded] or [1]);tail=b'';matched=False
        with file.open('rb') as stream:
            while chunk:=stream.read(4*1024*1024):
                combined=tail+chunk
                if any(value in combined for value in encoded):matched=True;break
                tail=combined[-overlap:]
        if matched:findings.append({'file':name,'kind':'private_identifier_match'})
        if file.suffix.lower() in TEXT_SUFFIXES and file.stat().st_size<20_000_000:
            text=file.read_text(encoding='utf-8',errors='replace')
            if re.search(r'(?m)^-----BEGIN (?:RSA |EC |ENCRYPTED |OPENSSH )?PRIVATE KEY-----\s*$',text):
                findings.append({'file':name,'kind':'private_key_material'})
    return {'status':'PASS' if not findings else 'FAIL','files_scanned':count,'bytes_scanned':size,
            'private_identifier_needles':len(needles),'findings':findings,
            'scope':'Distributable files; source comes from tracked Git export. Public third-party licenses and synthetic tests remain included.'}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--payload',type=Path,required=True)
    p.add_argument('--forbidden-values-file',type=Path);p.add_argument('--report',type=Path,required=True);args=p.parse_args()
    values=json.loads(args.forbidden_values_file.read_text(encoding='utf-8')) if args.forbidden_values_file else []
    assert isinstance(values,list)
    if args.forbidden_values_file and args.forbidden_values_file.resolve().is_relative_to(args.payload.resolve()):raise ValueError('Private scan needles must stay outside the payload.')
    result=audit(args.payload,values);args.report.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result));raise SystemExit(0 if result['status']=='PASS' else 1)
