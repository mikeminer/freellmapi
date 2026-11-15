"""One native question at a time, with an explicit submitted answer."""
import os
import re
import sys
import json
import terminal_ui as ui

def question_key(question):
    return ' '.join(re.findall(r'\w+',normalize_question(question).casefold()))

def answered_questions(messages):
    answers={}
    calls={c['id']:c.get('function',{}) for m in messages for c in m.get('tool_calls',[])}
    for m in messages:
        if m.get('role')!='tool' or calls.get(m.get('tool_call_id'),{}).get('name')!='ask_user': continue
        try:
            result=json.loads(m.get('content',''))
            if result.get('cancelled') or result.get('superseded') or not isinstance(result.get('question'),str) or not isinstance(result.get('answer'),str): continue
            answers[question_key(result['question'])]=result
        except (ValueError,TypeError,AttributeError): continue
    return answers

def wants_questionnaire(text):
    if re.search(r'\b(?:quiz|test)\b',text,re.I) and re.search(r'sottopon|fammi|ponimi|chiedimi|ask me|una domanda',text,re.I): return True
    return bool(re.search(r'\b(fammi|fai|ponimi|chiedimi|ask me)\b',text,re.I) and
                re.search(r'domand|question|frecce|arrow',text,re.I)) or bool(re.search(r'una domanda (?:alla|per) volta|one question at a time',text,re.I))


def prose_choice(text):
    """Hold a real choice prompt for the menu. A finished answer that only contains one question is delivered."""
    if not isinstance(text,str): return False
    text=re.sub(r'```[\s\S]*?```','',text)
    text=re.sub(r'`[^`]*`|https?://\S+','',text)
    text=re.sub(r'(?m)^\s*>.*$','',text)
    if not text.strip(): return False
    if re.search(r'\b(scegli|seleziona|preferisci|vuoi|dimmi (?:quale|se|le tue)|'
                 r'fammi sapere (?:le tue|quale|se)|rispondi (?:con|alle)|'
                 r'choose|select|do you prefer|let me know (?:which|your)|would you like)\b',text,re.I):
        return True
    if interrogative_count(text)>1: return True
    compact=' '.join(text.split())
    return interrogative_count(text)==1 and len(compact)<=240 and compact.endswith('?')

def explicit_choice(text):
    """Convert one explicit A-D/1-4 choice, without inventing options or answers."""
    if not isinstance(text,str) or '```' in text: return None
    matches=list(re.finditer(r'(?m)^\s*(?:[-*]\s+)?(?:\*\*)?([A-Ha-h]|[1-8])[.)](?:\*\*)?\s+(.+?)\s*$',text))
    if not 2<=len(matches)<=8: return None
    labels=[m[1].upper() for m in matches]
    if labels not in [list('ABCDEFGH'[:len(matches)]),list('12345678'[:len(matches)])]: return None
    # Reject explanatory text between options, multiple questions, or a revealed answer.
    if any(text[a.end():b.start()].strip() for a,b in zip(matches,matches[1:])): return None
    suffix=text[matches[-1].end():].strip()
    if suffix and not re.fullmatch(r'(?i)(?:scegli|seleziona|rispondi|choose|select)[^\n?.!]*[?.!]?',suffix): return None
    question=text[:matches[0].start()].strip()
    question=re.sub(r'(?m)^\s*#{1,6}\s*','',question)
    options=[m[2].strip() for m in matches]
    try: validate_question(question,options)
    except ValueError: return None
    if '?' not in question: return None
    return {'question':question,'options':options}


# A lone "?" between separators is a blank in a sequence (24, ?, 96), not another question.
_PLACEHOLDER_MARK=re.compile(r'(?:(?<=^)|(?<=[\s,;:]))\?(?=$|[\s,;:])')

def interrogative_count(question):
    return _PLACEHOLDER_MARK.sub('',question).count('?')

def validate_question(question,options):
    if not isinstance(question,str) or not question.strip() or len(question)>1200:
        raise ValueError('Domanda non valida: usa una stringa non vuota di massimo 1200 caratteri.')
    if interrogative_count(question)>1 or re.search(r'(?m)^\s*(?:[-*]|\d+[.)])\s+',question):
        raise ValueError('Una sola domanda per chiamata, senza elenchi di domande. Chiedi solo la prima scelta mancante e attendi la risposta.')
    if not isinstance(options,list) or not 2<=len(options)<=8 or any(not isinstance(o,str) or not o.strip() or len(o)>300 for o in options):
        raise ValueError('Servono da 2 a 8 opzioni testuali non vuote, massimo 300 caratteri ciascuna.')
    if len(set(o.strip().casefold() for o in options))!=len(options):
        raise ValueError('Le opzioni devono essere distinte.')
    if any(re.match(r'^(altro|altra risposta|other|risposta libera)(?:\b|:)',o.strip(),re.I) for o in options):
        raise ValueError('Non inserire Altro nelle opzioni: la risposta libera viene aggiunta automaticamente.')


def normalize_question(question):
    """Collapse only a redundant clarification preamble, not independent decisions."""
    if not isinstance(question,str): return question
    first,sep,rest=question.partition('?')
    if (sep and rest.strip() and question.count('?')==2 and
            re.match(r'^\s*(?:cosa intendi|che cosa intendi|what do you mean)\b',first,re.I) and
            re.match(r'^\s*(?:vuoi|intendi|stai cercando|do you mean|are you looking)\b',rest,re.I) and
            not re.search(r'\n\s*(?:[-*]|\d+[.)])\s',question)):
        return first.strip()+'?'
    return question

def first_question_text(question):
    """Keep one question, including a sequence blank. Drop only a later real question."""
    marks=[i for i,ch in enumerate(question) if ch=='?' and i and question[i-1].isalpha()]
    if len(marks)<2: return question.strip()
    rest=question[marks[0]+1:]
    if not re.search(r'[^\W\d_]\?',rest): return question.strip()
    return question[:marks[0]+1].strip()

def salvage_question(question,options):
    """Repair a malformed choice into one ask, or raise if it still cannot be asked."""
    if isinstance(question,str):
        question=normalize_question(question)
        if interrogative_count(question)>1: question=first_question_text(question)
        if re.search(r'(?m)^\s*(?:[-*]|\d+[.)])\s+',question):
            extracted=explicit_choice(question)
            if extracted: question,options=extracted['question'],extracted['options']
            else:
                stripped='\n'.join(line for line in question.splitlines() if not re.match(r'^\s*(?:[-*]|\d+[.)])\s+',line)).strip()
                if stripped: question=stripped
    validate_question(question,options)
    return question,options

def ask(question,options):
    question,options=salvage_question(question,options)
    from remote_access import active
    remote=active()
    if remote and remote.owner:return remote.ask(question,options)
    choices=options+['Altro: scrivo la mia risposta']; selected=0
    if ui.fancy() and os.name=='nt' and sys.stdin.isatty():
        import msvcrt
        from rich.live import Live
        from rich.panel import Panel
        from rich.text import Text
        def render():
            text=Text(question+'\n\n',style='bold white')
            for i,label in enumerate(choices): text.append(f"{'❯' if selected==i else ' '} {label}\n",style='bold #60d5ff' if selected==i else '#b4becb')
            text.append('\n↑ ↓ scegli · Invio conferma · Esc annulla',style='dim')
            return Panel(text,title='freellmapi · una domanda alla volta',border_style='#b7a1ff')
        with Live(render(),console=ui.console,auto_refresh=False,transient=True) as live:
            while True:
                key=msvcrt.getwch()
                if key in ('\xe0','\x00'):
                    code=msvcrt.getwch()
                    if code=='H': selected=(selected-1)%len(choices)
                    elif code=='P': selected=(selected+1)%len(choices)
                elif key=='\r': break
                elif key=='\x1b': return {'question':question,'cancelled':True}
                elif key=='\x03': raise KeyboardInterrupt
                live.update(render(),refresh=True)
    else:
        ui.write(question)
        for i,label in enumerate(choices,1): ui.write(f'{i}. {label}')
        while True:
            value=input('Numero (Invio annulla): ').strip()
            if not value: return {'question':question,'cancelled':True}
            if value.isdigit() and 1<=int(value)<=len(choices): selected=int(value)-1; break
    answer=choices[selected]
    if selected==len(options):
        answer=input('La tua risposta (Invio annulla): ').strip()
        if not answer: return {'question':question,'cancelled':True}
    ui.write(question+'\n  Risposta: '+answer)
    return {'question':question,'answer':answer,'cancelled':False}
