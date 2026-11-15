"""Visible Chrome automation in a separate profile, with observed element refs."""
import atexit
import os
from pathlib import Path
import time
from desktop_actions import target_uri

class Browser:
    def __init__(self,home):
        self.home=Path(home); self.engine=None; self.context=None; self.page=None; self.refs={}
        atexit.register(self.close)
    def active(self):
        try: return self.context is not None and any(not p.is_closed() for p in self.context.pages)
        except Exception: return False
    def start(self,headless=False):
        if self.active(): return
        self.close()
        from playwright.sync_api import sync_playwright
        self.engine=sync_playwright().start()
        try:
            channel=os.environ.get('FREELLMAPI_BROWSER_CHANNEL','chrome')
            options={} if channel=='chromium' else {'channel':channel}
            self.context=self.engine.chromium.launch_persistent_context(str(self.home/'browser-profile'),headless=headless,accept_downloads=False,**options)
            self.context.set_default_timeout(15000)
            self.page=self.context.pages[0] if self.context.pages else self.context.new_page()
        except BaseException: self.close(); raise
    def snapshot(self):
        if not self.active(): raise RuntimeError('Browser non avviato. Usa browser_navigate.')
        if self.page.is_closed(): self.page=self.context.pages[-1]
        self.refs={}
        targets=self.page.locator('a,button,input,textarea,select,[role="button"],[role="textbox"]')
        lines=[]
        for i in range(min(targets.count(),150)):
            item=targets.nth(i)
            if not item.is_visible(): continue
            ref=f'e{len(self.refs)+1}'; self.refs[ref]=item.element_handle()
            label=item.get_attribute('aria-label') or item.get_attribute('placeholder') or item.inner_text(timeout=2000)[:180] or item.get_attribute('name') or item.get_attribute('type') or 'element'
            lines.append(f'{ref}: {label}')
        return f'URL: {self.page.url}\nTitolo: {self.page.title()}\n\n'+self.page.locator('body').inner_text()[:16000]+'\n\nElementi:\n'+'\n'.join(lines)
    def navigate(self,url,root):
        uri=target_uri(url,root); self.start(); self.page.goto(uri,wait_until='domcontentloaded',timeout=30000)
        return self.snapshot()
    def element(self,ref):
        if ref not in self.refs: raise ValueError('Riferimento non valido: richiedi browser_snapshot e usa un elemento osservato.')
        return self.refs[ref]
    def click(self,ref):
        self.element(ref).click(); self.refs={}
        pages=self.context.pages
        if pages: self.page=pages[-1]
        self.page.wait_for_load_state('domcontentloaded'); return self.snapshot()
    def fill(self,ref,text):
        self.element(ref).fill(text); return 'Campo compilato. Nessun invio automatico.'
    def screenshot(self):
        if not self.active(): raise RuntimeError('Browser non avviato.')
        path=self.home/'screenshots'/f'{time.time_ns()}.png'; path.parent.mkdir(parents=True,exist_ok=True)
        self.page.screenshot(path=str(path),full_page=True); return str(path)
    def close(self):
        try:
            if self.context: self.context.close()
        except Exception: pass
        finally:
            self.context=None; self.page=None; self.refs={}
            if self.engine:
                try: self.engine.stop()
                except Exception: pass
                self.engine=None
