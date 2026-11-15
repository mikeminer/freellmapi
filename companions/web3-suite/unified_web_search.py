"""Fornai-compatible unified search; existing API credentials, bounded provider failover."""
import json,time,urllib.request,urllib.error
from datetime import datetime,timezone
from urllib.parse import urlsplit

ENGINES=('tavily-search','exa-search','firecrawl-search')

class SearchFailure(RuntimeError):
    def __init__(self,status=None,code=None):
        self.status=status;self.code=code
        super().__init__('HTTP '+str(status)+((' · '+code) if code else '') if status else 'Timeout o connessione non disponibile')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):return None

class SearchGateway:
    def __init__(self,base,token):
        self.base=base.rstrip('/').removesuffix('/v1');self.token=token
        self.catalog=None;self.catalog_until=0;self.cooldowns={}

    def _call(self,path,data,timeout):
        req=urllib.request.Request(self.base+'/v1/'+path,data=json.dumps(data).encode() if data is not None else None,
            headers={'Authorization':'Bearer '+self.token,'Accept':'application/json','Content-Type':'application/json'})
        try:
            with urllib.request.build_opener(NoRedirect(),urllib.request.ProxyHandler({})).open(req,timeout=timeout) as response:
                body=response.read(2_000_001)
                if len(body)>2_000_000:raise ValueError('Risposta ricerca oltre 2 MB')
                return json.loads(body)
        except urllib.error.HTTPError as error:
            # Provider bodies may contain credentials; expose only bounded codes.
            code=None
            try:
                value=json.loads(error.read(12000));candidate=(value.get('error') or {}).get('code')
                if isinstance(candidate,str) and len(candidate)<80 and all(c.isalnum() or c in '_-.' for c in candidate):code=candidate
            except (ValueError,AttributeError,TypeError):pass
            finally:error.close()
            raise SearchFailure(error.code,code) from None
        except (urllib.error.URLError,TimeoutError,ConnectionError):raise SearchFailure() from None

    def search(self,query,limit=6,news=False):
        if not isinstance(query,str) or not query.strip() or len(query)>1000:raise ValueError('Query non valida (1–1000 caratteri).')
        deadline=time.monotonic()+20;warnings=[];attempted=[]
        def call(path,data=None):
            remaining=deadline-time.monotonic()
            if remaining<=0:raise SearchFailure()
            return self._call(path,data,min(8,remaining))
        try:
            if self.catalog is None or self.catalog_until<=time.monotonic():
                response=call('search/engines')
                if not isinstance(response,dict) or not isinstance(response.get('data'),list):raise ValueError('Catalogo motori non valido')
                self.catalog={x.get('id') for x in response['data'] if isinstance(x,dict) and x.get('available') is True};self.catalog_until=time.monotonic()+60
        except (SearchFailure,ValueError,TypeError) as error:
            return {'query':query,'results':[],'errors':['FreeLLMAPI search/engines: '+str(error)],'attempted_engines':[]}
        for engine in ENGINES:
            if engine not in self.catalog or self.cooldowns.get(engine,0)>time.monotonic():continue
            attempted.append(engine)
            try:
                data=call('search',{'query':query,'max_results':min(6,max(1,int(limit))),'include_answer':False,'topic':'news' if news else 'general','engine':engine})
                if not isinstance(data,dict) or data.get('engine')!=engine or not isinstance(data.get('results'),list):raise ValueError('Risposta o motore ricerca non corrispondente')
                items=[]
                for row in data['results'][:6]:
                    if not isinstance(row,dict):continue
                    href=row.get('url');parsed=urlsplit(href) if isinstance(href,str) else None
                    if not parsed or parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or any(c in href for c in '\r\n') or len(href)>2000:continue
                    content=row.get('content') or row.get('snippet') or row.get('description') or ''
                    item={'url':href,'title':str(row.get('title') or parsed.hostname)[:300],'snippet':str(content)[:4000]}
                    published=row.get('published_date') or row.get('publishedDate') or row.get('date')
                    if published:item['published']=str(published)[:100]
                    items.append(item)
                if items:return {'query':query,'engine':engine,'provider':data.get('provider'),'results':items[:limit],'warnings':warnings,'attempted_engines':attempted,'retrieved_utc':datetime.now(timezone.utc).isoformat()}
                warnings.append(engine+': nessun risultato utilizzabile')
            except (SearchFailure,ValueError,TypeError) as error:
                warnings.append(engine+': '+str(error));self.cooldowns[engine]=time.monotonic()+60
        return {'query':query,'results':[],'errors':warnings or ['Nessun motore unificato disponibile; uso il fallback pubblico.'],'attempted_engines':attempted}
