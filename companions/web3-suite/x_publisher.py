"""Compose using X's visible UI. Fail closed when controls/account cannot be verified."""
import re

def prepare(browser,draft):
    browser.start(); browser.refs={}; page=browser.page
    page.goto('https://x.com/compose/post',wait_until='domcontentloaded',timeout=30000)
    account=page.locator('[data-testid="SideNav_AccountSwitcher_Button"]')
    account.wait_for(state='visible',timeout=12000)
    handles=re.findall(r'@([A-Za-z0-9_]{1,15})',account.inner_text())
    if draft['account'].casefold() not in [h.casefold() for h in handles]:
        raise ValueError('Account X attivo diverso o non verificabile. Accedi manualmente all’account @'+draft['account']+'.')
    parts=[p.strip() for p in re.split(r'(?m)^---\s*$',draft['text']) if p.strip()]
    if page.locator('[data-testid="tweetTextarea_0"]').inner_text().strip():
        raise ValueError('Il compositore contiene già testo: conserva o rimuovi manualmente quella bozza prima di riprovare.')
    for index,text in enumerate(parts):
        if index:
            add=page.locator('[data-testid="addButton"]')
            if add.count()!=1: raise ValueError('Pulsante aggiunta al thread non identificabile: nessun invio.')
            add.click(timeout=5000)
        editor=page.locator(f'[data-testid="tweetTextarea_{index}"]')
        editor.fill(text,timeout=5000)
        if editor.inner_text().replace('\r\n','\n')!=text.replace('\r\n','\n'):
            raise ValueError('Testo nel browser diverso dalla bozza: nessun invio.')
    button=page.locator('[data-testid="tweetButton"]')
    if button.count()!=1 or not button.is_enabled(): raise ValueError('Invio non disponibile: controlla testo, limiti o login nel browser.')
    return page,button

def submit(page,button):
    button.click(timeout=10000)
    # A toast link is evidence of acknowledgement, not proof all thread posts are public.
    link=page.locator('[data-testid="toast"] a[href*="/status/"]').first
    try:
        link.wait_for(state='visible',timeout=12000)
        url=link.evaluate('(el) => el.href')
        if re.fullmatch(r'https://(?:www\.)?x\.com/[A-Za-z0-9_]+/status/[0-9]+(?:\?.*)?',url):
            return {'state':'submitted','url':url,'verification':'X ha mostrato un link di conferma. Usa x_post per verificare il contenuto; il thread completo non è ancora verificato.'}
    except Exception: pass
    return {'state':'uncertain','next':'Controlla il profilo su X. Nessun nuovo invio automatico; /social resolve ID dopo verifica manuale.'}
