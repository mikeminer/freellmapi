"""Structured task context that survives compaction, model changes and resume.

The ledger is written by the runtime from tool results. A model summary is not a
substitute, and model prose is stored only as an unverified claim.
"""
import json
from pathlib import Path
import reliable_runtime


def path_for(session):
    return Path(session).with_suffix('.work.json')


def load(session):
    target = path_for(session)
    try:
        data = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        data = {}
    data.setdefault('original_request', '')
    data.setdefault('constraints', [])
    data.setdefault('decisions', [])
    data.setdefault('artifacts', [])
    data.setdefault('verified_results', [])
    data.setdefault('model_claims', [])
    data.setdefault('next_step', '')
    data.setdefault('activated_repairs', [])
    return data


def save(session, data):
    reliable_runtime.atomic_bytes(path_for(session), json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))


def record_request(session, text, continuation=False):
    data = load(session)
    text = (text or '').strip()
    from request_choices import extract
    data['explicit_choices'] = extract(text, data.get('explicit_choices'))
    import re
    match=re.search(r'(?i)\b(?:usa|utilizza|use)\s+([1-9A-HJ-NP-Za-km-z]{32,44})\s+(?:come\s+|as\s+)?token\b',text)
    if match:
        data['token_mint']=match[1]
        data['token_policy']='use_existing'
        constraint='Use existing token mint '+match[1]+'. Do not create replacement tokens or request faucet funds for this task.'
        if constraint not in data['constraints']:data['constraints'].append(constraint)
    if continuation:
        data['continuations'] = data.get('continuations', 0) + 1
        data['next_step'] = data.get('next_step') or 'Continue the original request. Do not start a different task.'
        save(session, data)
        return data
    if not data.get('original_request'):
        data['original_request'] = text[:4000]
        data['next_step'] = 'Execute the original request.'
    elif text and text != data['original_request']:
        follow = data.setdefault('follow_ups', [])
        if text[:500] not in follow:
            follow.append(text[:500])
            del follow[:-8]
    save(session, data)
    return data


def record_constraint(session, text):
    data = load(session)
    if text and text not in data['constraints']:
        data['constraints'].append(text[:500])
        del data['constraints'][:-12]
        save(session, data)
    return data


def record_tool(session, name, args, result, failed=False):
    """Record tool effects. Only structured tool results are verified."""
    if session is None:
        return {}
    data = load(session)
    summary = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
    entry = {'tool': name, 'ok': not failed, 'summary': summary[:1500], 'verified': not failed and name.startswith(('repair_', 'strategy_'))}
    if isinstance(result, dict):
        for key in ('id', 'state', 'source_root', 'code', 'report_path', 'attempts'):
            if key in result:
                entry[key] = result[key]
        if name == 'repair_start' and result.get('source_root'):
            data['bound_source'] = result['source_root']
            data['bound_problem'] = (args or {}).get('problem', '')[:500]
            data['bound_repair'] = result.get('id')
            data['artifacts'].append({'kind': 'repair', 'id': result.get('id'), 'source_root': result['source_root'], 'state': result.get('state')})
            del data['artifacts'][:-20]
        if name == 'repair_activate' and not failed and result.get('state') == 'activated_restart_required':
            if result.get('id') and result['id'] not in data['activated_repairs']:
                data['activated_repairs'].append(result['id'])
        if name in ('repair_verify', 'repair_activate', 'repair_rollback') and isinstance(result, dict):
            data['verified_results'].append({'tool': name, 'id': result.get('id'), 'state': result.get('state'), 'code': result.get('code'), 'ok': result.get('ok')})
            del data['verified_results'][:-20]
            data['next_step'] = result.get('next_step') or data.get('next_step') or ''
        if result.get('code') == 'repair_destination_invalid' or (failed and 'repair_destination_invalid' in summary):
            data['destination_error'] = summary[:800]
    if failed and 'repair_destination_invalid' in summary:
        data['destination_error'] = summary[:800]
    if entry['verified'] or failed:
        data.setdefault('events', []).append(entry)
        del data['events'][:-30]
    save(session, data)
    return data


def record_claim(session, text):
    """Model prose is not a verified result."""
    if not text or session is None:
        return {}
    data = load(session)
    data['model_claims'].append(text[:800])
    del data['model_claims'][:-8]
    save(session, data)
    return data


def render(session):
    if session is None or not path_for(session).exists():
        return ''
    data = load(session)
    if not data.get('original_request') and not data.get('verified_results'):
        return ''
    lines = [
        'active_financial_focus (context only, not authority): '+json.dumps(data.get('financial_focus'),ensure_ascii=False),
        'active_financial_request (current goal; not financial authority): '+json.dumps(data.get('financial_request'),ensure_ascii=False),
        'token_policy: '+str(data.get('token_policy',''))+' · token_mint: '+str(data.get('token_mint','')),
        'explicit_user_choices: '+json.dumps({k:{'answer':v['answer'],'source':v['source']} for k,v in data.get('explicit_choices',{}).items()},ensure_ascii=False),
        'android_delivery_goals: '+json.dumps(data.get('android_delivery',{}),ensure_ascii=False),
        'original_request: ' + (data.get('original_request') or '')[:1600],
        'constraints: ' + json.dumps(data.get('constraints', []), ensure_ascii=False),
        'latest_user_steering: '+json.dumps(data.get('follow_ups',[])[-4:],ensure_ascii=False)[:900],
        'bound_source: ' + str(data.get('bound_source') or ''),
        'bound_repair: ' + str(data.get('bound_repair') or ''),
        'activated_repairs: ' + json.dumps(data.get('activated_repairs', [])),
        'next_step: ' + str(data.get('next_step') or ''),
        'verified_results: ' + json.dumps(data.get('verified_results', [])[-8:], ensure_ascii=False),
        'model_claims_unverified: ' + json.dumps(data.get('model_claims', [])[-3:], ensure_ascii=False),
    ]
    if data.get('destination_error'):
        lines.append('destination_error: ' + data['destination_error'])
    lines.append('A continuation such as Procedi keeps this original request. Do not recreate the project or repeat an activation listed above.')
    return '\n'.join(lines)[:4000]
