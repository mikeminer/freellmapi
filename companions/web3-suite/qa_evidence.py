"""Validate test reports against machine-readable per-test evidence, not prose."""
import hashlib
import json
from pathlib import Path


def validate(path):
    path=Path(path); report=json.loads(path.read_text(encoding='utf-8')); errors=[]; counts={'PASS':0,'FAIL':0,'NON_VERIFICATO':0}
    if 'phases' in report:
        return validate_phases(path,report)
    rows=report.get('scenarios',{})
    if isinstance(rows,dict): rows=list(rows.values())
    if not isinstance(rows,list) or not rows: return {'ok':False,'errors':['Nessuno scenario verificabile.']}
    seen=set()
    for row in rows:
        ident=row.get('id'); state=row.get('status')
        if not ident or ident in seen: errors.append('ID scenario assente o duplicato.')
        seen.add(ident)
        if state not in counts: errors.append(f'{ident}: stato non conclusivo {state}'); continue
        counts[state]+=1
        if state=='NON_VERIFICATO':
            if not row.get('reason'): errors.append(f'{ident}: motivo mancante.')
            if not row.get('evidence'): continue
        evidence=row.get('evidence',{}); relative=evidence.get('path','')
        target=(path.parent/relative).resolve()
        if not relative or not target.is_relative_to(path.parent.resolve()) or not target.is_file():
            errors.append(f'{ident}: evidenza file assente o fuori dal run.'); continue
        raw=target.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=evidence.get('sha256'): errors.append(f'{ident}: evidenza cambiata.'); continue
        try: data=json.loads(raw)
        except ValueError: errors.append(f'{ident}: evidenza non strutturata.'); continue
        selected=row.get('test_ids',[]); records=data.get('tests',[])
        mapping={r['id']:r for r in records}
        if len(mapping)!=len(records): errors.append(f'{ident}: test duplicati nel registro.')
        if not selected or len(set(selected))!=len(selected) or any(t not in mapping for t in selected):
            errors.append(f'{ident}: selezione test vuota, duplicata o inesistente.'); continue
        statuses=[mapping[t]['status'] for t in selected]
        if any(s not in counts for s in statuses): errors.append(f'{ident}: stato test non valido.'); continue
        actual='FAIL' if 'FAIL' in statuses else 'NON_VERIFICATO' if 'NON_VERIFICATO' in statuses else 'PASS'
        if actual!=state: errors.append(f'{ident}: dichiarato {state}, evidenza {actual}.')
    if report.get('summary',{}).get('counts')!=counts: errors.append('Conteggi riepilogo incoerenti.')
    return {'ok':not errors,'counts':counts,'errors':errors,'scope':'Convalida delle evidenze registrate; non prova tutte le capacità dei modelli.'}

def validate_phases(path,report):
    """Live reports have their own contract; never repair them by running mock suites."""
    errors=[];phases=report.get('phases');counts={'PASS':0,'FAIL':0,'NOT_VERIFIED':0}
    if not isinstance(phases,dict) or not phases: return {'ok':False,'errors':['phases deve contenere le verifiche del run reale.']}
    for ident,row in phases.items():
        if not isinstance(row,dict): errors.append(f'{ident}: fase non valida.');continue
        status=row.get('status');status='NOT_VERIFIED' if status=='NON_VERIFICATO' else status
        if status not in counts: errors.append(f'{ident}: stato non conclusivo {status}.');continue
        counts[status]+=1
        if status=='NOT_VERIFIED':
            if not (row.get('reason') or row.get('notes')): errors.append(f'{ident}: motivo mancante.')
            continue
        evidence=row.get('evidence',{})
        if not isinstance(evidence,dict): evidence={}
        relative=evidence.get('path','');target=(path.parent/relative).resolve()
        if not relative or not target.is_relative_to(path.parent.resolve()) or not target.is_file():
            errors.append(f'{ident}: manca evidence con path locale e sha256; non eseguire suite simulate per sostituire prove live.');continue
        raw=target.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=evidence.get('sha256'): errors.append(f'{ident}: hash evidenza incoerente.')
        if row.get('mode') not in ('live','local','injected_failure','simulated'): errors.append(f'{ident}: indica mode live/local/injected_failure/simulated.')
        if row.get('mode')=='simulated' and report.get('kind')=='live': errors.append(f'{ident}: una suite simulata non sostituisce una prova live.')
        try: payload=json.loads(raw)
        except ValueError:
            errors.append(f'{ident}: hash presente ma il contenuto non è una prova strutturata.'); continue
        measured=_measured_status(ident,payload)
        if measured is None: errors.append(f'{ident}: hash non sufficiente. Il contenuto non contiene misure osservate che confermano {status}.')
        elif measured!=status: errors.append(f'{ident}: contenuto osservato {measured}, dichiarato {status}.')
        elif payload.get('origin') not in ('repair_runner','qa_audit_runner','local_process','injected_failure','live'): errors.append(f'{ident}: origine della prova assente.')
    # Unknown metrics must be null, never placeholder zeros carried from setup.
    if report.get('api_requests')==0 and not report.get('request_count_verified'):
        errors.append('api_requests=0 non verificato: usa null con motivo se non misurato, oppure conteggio osservato con registro richieste.')
    if report.get('status') in ('IN_PROGRESS','NOT_RUN'): errors.append('Run non concluso.')
    if report.get('require_markdown'):
        markdown=path.parent/'REPORT.md'
        if not markdown.is_file(): errors.append('REPORT.md assente: il JSON non è un report completo.')
        else:
            text=markdown.read_text(encoding='utf-8')
            for ident,row in phases.items():
                if not isinstance(row,dict): continue
                status=row.get('status'); status='NOT_VERIFIED' if status=='NON_VERIFICATO' else status
                if f'{ident}: {status}' not in text: errors.append(f'{ident}: REPORT.md non riporta {status}.')
                if status!='PASS' and f'{ident}: PASS' in text: errors.append(f'{ident}: REPORT.md dichiara PASS in contraddizione con {status}.')
    return {'ok':not errors,'counts':counts,'errors':errors,'scope':'Integrità documentale delle prove. Un hash non dimostra da solo il contenuto: lo stato deve concordare con le misure nel file. Nessuna suite simulata richiesta.'}

def _contaminated(data):
    provenance=data.get('provenance') or {}
    return bool(data.get('contamination') or provenance.get('contaminated_modules') or provenance.get('contaminated_opens'))

def _measured_status(phase,data):
    """Interpret the evidence file. Returning None means the hash alone is not proof."""
    if not isinstance(data,dict) or data.get('observed') is not True: return None
    if phase.endswith('_isolation'):
        if _contaminated(data): return 'FAIL'
        modules=(data.get('provenance') or {}).get('modules') or []
        return 'PASS' if modules or data.get('tests',0)>0 else 'FAIL'
    if phase=='baseline_regression':
        if _contaminated(data): return 'FAIL'
        if data.get('failures',0)>0 and data.get('errors',0)==0 and data.get('ok') is False and data.get('tests',0)>0: return 'PASS'
        return 'FAIL'
    if phase in ('candidate_regression','suite','post_activation'):
        if _contaminated(data): return 'FAIL'
        if data.get('ok') is True and data.get('failures',0)==0 and data.get('errors',0)==0 and data.get('tests',0)>0: return 'PASS'
        return 'FAIL'
    if phase=='activation':
        return 'PASS' if data.get('state')=='activated_restart_required' and data.get('hashes_match') is True and data.get('exit_code')==0 else 'FAIL'
    if phase=='rollback':
        return 'PASS' if data.get('state')=='rolled_back' and data.get('hashes_match') is True and data.get('new_files_removed') is True else 'FAIL'
    if _contaminated(data): return 'FAIL'
    if data.get('ok') is True or data.get('exit_code')==0 or data.get('status')=='PASS': return 'PASS'
    if data.get('ok') is False or (isinstance(data.get('exit_code'),int) and data.get('exit_code')!=0) or data.get('status')=='FAIL': return 'FAIL'
    return None
