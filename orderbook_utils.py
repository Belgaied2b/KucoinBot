# orderbook_utils.py
def detect_imbalance(obs: list, threshold: float = 0.2) -> str:
    """
    obs: liste de dict { 'bids':[[p,s],...], 'asks':... }
    Renvoie 'buy' si bid_vol > ask_vol*(1+th), 'sell' si ask_vol > bid_vol*(1+th), sinon None.
    """
    bid_vol = sum(o['bids'][i][1] for o in obs for i in range(min(10, len(o['bids']))))
    ask_vol = sum(o['asks'][i][1] for o in obs for i in range(min(10, len(o['asks']))))
    if bid_vol > ask_vol * (1 + threshold):
        return 'buy'
    if ask_vol > bid_vol * (1 + threshold):
        return 'sell'
    return None
