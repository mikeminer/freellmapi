"""Compatibility only: retired paper markers never block authorized live work."""
def active(home):return False
def require_paper(home,mode):
    if mode=='paper':raise ValueError('paper_trading_removed')
