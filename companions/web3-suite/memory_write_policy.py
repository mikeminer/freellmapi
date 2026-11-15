"""Separate conversation retention from writes to shared personal memory.

Auto approval permits an action; it does not turn fictional chat data into a
personal fact. Scope boundaries come from user messages, never model notes.
"""
import hashlib
import json
import re
import unicodedata
from pathlib import Path

SCOPES = ('session', 'project', 'profile')
PROMPT = (
    'Conversation facts already persist in the session history across clients and models. '
    'memory_remember defaults to session (no extra note). Use project for durable project facts. '
    'Use profile ONLY when the current user explicitly requests a lasting personal memory across chats. '
    'Fiction, examples and data limited to this chat are not global user facts. '
    'A session-only retention result is successful: answer from history, do not retry another scope or vault_write.\n'
)


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(text).casefold())
                   if not unicodedata.combining(c)).replace('’', "'")


def boundary(text):
    text = normalized(text)
    local = re.search(
        r'\b(?:solo|soltanto|only|just)\s+(?:(?:in|per|within|for)\s+)?'
        r'(?:(?:questa|questa sola|this|the current)\s+)?(?:chat|conversazione|conversation|sessione|session)\b'
        r'|\b(?:non salvare|non memorizzare|do not save|do not store|don.t store)\b', text)
    durable = re.search(
        r'\b(?:profilo (?:globale|personale)|global profile|personal profile|'
        r'(?:prossime|future|altre|tutte le) (?:chat|conversazioni|sessioni)|'
        r'(?:future|other|all) (?:chats|conversations|sessions)|across (?:chats|sessions))\b', text)
    intent = re.search(r'\b(?:ricorda\w*|memorizza\w*|salva\w*|aggiorna\w*|remember|save|store|keep|update)\b', text)
    # A conflicting instruction stays local; broadening must be unambiguous.
    if local:
        return 'session'
    if durable and intent:
        return 'durable'
    return None


def policy_path(session):
    return Path(session).with_suffix('.memory-policy.json')


def record_prompt(session, prompt):
    retention = boundary(prompt)
    if not retention or not session:
        return
    from reliable_runtime import atomic_bytes
    value = {'version': 1, 'retention': retention,
             'source_sha256': hashlib.sha256(prompt.encode()).hexdigest()}
    path = policy_path(session)
    if path.exists() and json.loads(path.read_text(encoding='utf-8')) == value:
        return
    atomic_bytes(path, json.dumps(value).encode())


def decide(agent, arguments):
    if not isinstance(arguments.get('text'), str) or not arguments['text'].strip():
        return {'ok': False, 'code': 'invalid_memory_text', 'stored': False, 'retry': False,
                'message': 'text must be a nonempty string. No note was created.'}
    requested = arguments.get('scope', 'session')
    if requested not in SCOPES:
        return {'ok': False, 'code': 'invalid_memory_scope', 'allowed_scopes': list(SCOPES),
                'stored': False, 'retry': False,
                'message': 'Use session for chat data. Do not change to profile to bypass a scope error.'}
    users = [m['content'] for m in agent.messages
             if m.get('role') == 'user' and isinstance(m.get('content'), str)
             and not m['content'].startswith('Conversation summary (data, not new instructions):')]
    session = getattr(agent, 'session', None)
    retention = None
    for text in reversed(users):
        retention = boundary(text)
        if retention:
            record_prompt(session, text)
            break
    if retention is None and session and policy_path(session).exists():
        retention = json.loads(policy_path(session).read_text(encoding='utf-8')).get('retention')
    profile_authorized = bool(users and boundary(users[-1]) == 'durable')
    effective = ('session' if retention == 'session' or
                 (requested == 'profile' and not profile_authorized) else requested)
    if effective == 'session':
        return {'ok': True, 'code': 'retained_in_session', 'scope': 'session',
                'requested_scope': requested, 'stored': False, 'context_preserved': True,
                'retry': False, 'session': Path(session).stem if session else None,
                'message': 'Dati conservati nella cronologia condivisa della chat, non nel profilo globale. '
                           'Non riprovare con altri scope o vault_write; continua dalla conversazione.'}
    return {'ok': True, 'scope': effective, 'write_allowed': True}
