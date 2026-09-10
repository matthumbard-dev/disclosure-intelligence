from __future__ import annotations

def score_event(event:dict)->tuple[int,list[str]]:
    form=(event.get('form') or '').upper(); typ=event.get('event_type') or ''; code=(event.get('transaction_code') or '').upper(); value=float(event.get('value') or 0); role=(event.get('role') or '').lower(); pct=float(event.get('ownership_after') or 0)
    score=20; reasons=[]
    if form in {'SC 13D','SC 13D/A'}:score+=42;reasons.append('activist/significant ownership filing')
    elif form in {'SC 13G','SC 13G/A'}:score+=22;reasons.append('large beneficial ownership filing')
    elif form=='144':score+=22;reasons.append('proposed affiliate sale')
    elif form.startswith('8-K'):score+=12;reasons.append('material corporate event')
    if typ=='insider_transaction':
        if code=='P':score+=35;reasons.append('open-market insider purchase')
        elif code=='S':score+=13;reasons.append('open-market insider sale')
        elif code in {'A','M','F','G','D'}:score-=10;reasons.append('award/exercise/tax/gift or routine transaction')
        if any(x in role for x in ('chief executive','ceo','chief financial','cfo','president')):score+=9;reasons.append('senior executive transaction')
        elif 'director' in role:score+=4;reasons.append('director transaction')
    if value>=5_000_000:score+=20;reasons.append('value >= $5M')
    elif value>=1_000_000:score+=15;reasons.append('value >= $1M')
    elif value>=500_000:score+=10;reasons.append('value >= $500K')
    elif value>=100_000:score+=5;reasons.append('value >= $100K')
    if typ=='ownership' and pct>=10:score+=8;reasons.append('ownership >= 10%')
    return max(0,min(100,int(score))),reasons

def band(score:int)->str:
    return 'High' if score>=80 else 'Interesting' if score>=60 else 'Notable' if score>=40 else 'Routine'
