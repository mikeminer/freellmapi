"""Cached, authenticated mobile account display. No signer or LLM dependencies."""
import threading
import time
from copy import deepcopy
from pathlib import Path


class TradingView:
    def __init__(self,home):
        self.home=Path(home);self.lock=threading.Lock();self.refreshing=False;self.next_refresh=0;self.closed=False
        self.scope=None
        self.value={'state':'unconfigured','read_only':True}

    def snapshot(self,refresh=False):
        import hyperliquid_tools as h
        # A public profile is enough; never load a signer or private key.
        config=h.config(self.home);configured=config.get('default');profile=config.get('profiles',{}).get(configured,{})
        scope=(configured,profile.get('network'),str(profile.get('target','')).lower())
        with self.lock:
            if scope!=self.scope:
                self.scope=scope;self.next_refresh=0;self.value={'state':'unconfigured','read_only':True}
            if configured and not self.closed and not self.refreshing and (refresh or time.monotonic()>=self.next_refresh):
                self.refreshing=True;threading.Thread(target=self._read,args=(configured,scope),daemon=True).start()
            value=deepcopy(self.value);value['refreshing']=self.refreshing
            if value.get('retrieved_ms'):value['age_seconds']=max(0,int((time.time()*1000-value['retrieved_ms'])/1000))
            return value

    def _read(self,name,scope):
        import hyperliquid_tools as h
        from market_routing import account_state
        try:
            raw=h.read(self.home,{'resource':'risk','profile':name})
            value=account_state([{'tool':'hyperliquid_read','arguments':{'resource':'risk'},'result':raw}])
            if not value:raise ValueError('Stato conto non verificabile')
            if value['orders'] is None:
                value['orders']=h.read(self.home,{'resource':'orders','profile':name})['data']
            value.update(state='ready',read_only=True)
            with self.lock:
                if self.scope==scope and not self.closed:self.value=value;self.next_refresh=time.monotonic()+60
        except Exception as exc:
            # Preserve observed funds with an explicit stale flag, never zero them.
            from secret_output import redact
            with self.lock:
                if self.scope==scope:self.value.update(state='stale' if self.value.get('retrieved_ms') else 'error',
                                                      error=redact(str(exc))[:350],read_only=True)
                self.next_refresh=time.monotonic()+30
        finally:
            with self.lock:self.refreshing=False

    def close(self):
        with self.lock:self.closed=True
