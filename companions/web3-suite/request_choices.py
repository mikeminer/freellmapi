"""Conservative, source-backed build choices; never infer consent from memory."""
import re
import unicodedata


def norm(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(text).lower()) if not unicodedata.combining(c))


def extract(text, previous=None):
    facts = dict(previous or {})
    text = str(text or '')
    value = norm(text)

    def put(key, answer, evidence=None):
        facts[key] = {'answer': answer, 'evidence': (evidence or text)[:900], 'source': 'explicit_user_request'}

    if re.search(r'\b(?:per|for|target|piattaforma|platform).*?\bseeker\b', value) or re.search(r'\b(?:app|apk)\b.*?\bseeker\b', value):
        put('platform', 'Seeker Android')
    if re.search(r'(?:gioco|game|risorse|assets).{0,65}(?:inclus|includ|bundl|local).{0,30}(?:apk|locale)|\bbundled(?:-only)?\b', value):
        put('apk_architecture', 'Gioco e risorse inclusi nell’APK (bundled, tutto in locale)')
    elif re.search(r'apk.{0,35}(?:caric|loads?).{0,25}(?:sito|website|https)', value):
        put('apk_architecture', 'APK che carica il sito web (hosted)')
    if re.search(r'\b(?:senza|no|without|nessun[ae]?)\b.{0,50}\b(?:promozion|promotion|pubblicit|ads?\b)', value):
        for key in ('free_play_promo', 'free_play_banner'):
            put(key, 'No, nessuna promozione aggiuntiva')
    if re.search(r'pagamenti\s+disattivat|payments?\s+(?:off|disabled)|senza\s+(?:acquisti|pagamenti)|no\s+payments', value):
        put('payments', 'Pagamenti disattivati, solo sviluppo libero')
    if re.search(r'\b(?:senza|no|without|nessun)\b.{0,15}(?:nuov[oi]\s+)?market\b', value):
        put('market', 'No, nessun market aggiuntivo')
    if re.search(r'(?:decidi|scegli)\s+tu|(?:you\s+)?decide\s+(?:the|all)|dettagli.{0,15}delegat', value):
        put('delegated_details', 'Dettagli reversibili delegati all’agente')
    match = re.search(r'(?i)\b(?:usa|utilizza|use)\s+([1-9A-HJ-NP-Za-km-z]{32,44})\s+(?:come\s+|as\s+)?token\b', text)
    if match:
        put('token_mint', match[1], match[0])
    if re.search(r'\bmainnet\b', value):
        put('network', 'Solana mainnet; RPC pubblico se non ne è stato indicato uno privato')
    return facts


def topic(question, options):
    q = norm(question)
    choices = norm(' '.join(options))
    if 'game room' in q or 'game-room' in q:
        return 'game_room'
    if re.search(r'intro|introduzion', q):
        return 'intro'
    if re.search(r'banner|scan|your ad here|card|sponsor', q):
        return 'free_play_banner'
    if re.search(r'promozion|promotion|pubblicit', q):
        return 'free_play_promo'
    if re.search(r'market|mercato in.game', q):
        return 'market'
    if (re.search(r'apk|risorse|assets|hosted|bundled', q) and
            re.search(r'sito|website|hosted|includ|bundl|locale', q + ' ' + choices)):
        return 'apk_architecture'
    if re.search(r'piattaforma|platform', q) and re.search(r'browser|seeker|android', choices):
        return 'platform'
    if re.search(r'\brpc\b', q):
        return 'network'
    if re.search(r'token|\bmint\b|contract address|\bca\b', q) and not re.search(r'prezz|price|amount|quant|sogli', q):
        return 'token_mint'
    if re.search(r'pass|abbonament|subscription|pagament|payment', q) and not re.search(r'wallet|key|chiav|password', q):
        return 'payments'
    return None


def resolve(question, options, facts, authorized_delivery=False):
    q=norm(question)
    confirmation=bool(re.search(r'(?:vuoi|posso|possiamo|do you want|shall i|can i).{0,35}(?:proced|continu|prosegu|iniziar|start|proceed|continue)',q))
    sensitive=bool(re.search(r'acquist|pagament|spend|purchase|buy|transaz|transaction|pubblic|publish|deploy|store|disinstall|uninstall|cancell|delet|formatt|wallet|chiav|secret|faucet|nuovo token|new token|mint',q))
    if confirmation and sensitive:
        return None
    if authorized_delivery and confirmation and not sensitive:
        return {'question':question,'answer':'Procedi con la consegna APK già richiesta, nella cartella corrente. Conserva il progetto e i risultati; riusa la base autorizzata e non ricominciare da zero.',
                'cancelled':False,'reused':True,'decision_key':'authorized_delivery',
                'source':'existing_user_task_authorization','evidence':'La richiesta corrente chiede già di completare la build e consegna APK.',
                'instruction':'Non chiedere nuovamente se iniziare o continuare. Le approvazioni degli strumenti restano gestite dal runtime.'}
    key = topic(question, options)
    fact = facts.get(key)
    if key == 'game_room' and facts.get('platform', {}).get('answer') == 'Seeker Android' and 'bundled' in facts.get('apk_architecture', {}).get('answer', ''):
        fact = {'answer': 'Non applicabile: solo Seeker con gioco incluso nell’APK, nessun gioco web da inserire nella Game Room.',
                'source': 'applicability_from_explicit_user_choices', 'evidence': facts['apk_architecture']['evidence']}
    if key == 'intro' and facts.get('delegated_details'):
        fact = {'answer': 'Nessuna intro aggiuntiva per questa build di sviluppo.', 'source': 'delegated_reversible_default',
                'evidence': facts['delegated_details']['evidence']}
    if not fact:
        return None
    return {'question': question, 'answer': fact['answer'], 'cancelled': False, 'reused': True,
            'decision_key': key, 'source': fact['source'], 'evidence': fact['evidence'],
            'instruction': 'Scelta già stabilita nella richiesta o non applicabile. Esegui il lavoro; non ripetere questa domanda. Non è un’autorizzazione ad acquisti o pubblicazioni.'}
