"""Keyless public search with explicit failure reporting, never invented results."""
from html.parser import HTMLParser
import urllib.request
from urllib.parse import urlencode,urlsplit,parse_qs
import xml.etree.ElementTree as ET
import re
from datetime import datetime,timezone

STOP_WORDS=set('the and for with from this that what how today latest news skill skills github signal signals trading oggi ultime ultimo notizie segnale segnali dammi per della delle dello degli sono come cosa una con sul sulla delle'.split())

def query_terms(query):
    return {w for w in re.findall(r'\w{3,}',query.casefold()) if w not in STOP_WORDS and not w.isdigit()}

def relevant(item,query):
    terms=query_terms(query)
    if not terms: return True
    text=' '.join(str(item.get(k,'')) for k in ('title','url','snippet')).casefold()
    words=set(re.findall(r'\w+',text))
    return len(terms & words)>=min(2,len(terms))

class Results(HTMLParser):
    def __init__(self): super().__init__(); self.items=[]; self.current=None
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='a' and 'result__a' in a.get('class','').split():
            url=a.get('href','')
            if url.startswith('//'): url='https:'+url
            url=parse_qs(urlsplit(url).query).get('uddg',[url])[0]
            if url.startswith(('https://','http://')): self.current={'title':'','url':url}
    def handle_data(self,data):
        if self.current is not None: self.current['title']+=data
    def handle_endtag(self,tag):
        if tag=='a' and self.current is not None:
            self.items.append(self.current); self.current=None

def fetch(url):
    req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) freellmapi/2'})
    with urllib.request.urlopen(req,timeout=20) as r:
        data=r.read(2_000_000).decode('utf-8',errors='replace')
        if r.status==202: raise RuntimeError('Il motore richiede una verifica anti-bot.')
        return data

def search(query,limit=6,news=False):
    if not query.strip(): raise ValueError('Inserisci una ricerca.')
    if len(query)>1000: raise ValueError('Query troppo lunga.')
    errors=[]
    for engine in (('Google News RSS','DuckDuckGo','Bing RSS') if news else ('DuckDuckGo','Bing RSS')):
        try:
            if engine=='Google News RSS':
                page=fetch('https://news.google.com/rss/search?'+urlencode({'q':query+' when:7d','hl':'it','gl':'IT','ceid':'IT:it'}))
                items=[{'title':i.findtext('title',''),'url':i.findtext('link',''),'snippet':re.sub('<[^>]+>',' ',i.findtext('description','')),'published':i.findtext('pubDate',''),'publisher':i.findtext('source','')} for i in ET.fromstring(page).findall('./channel/item')]
            elif engine=='DuckDuckGo':
                page=fetch('https://html.duckduckgo.com/html/?'+urlencode({'q':query}))
                parser=Results(); parser.feed(page); items=parser.items
                if 'anomaly.js' in page or 'challenge-form' in page: raise RuntimeError('Verifica anti-bot richiesta.')
            else:
                page=fetch('https://www.bing.com/search?'+urlencode({'q':query,'format':'rss'}))
                items=[{'title':i.findtext('title',''),'url':i.findtext('link',''),'snippet':i.findtext('description','')} for i in ET.fromstring(page).findall('./channel/item')]
            candidates=[i for i in items if i['url'].startswith(('https://','http://'))]
            items=[i for i in candidates if relevant(i,query)][:limit]
            if candidates and not items: errors.append(engine+': risultati scartati perché fuori tema rispetto alla query')
            if items: return {'query':query,'engine':engine,'results':items,'warnings':errors,'retrieved_utc':datetime.now(timezone.utc).isoformat()}
            errors.append(engine+': nessun risultato restituito')
        except Exception as e: errors.append(engine+': '+str(e))
    return {'query':query,'results':[],'errors':errors,'message':'Ricerca non riuscita o senza risultati. Non equivale a provare che la risorsa non esista.'}
