"""Read X directly in the existing persistent, visible browser. No API intermediary."""
import re
from datetime import datetime, timezone
from urllib.parse import urlencode

def target(name,args):
    limit=args.get('limit',10)
    if type(limit)!=int or not 1<=limit<=20: raise ValueError('limit deve essere tra 1 e 20.')
    if name=='x_search':
        query=args['query'].strip(); order=args.get('order','Latest')
        if not query or len(query)>1000 or order not in ('Latest','Top'): raise ValueError('Usa una query non vuota e order Latest oppure Top.')
        if args.get('cursor'): raise ValueError('La lettura browser non usa cursori API. Restringi la query con from:, since: o until:.')
        return 'https://x.com/search?'+urlencode({'q':query,'src':'typed_query','f':'live' if order=='Latest' else 'top'}),limit
    if name=='x_post':
        if not re.fullmatch(r'[0-9]+',str(args['id'])): raise ValueError('ID post non valido: usa cifre numeriche.')
        return 'https://x.com/i/status/'+str(args['id']),1
    username=args['username'].removeprefix('@')
    if not re.fullmatch(r'[A-Za-z0-9_]{1,15}',username): raise ValueError('Username X non valido.')
    return 'https://x.com/'+username,limit

EXTRACT = r'''() => ({
  url: location.href,
  title: document.title,
  text: document.body.innerText.slice(0,12000),
  posts: Array.from(document.querySelectorAll('article[data-testid="tweet"]')).map(el => {
    const time=el.querySelector('time');
    const link=time && time.closest('a');
    return {url:link ? link.href : null, timestamp:time ? time.getAttribute('datetime') : null,
      text:el.querySelector('[data-testid="tweetText"]')?.innerText || '',
      author:el.querySelector('[data-testid="User-Name"]')?.innerText || '',
      visible_metrics:el.querySelector('[role="group"]')?.getAttribute('aria-label') || '',
      context:el.innerText.slice(0,1500)};
  })
})'''

def read(agent,name,args):
    url,limit=target(name,args)
    if not agent.interactive or getattr(agent,'depth',0):
        return {'status':'main_session_required','next':'Usa lo strumento nella sessione principale interattiva.'}
    browser=agent.browser; browser.start()
    page=browser.page; browser.refs={}
    try:
        page.goto(url,wait_until='domcontentloaded',timeout=30000)
        try: page.locator('article[data-testid="tweet"], input[autocomplete="username"], [data-testid="emptyState"]').first.wait_for(state='visible',timeout=12000)
        except Exception: pass
        posts={}; observation={}
        for step in range(3):
            observation=page.evaluate(EXTRACT)
            for post in observation['posts']:
                if post.get('url') and re.match(r'https://(?:www\.)?x\.com/[^/]+/status/\d+',post['url']):
                    if name=='x_post' and post['url'].split('/status/')[-1].split('?')[0]!=str(args['id']): continue
                    posts[post['url']]=post
            if len(posts)>=limit or not observation['posts']: break
            page.mouse.wheel(0,900); page.wait_for_timeout(650)
        login='/i/flow/login' in observation.get('url','') or page.locator('input[autocomplete="username"], input[type="password"]').count()>0
        result={'source':'X via browser locale','url':observation.get('url',url),'retrieved_utc':datetime.now(timezone.utc).isoformat(),
                'status':'observed' if posts else ('login_required' if login else 'unavailable'),
                'posts':list(posts.values())[:limit],'requested_limit':limit,'complete':False,'untrusted_content':True,
                'note':'Solo contenuti osservati; non garantisce completezza o ordine cronologico del profilo. Non inventare post o metriche mancanti.'}
        if name=='x_profile': result['visible_profile_page']=observation.get('text','')[:6000]
        if not posts:
            result['visible_page']=observation.get('text','')[:3000]
            result['next']='Se X richiede accesso, accedi manualmente nella finestra Chrome di freellmapi, poi riprova. Non chiedere chiavi API, password in chat o ripetere ricerche in loop. Nessun post verificato.'
        return result
    except Exception as exc:
        match=re.search(r'net::[A-Z_]+',str(exc))
        return {'status':'browser_error','url':url,'posts':[],'error_type':type(exc).__name__,
                'network_error':match.group(0) if match else None,
                'next':'Controlla la finestra browser o browser_snapshot. Non ripetere in loop e non dichiarare risultati verificati.'}
