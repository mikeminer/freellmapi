"""Detect draft tasks and require evidence of a local save before claiming completion."""
import json
import re

def requested(prompt,messages):
    if re.search(r"\b(?:non salvare|senza salvare|solo testo|text only|do not save|don't save)\b",prompt,re.I): return False
    actions=re.sub(r"\b(?:non pubblicare|senza pubblicare|do not publish|don't publish)\b",'',prompt,flags=re.I)
    if re.search(r'\b(?:pubblica|publish|schedule|programma|annulla|cancel)\b',actions,re.I): return False
    social=r'\b(?:twitter|tweets?|account\s+x|su\s+x|on\s+x)\b|(?<!\S)@[A-Za-z0-9_]{1,15}\b'
    if re.search(social,prompt,re.I) and re.search(r'\b(?:prepara|scrivi|salv\w*|crea|write|save|draft|prepare|modifica|aggiorna)\b',prompt,re.I): return True
    if re.search(r'\b(?:english|inglese|traduc\w*|translate|aggiungi|add|modifica|rewrite|riscrivi)\b',prompt,re.I):
        return any(m.get('role')=='user' and re.search(social,str(m.get('content','')),re.I) for m in messages[-20:])
    return False

def clarification(text):
    return bool(re.search(r'\b(?:vuoi che|preferisci|quale (?:account|tono|lingua)|do you want (?:me|to include)|would you like me|which (?:account|tone|language))\b',text or '',re.I))

def saved(messages,start,home):
    from social_tools import drafts
    calls={}
    for message in messages[start:]:
        for call in message.get('tool_calls',[]):
            if call.get('function',{}).get('name')=='social_save_draft': calls[call['id']]=call['function']
    for message in reversed(messages[start:]):
        if message.get('role')!='tool' or message.get('tool_call_id') not in calls: continue
        try:
            result=json.loads(message['content']); args=json.loads(calls[message['tool_call_id']]['arguments'])
            if not isinstance(result,dict) or 'id' not in result: continue
            row=drafts(home,result['id'])
            if row['text']==args['text'] and row['account']==args['account'].removeprefix('@') and row['state']=='draft': return row
        except (ValueError,KeyError,TypeError): continue
    return None

def render(draft):
    return 'Bozza locale salvata e verificata · @'+draft['account']+' · ID: '+draft['id']+'\nNessuna pubblicazione eseguita.\n\n'+draft['text']
