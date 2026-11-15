"""Lossless, deterministic consolidation with explicit provenance and project registry."""
import json,re
from pathlib import Path

def consolidate(vault):
    data=vault.operational.load();excluded=set(data['excluded'])
    all_claims=list(data['claims'])
    claims=[c for c in all_claims if not c.get('superseded_by') and not any(data['events'].get(s,{}).get('path') in excluded for s in c['sources'])]
    events=[e for e in data['events'].values() if e['path'] not in excluded]
    sections={'Obiettivi e vincoli':('objective','constraint'),'Decisioni e preferenze':('decision','preference'),'Stato e prossimi passi':('state','next_step'),'Problemi aperti':('issue',),'Verifiche dichiarate con fonti':('verified',)}
    lines=['# Memoria consolidata · '+vault.project.name,'','Progetto: `'+str(vault.project)+'`','Sintesi estrattiva automatica: non certifica interpretazioni del modello. Gli archivi originali restano intatti.']
    for title,kinds in sections.items():
        lines+=['','## '+title]
        seen=set(); wrote=False
        for c in reversed(claims):
            if c['kind'] not in kinds or c['text'] in seen: continue
            seen.add(c['text']); wrote=True
            refs=[data['events'][s]['path'][:-3] for s in c['sources'] if s in data['events']]
            lines.append('- '+c['text'][:700]+' — '+c['authority']+' '+ ' '.join('[['+r+']]' for r in refs))
        if title=='Problemi aperti' and not wrote: lines.append('- Nessun problema aperto registrato.')
        elif title=='Stato e prossimi passi' and not any(c['kind']=='next_step' for c in claims): lines.append('- Nessun prossimo passo registrato.')
        elif not wrote: lines.append('- Nessuna voce registrata.')
    # Recorded user answers are facts about the choice, not inferred preferences.
    lines+=['','## Scelte esplicite recenti']
    for e in [e for e in events if e['kind']=='decided'][-12:]: lines.append('- '+e['summary'][:400]+' [['+e['path'][:-3]+']]')
    lines+=['','## Ultime richieste (non completamenti)']
    for e in [e for e in events if e['kind']=='requested' and e['actor']=='main'][-3:]:lines.append('- '+e['summary'][:450]+' [['+e['path'][:-3]+']]')
    lines+=['','## Errori osservati recenti (non necessariamente ancora aperti)']
    for e in [e for e in events if e['kind']=='failed_or_denied'][-5:]:lines.append('- '+e['summary'][:300]+' [['+e['path'][:-3]+']]')
    lines+=['','## Storico sostituzioni (non è lo stato corrente)']
    replaced=[c for c in all_claims if c.get('superseded_by')]
    lines+=['- '+c['text'][:300]+' · sostituita da '+c['superseded_by']+' · '+c['authority'] for c in replaced] or ['- Nessuna sostituzione registrata.']
    path=vault.operational.folder/'Consolidated.md';text='\n'.join(lines)+'\n'
    if not path.exists() or path.read_text(encoding='utf-8')!=text: vault.atomic(path,text)
    return {'project':str(vault.project),'claims':len(claims),'events':len(events),'path':str(path),'originals_preserved':True}

def mentioned_projects(vault, text):
    rows=projects(vault); folded=str(text).casefold(); hits=[]
    for row in rows:
        root=str(row['root'])
        if root and root.casefold() in folded: hits.append(row); continue
        name=row['name']
        if len(name)<4: continue
        if re.search(r'(?<![\w.-])'+re.escape(name.casefold())+r'(?![\w.-])', folded): hits.append(row)
    unique=[]
    for row in hits:
        if row['id'] not in [item['id'] for item in unique]: unique.append(row)
    roots=[str(item['root']) for item in unique]
    kept=[]
    for row in unique:
        root=str(row['root']).casefold().rstrip('\\/')
        if any(other.casefold().rstrip('\\/').startswith(root+'\\') or other.casefold().rstrip('\\/').startswith(root+'/') for other in roots if other.casefold().rstrip('\\/')!=root):
            continue
        kept.append(row)
    return kept

def resolve_scope(vault, prompt, choose=None):
    hits=mentioned_projects(vault, prompt)
    active=vault.project_id
    if len(hits)<=1:
        chosen=hits[0] if hits else None
        if chosen is None or chosen['id']==active: return {'mode':'active','project_id':active,'ambiguous':False}
        return {'mode':'consult','project_id':chosen['id'],'root':chosen['root'],'name':chosen['name'],'ambiguous':False}
    labels=[f"{row['name']} · {row['root']}" for row in hits]
    answer=choose(labels) if choose else None
    selected=next((row for row in hits if answer==f"{row['name']} · {row['root']}"),None)
    if selected is None: return {'mode':'active','project_id':active,'ambiguous':True}
    if selected['id']==active: return {'mode':'active','project_id':active,'ambiguous':False}
    return {'mode':'consult','project_id':selected['id'],'root':selected['root'],'name':selected['name'],'ambiguous':False}

def bind_memory_scope(agent, prompt):
    """Choose which project memory to read. Never changes the working directory or the session."""
    memory=getattr(agent,'memory',None)
    if not memory or getattr(agent,'depth',0):
        scope={'mode':'active','project_id':getattr(memory,'project_id',None),'ambiguous':False}
    else:
        def choose(labels):
            if not getattr(agent,'interactive',False): return None
            import sys
            try:
                if not sys.stdin.isatty(): return None
            except (AttributeError,ValueError): return None
            from questions import ask
            result=ask('Quale memoria di progetto devo consultare? Directory e sessione restano invariate.', labels)
            if result.get('cancelled'): return None
            return result.get('answer')
        scope=resolve_scope(memory, prompt, choose=choose)
    agent._memory_scope=scope
    agent._memory_scope_for=prompt
    return scope

def projects(vault):
    rows=[]
    for path in (vault.path/'Projects').glob('*.md'):
        if not re.fullmatch('[a-f0-9]{16}',path.stem):continue
        text=path.read_text(encoding='utf-8');match=re.search(r'Percorso: `([^`]+)`',text)
        if match:rows.append({'id':path.stem,'name':Path(match[1]).name,'root':match[1],'active':path.stem==vault.project_id})
    return rows
