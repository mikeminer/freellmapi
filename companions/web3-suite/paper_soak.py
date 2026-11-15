"""Retired feature. Historical reports remain on disk; no runnable paper service."""
def current(home):return {'available':False,'status':'removed','history_preserved':True}
def disabled(*args,**kwargs):raise ValueError('paper_trading_removed')
start=install=run=disabled
def cancel(home):return {'status':'removed','history_preserved':True,'orders_submitted':0}
if __name__=='__main__':raise SystemExit('paper_trading_removed')
