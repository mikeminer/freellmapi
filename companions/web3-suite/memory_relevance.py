"""Conservative retrieval policy shared by lexical ranking and prompt injection."""
import re
from collections import Counter

MIN_SEMANTIC_SCORE=0.65
MAX_TOP_GAP=0.04

def content_tokens(text):
    # Project IDs, timestamps and source labels describe provenance, not subject matter.
    text=re.sub(r'(?m)^\s*(?:#.*|Data(?: UTC)?\s*:.*|Progetto\s*:.*|Percorso\s*:.*|Fonte\s*:.*|Contesto\s*:.*|Sessione\s*:.*|Turno\s*:.*|Archiviato UTC\s*:.*|Attore\s*:.*|Strumento\s*:.*)\s*$', '',text)
    return Counter(re.findall(r'\w+',text.casefold()))

def lexical_score(query,text,project_name=''):
    from web_search import query_terms
    words=query_terms(query)
    words-=set(re.findall(r'\w+',str(project_name).casefold()))
    tokens=content_tokens(text)
    matched=words & tokens.keys()
    if len(matched)<min(2,len(words)) or not words:return 0
    return sum(min(tokens[word],3) for word in matched)

def select_semantic(rows):
    """Scores are similarity values, not probabilities; abstention is intentional."""
    if not rows:return []
    top=max(row['score'] for row in rows)
    cutoff=max(MIN_SEMANTIC_SCORE,top-MAX_TOP_GAP)
    seen=set();selected=[]
    for row in sorted(rows,key=lambda r:r['score'],reverse=True):
        if row['score']<cutoff or row['path'] in seen:continue
        selected.append(row);seen.add(row['path'])
    return selected
