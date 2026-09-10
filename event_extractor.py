from __future__ import annotations
import re

MONEY_RE = re.compile(r'\$\s?([0-9][0-9,]*(?:\.\d+)?)\s*(million|billion|thousand|m|bn|b|k)?', re.I)
SHARES_RE = re.compile(r'([0-9][0-9,]*(?:\.\d+)?)\s+(?:shares?|units?)\b', re.I)
DATE_RE = re.compile(r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+20\d{2}\b', re.I)


def _clean(s:str)->str:
    return ' '.join(str(s or '').split())


def _money_values(text:str):
    out=[]
    for m in MONEY_RE.finditer(text or ''):
        num=float(m.group(1).replace(',','')); unit=(m.group(2) or '').lower()
        mult=1
        if unit in {'thousand','k'}: mult=1_000
        elif unit in {'million','m'}: mult=1_000_000
        elif unit in {'billion','bn','b'}: mult=1_000_000_000
        out.append({'raw':m.group(0).strip(),'value':num*mult})
    return out


def _first_sentence(text:str, limit=520):
    text=_clean(text)
    if not text:return ''
    # Prefer the first two informative sentences, avoiding exhibit boilerplate.
    parts=re.split(r'(?<=[.!?])\s+', text)
    keep=[]
    for p in parts:
        if len(p)<24: continue
        if re.search(r'forward[- ]looking|incorporated by reference|exhibit|signature',p,re.I): continue
        keep.append(p)
        if len(' '.join(keep))>=limit or len(keep)>=2: break
    return _clean(' '.join(keep))[:limit]


def extract_8k_event(codes:list[str], sections:list[dict])->dict:
    joined=' '.join(_clean(s.get('summary_text')) for s in sections if s.get('summary_text'))
    lower=joined.lower()
    facts={'event_kind':'other','event_label':'Corporate event','summary':'','counterparty':'','amount':None,'amount_text':'','shares':None,'dates':DATE_RE.findall(joined)[:4],'securities':'','dilution_context':'','confidence':0}

    # Event classification. Order matters: financing before generic agreement.
    if re.search(r'securities purchase agreement|registered direct offering|private placement|purchase agreement.*institutional investor|sale of .*shares|issue and sell', lower):
        facts['event_kind']='financing'; facts['event_label']='Equity financing / securities sale'; facts['confidence']=85
    elif re.search(r'credit agreement|term loan|revolving credit|senior notes|debt financing|borrowed|principal amount', lower):
        facts['event_kind']='debt'; facts['event_label']='Debt / credit financing'; facts['confidence']=82
    elif re.search(r'merger agreement|acquisition agreement|purchase of .*business|acquire[d]? .*assets|business combination', lower) or '2.01' in codes:
        facts['event_kind']='acquisition'; facts['event_label']='Acquisition / disposition'; facts['confidence']=86
    elif re.search(r'resign(?:ed|ation)|terminate(?:d|ion).*chief|appointed .*chief|named .*chief executive|director .*resign', lower) or '5.02' in codes:
        facts['event_kind']='leadership'; facts['event_label']='Executive / board change'; facts['confidence']=80
    elif re.search(r'cybersecurity incident|ransomware|unauthorized access|data breach', lower):
        facts['event_kind']='cyber'; facts['event_label']='Cybersecurity incident'; facts['confidence']=88
    elif re.search(r'bankrupt|chapter 11|chapter 7|restructuring support agreement', lower):
        facts['event_kind']='bankruptcy'; facts['event_label']='Bankruptcy / restructuring'; facts['confidence']=90
    elif re.search(r'delisting|noncompliance|minimum bid price|listing rule', lower) or '3.01' in codes:
        facts['event_kind']='listing'; facts['event_label']='Exchange listing / compliance event'; facts['confidence']=78
    elif re.search(r'material definitive agreement|entered into .*agreement', lower) or '1.01' in codes:
        facts['event_kind']='agreement'; facts['event_label']='Material agreement'; facts['confidence']=68
    elif '2.02' in codes:
        facts['event_kind']='earnings'; facts['event_label']='Earnings / operating results'; facts['confidence']=75

    monies=_money_values(joined)
    if monies:
        # Largest disclosed dollar figure is generally the most headline-worthy; preserve raw text.
        best=max(monies,key=lambda x:x['value'])
        facts['amount']=best['value']; facts['amount_text']=best['raw']
    sh=SHARES_RE.findall(joined)
    if sh:
        try:facts['shares']=max(float(x.replace(',','')) for x in sh)
        except:pass

    # Security types for financing events.
    sec=[]
    for label,pat in [
        ('common stock',r'common stock'),('preferred stock',r'preferred stock'),('warrants',r'warrants?'),
        ('convertible notes',r'convertible (?:senior )?notes?'),('senior notes',r'senior notes?'),
        ('pre-funded warrants',r'pre-funded warrants?')]:
        if re.search(pat, lower):sec.append(label)
    facts['securities']=', '.join(dict.fromkeys(sec))

    # Counterparty/investor phrasing.
    m=re.search(r'(?:with|by)\s+((?:certain|one or more)?\s*(?:institutional investors?|purchasers?|lenders?|buyers?))', joined, re.I)
    if m:facts['counterparty']=_clean(m.group(1))
    else:
        m=re.search(r'entered into [^.]{0,120}? with ([A-Z][A-Za-z0-9&,.\- ]{3,90}?)(?:,|\.| pursuant| under)', joined)
        if m:facts['counterparty']=_clean(m.group(1))

    if facts['event_kind']=='financing':
        if re.search(r'dilut', lower): facts['dilution_context']='The filing explicitly references dilution.'
        elif facts['shares'] or re.search(r'common stock|warrant|preferred stock',lower): facts['dilution_context']='Issuing equity or equity-linked securities can dilute existing shareholders; the actual effect depends on the terms and shares outstanding.'

    facts['summary']=_first_sentence(joined)
    return facts
