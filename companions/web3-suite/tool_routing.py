"""Small bilingual routing hints; selects schemas, never invents arguments or actions."""
import re
import unicodedata


def normalize(text):
    return ''.join(c for c in unicodedata.normalize('NFKD',text.casefold()) if not unicodedata.combining(c))


RULES=[
 (r'\b(h24|24/7|trading service|servizio trading|trading persistente|monitor mcc|breakout|pullback|retest|ingresso condizionato)\b',{'hyperliquid_service','hyperliquid_monitor','hyperliquid_status','hyperliquid_read','hyperliquid_analyze'}),
 (r'\b(solana|solders|spl|token.?2022|jupiter|raydium|metaplex|solana agent kit|helius)\b',{'solana_status','solana_read','solana_prepare_transfer','solana_simulate','solana_operations','solana_panel','solana_sdk_catalog','helius_status','helius_read'}),
 (r'\b(hyperliquid|hypercore|wallet master)\b',{'hyperliquid_status','hyperliquid_read','hyperliquid_preview','hyperliquid_submit','hyperliquid_operations','hyperliquid_select','hyperliquid_strategy'}),
 (r'\b(strategi\w*|regole operative|versioned rules|migrazion\w*|migration|dipendenze.*freellmapi|aggiorna.*moduli)\b',{'coordinator_state','strategy_status','strategy_propose','strategy_evaluate','strategy_activate','strategy_rollback','repair_start','repair_read','repair_edit','repair_migrate_state','repair_dependencies'}),
 (r'\b(?:benchmark|valuta.*modell\w*|confronta.*modell\w*|(?:bug|debug|correggi|ripara).*freellmapi|create.*tool)\b',{'coordinator_state','model_benchmark','coordinator_delegate','repair_start','repair_read','repair_edit','repair_verify','repair_activate','repair_status','generated_tool_build','generated_tool_list','generated_tool_run'}),
 (r'\b(automiglior\w*|self.improv\w*|self.repair|coordin\w* modelli|correggi.*tuoi bug|crea.*strument\w*|crearti.*strument\w*)\b',{'coordinator_state','model_benchmark','coordinator_delegate','repair_start','repair_edit','repair_verify','repair_activate','repair_rollback','repair_status','generated_tool_build','generated_tool_list','generated_tool_run'}),
 (r'\b(twitter|tweet|tweets|typefully|xquik|post su x|profilo x|account x|bozza|bozze|thread per|social draft|su x|on x)\b|x\.com/',{'social_status','x_search','x_post','x_profile','social_drafts','social_save_draft','social_schedule','social_publish','social_cancel'}),
 (r'\b(?:assistenti|subagenti|agents|assistants)\b.*\b(?:parallelo|parallel)\b',{'agent_start','agent_wait','agent_result','agent_status'}),
 (r'\b(?:pagina locale|html|campo|pulsante)\b.*\b(?:inserisci|premi|compila|clicca|conferma)\b',{'browser_navigate','browser_snapshot','browser_click','browser_fill'}),
 (r'\b(?:nota|note|risorsa|risorse)\b.*\b(?:esposta|esposte|server|mcp)\b',{'mcp_resources','mcp_resource_read','connections_status'}),
 (r'\b(pdf|fattura\.pdf)\b',{'pdf_read','image_read'}),
 (r'\b(notebook|quaderno|ipynb|cella|celle)\b',{'notebook_read','notebook_edit'}),
 (r'\b(sqlite|database|query|banca dati)\b',{'sqlite_query','read_file'}),
 (r'\b(in background|senza bloccare|processo|processi)\b',{'process_start','process_read','process_list','process_stop'}),
 (r'\b(attivita persistenti|lista (?:delle )?attivita|task|dipendenz\w*|da fare|to.do)\b',{'task_create','task_list','task_get','task_update'}),
 (r'\b(checkpoint|ripristin\w*|annulla (?:la |l[’\x27])?(?:mia |tua )?ultima modifica)\b',{'file_checkpoints','file_rewind'}),
 (r'\b(errori? di tipo|definizione (?:della |di una )?funzione|server del linguaggio|lsp|riferimenti (?:alla |di una )?funzione)\b',{'lsp_start','lsp_query','lsp_close'}),
 (r'\b(espressione regolare|regex|ripgrep)\b',{'grep'}),
 (r'\b(ogni \d+ (?:minuti|secondi)|fra \d+ (?:minuti|secondi)|pianifica|programma un controllo)\b',{'schedule_create','schedule_list','schedule_delete'}),
 (r'\b(coppia|quotazione|prezzo|candele)\b.*\b\w+\s*[/_-]\s*(?:usdc|usdt|usd|eur)\b',{'market_snapshot'}),
 (r'\b(apri|visita|naviga|compila|clicca)\b.*\b(chrome|browser|pagina|sito)\b',{'browser_navigate','browser_snapshot','browser_click','browser_fill'}),
 (r'\b(dispositivo|seeker|adb|logcat|apk)\b',{'seeker_devices','seeker_packages','seeker_logs','seeker_crashes','seeker_install','seeker_launch'}),
 (r'\b(risors\w*|note esposte)\b.*\b(mcp|server)\b',{'mcp_resources','mcp_resource_read','connections_status'}),
]


def suggested(prompt):
    text=normalize(prompt); result=set()
    from current_information import request as current_request
    if current_request(prompt):result.update({'web_search','fetch_url'})
    for pattern,names in RULES:
        if re.search(pattern,text): result.update(names)
    if re.search(r'pump[.\s_-]?fun|pumpswap|bonding.?curve',text):
        import pump_tools
        return {n for n in result if not n.startswith('hyperliquid_') and n not in ('market_snapshot','solana_prepare_transfer')}|pump_tools.NAMES|{'solana_status','solana_read'}
    from market_routing import intent
    market=intent(prompt)
    if market:
        result.add('market_snapshot')
        if market['trading'] or market['venue']=='hyperliquid':result.update({'hyperliquid_status','hyperliquid_read','hyperliquid_analyze','hyperliquid_preflight','hyperliquid_execute_plan','hyperliquid_preview','hyperliquid_submit','hyperliquid_operations','hyperliquid_select','hyperliquid_strategy'})
    return result


def simple_native_request(prompt):
    text=normalize(prompt)
    if len(re.findall(r'\w+',text))<3: return False
    if re.search(r'\bskill\b',text): return False
    if suggested(prompt)&{'coordinator_state'}: return True
    if suggested(prompt)&{'x_search','social_save_draft'}: return True
    if re.search(r'\b(progetta|crea|sviluppa|implementa|costruisci|build|design)\b',text) and not suggested(prompt)&{'task_create','schedule_create','process_start'}: return False
    return bool(suggested(prompt)) or bool(re.search(r'\b(test\w*|bug)\b.*\b(fallisc\w*|corregg\w*|ripara\w*)|\b(correggi|ripara)\b.*\b(bug|test|errore)\b',text))


def search_score(function,query):
    terms=re.findall(r'\w+',normalize(query)); name=function['name']
    body=normalize(name+' '+function.get('description',''))
    score=sum(term in body for term in terms)
    if name in suggested(query): score+=20
    if normalize(name)==normalize(query.strip()): score+=100
    if normalize(function.get('description','').strip())==normalize(query.strip()): score+=1000
    return score
