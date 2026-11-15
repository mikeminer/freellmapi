"""Check Node syntax and actual package exports without executing the target script."""
import json,re,subprocess
from pathlib import Path

def script_paths(command,root):
    pattern=r'(?i)(?<![\w.-])node(?:\.exe)?\s+(?:"([^"]+\.(?:js|mjs|cjs))"|\x27([^\x27]+\.(?:js|mjs|cjs))\x27|([^\s;|&]+\.(?:js|mjs|cjs)))'
    return [(Path(root)/next(x for x in groups if x)).resolve() for groups in re.findall(pattern,command)]

def check_script(path,root,token_mint=None):
    path=Path(path)
    if not path.is_file():raise ValueError('node_preflight: script inesistente: '+str(path)+'. Crea o individua il file prima di eseguirlo.')
    source=path.read_text(encoding='utf-8-sig')
    if re.search(r'console\.(?:log|info|debug|error)\s*\([^;]*(?:\.secretKey|\bprivateKey\b|chiave privata|mnemonic|seedPhrase)',source,re.I):
        raise ValueError('secret_output_blocked: lo script stampa chiavi private. Rimuovi la stampa prima di eseguirlo.')
    if token_mint and re.search(r'\b(?:createMint|createToken|initializeMint|createInitializeMintInstruction)\s*\(|\bnew\s+Token\s*\(|\brequestAirdrop\s*\(',source):
        raise ValueError('existing_token_required: usa il mint '+token_mint+'. Creazione di mint e faucet non pertinenti al compito; conserva il progetto e continua la build APK.')
    result=subprocess.run(['node','--check',str(path)],cwd=root,capture_output=True,text=True,timeout=15)
    if result.returncode:raise ValueError('node_preflight: sintassi non valida. '+result.stderr[-1200:])
    package=path.parent
    while package!=package.parent and not (package/'package.json').exists():package=package.parent
    esm=path.suffix=='.mjs' or ((package/'package.json').exists() and path.suffix=='.js' and json.loads((package/'package.json').read_text()).get('type')=='module')
    if esm and re.search(r'\brequire\s*\(',source) and 'createRequire' not in source:
        raise ValueError('node_preflight: package type=module; require non disponibile. Usa import oppure un modulo .cjs coerente.')
    requested={}
    for names,spec in re.findall(r"import\s*\{([^}]+)\}\s*from\s*['\"]([^'\"]+)['\"]",source):
        if spec.startswith(('.', '/', 'node:')):continue
        if not re.fullmatch(r'(?:@[\w.-]+/)?[\w.-]+(?:/[\w./-]+)?',spec):continue
        requested.setdefault(spec,[]).extend(n.strip().split(' as ')[0].strip() for n in names.split(',') if n.strip())
    if requested:
        code="import fs from 'node:fs';const modules=JSON.parse(fs.readFileSync(0,'utf8'));for(const [name,names] of Object.entries(modules)){const api=await import(name);const missing=names.filter(n=>!(n in api));if(missing.length){console.error(JSON.stringify({module:name,missing,available:Object.keys(api).filter(n=>/Mint|Token|Connection|Keypair/.test(n)).slice(0,20)}));process.exitCode=1;}}"
        result=subprocess.run(['node','--input-type=module','-e',code],input=json.dumps(requested),cwd=root,capture_output=True,text=True,timeout=20)
        if result.returncode:raise ValueError('node_preflight: API non disponibile nella versione installata. '+result.stderr[-1600:]+'. Correggi gli import, non reinstallare invariato.')
    return {'checked':str(path),'syntax':'ok','package_imports':requested}
