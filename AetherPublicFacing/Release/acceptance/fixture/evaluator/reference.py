"""Evaluator-only oracle for qualifying the grader. NOT agent starter code."""
MONEY = '''import re
def parse_minor(amount, scale=2):
    if type(scale) is not int or not 0 <= scale <= 3: raise ValueError('scale')
    if not isinstance(amount,str) or not re.fullmatch(r'[0-9]+(?:\\.[0-9]+)?',amount): raise ValueError('amount')
    whole, _, fraction = amount.partition('.')
    if len(fraction)>scale: raise ValueError('precision')
    minor=int(whole)*10**scale+int((fraction+'0'*scale)[:scale] or '0')
    if not 0<=minor<=999999999: raise ValueError('range')
    return minor
'''

REPORTS = '''import re
from .money import parse_minor
from .time_window import timestamp
def _groups(rows,start,end,scales):
    low,high=timestamp(start),timestamp(end)
    if low>=high: raise ValueError('interval')
    groups={}; seen=set()
    for row in rows:
        if set(row)!={'id','at','amount','currency'}: raise ValueError('fields')
        ident=row['id']; currency=row['currency']
        if not isinstance(ident,str) or not re.fullmatch(r'[A-Za-z0-9_-]+',ident) or ident in seen: raise ValueError('id')
        seen.add(ident)
        if currency not in scales: raise ValueError('currency')
        amount=parse_minor(row['amount'],scales[currency]); at=timestamp(row['at'])
        if low<=at<high:
            group=groups.setdefault(currency,dict(currency=currency,count=0,total_minor=0,ids=[]))
            group['count']+=1; group['total_minor']+=amount; group['ids'].append(ident)
    for g in groups.values(): g['ids'].sort()
    return [groups[c] for c in sorted(groups)]
def daily_report(rows,start,end):
    groups=_groups(rows,start,end,{'USD':2})
    return {k:v for k,v in groups[0].items() if k!='currency'} if groups else dict(count=0,total_minor=0,ids=[])
def currency_report(rows,start,end):
    return _groups(rows,start,end,{'USD':2,'JPY':0,'KWD':3})
'''
