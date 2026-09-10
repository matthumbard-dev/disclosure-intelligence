from __future__ import annotations
import re, time, hashlib
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html import unescape
from urllib.parse import urlencode
import requests
from bs4 import BeautifulSoup
from scoring import score_event, band

SEC_DATA='https://data.sec.gov'
SEC_WWW='https://www.sec.gov'
SEC_TICKERS='https://www.sec.gov/files/company_tickers.json'
CURRENT='https://www.sec.gov/cgi-bin/browse-edgar'
FORMS=['4','144','SC 13D','SC 13D/A','SC 13G','SC 13G/A','8-K']

@dataclass
class SecClient:
    user_agent:str
    min_interval:float=.22
    def __post_init__(self):
        if not self.user_agent or '@' not in self.user_agent:
            raise ValueError('SEC_USER_AGENT must contain a contact email.')
        self.s=requests.Session()
        self.s.headers.update({'User-Agent':self.user_agent,'Accept-Encoding':'gzip, deflate'})
        self._last=0.0
    def _get(self,url:str):
        wait=self.min_interval-(time.time()-self._last)
        if wait>0: time.sleep(wait)
        r=self.s.get(url,timeout=30)
        self._last=time.time(); r.raise_for_status(); return r
    def ticker_maps(self):
        raw=self._get(SEC_TICKERS).json()
        by_ticker={}; by_cik={}
        for v in raw.values():
            cik=str(v['cik_str']).zfill(10); t=v['ticker'].upper(); title=v['title']
            by_ticker[t]={'cik':cik,'title':title}; by_cik[cik]={'ticker':t,'title':title}
        return by_ticker,by_cik
    def submissions(self,cik:str):
        return self._get(f'{SEC_DATA}/submissions/CIK{str(cik).zfill(10)}.json').json()
    def filing_document(self,cik:str,accession:str,primary:str):
        url=f"{SEC_WWW}/Archives/edgar/data/{int(cik)}/{accession.replace('-','')}/{primary}"
        return self._get(url).content
    def current_feed(self,form:str,start:int=0,count:int=100):
        q=urlencode({'action':'getcurrent','type':form,'owner':'include','start':start,'count':count,'output':'atom'})
        return self._get(f'{CURRENT}?{q}').content


def _local(tag): return tag.split('}',1)[-1].lower()
def _text(node,path,default=''):
    x=node.find(path); return (x.text or '').strip() if x is not None else default
def _value(node,path,default=''):
    x=node.find(path)
    if x is None:return default
    v=x.find('value'); return ((v.text if v is not None else x.text) or '').strip()
def _float(v):
    try:return float(str(v).replace(',','').replace('$','').replace('%','').strip())
    except:return None

def _all_values(root):
    out={}
    for e in root.iter():
        txt=(e.text or '').strip()
        if txt: out.setdefault(_local(e.tag),[]).append(txt)
    return out

def _pick(vals,*needles):
    for n in needles:
        n=n.lower()
        for k,v in vals.items():
            if n in k and v:return v[0]
    return ''

def parse_atom(raw:bytes,form_hint:str)->list[dict]:
    root=ET.fromstring(raw)
    ns={'a':'http://www.w3.org/2005/Atom'}
    entries=[]
    for e in root.findall('a:entry',ns):
        title=(e.findtext('a:title',default='',namespaces=ns) or '').strip()
        link=e.find('a:link',ns); href=link.attrib.get('href','') if link is not None else ''
        updated=(e.findtext('a:updated',default='',namespaces=ns) or '')[:10]
        ident=e.findtext('a:id',default='',namespaces=ns) or ''
        summary=e.findtext('a:summary',default='',namespaces=ns) or ''
        acc=''
        m=re.search(r'accession-number=([0-9-]+)',ident,re.I)
        if m:acc=m.group(1)
        if not acc:
            m=re.search(r'(\d{10}-\d{2}-\d{6})',href)
            if m:acc=m.group(1)
        cik=''
        m=re.search(r'/data/(\d+)/',href)
        if m:cik=m.group(1).zfill(10)
        if not cik:
            m=re.search(r'\((\d{10})\)',title)
            if m:cik=m.group(1)
        filed=updated
        m=re.search(r'Filed:\s*</b>\s*([0-9-]+)',summary,re.I)
        if m:filed=m.group(1)
        company=re.sub(r'^.*?\s+-\s+','',title).strip()
        company=re.sub(r'\s+\(\d{10}\).*','',company).strip() or title
        entries.append({'form':form_hint,'accession':acc,'cik':cik,'filed_at':filed,'company':unescape(company),'source_url':href})
    return entries

def _find_primary(sub:dict,accession:str):
    recent=sub.get('filings',{}).get('recent',{})
    accs=recent.get('accessionNumber',[])
    try:i=accs.index(accession)
    except ValueError:return None
    return {
      'primary_document':recent.get('primaryDocument',['']*len(accs))[i],
      'form':recent.get('form',['']*len(accs))[i],
      'report_date':recent.get('reportDate',['']*len(accs))[i] if recent.get('reportDate') else '',
      'items':recent.get('items',['']*len(accs))[i] if recent.get('items') else ''
    }

def parse_form4(raw:bytes,filing:dict,ticker:str,cik:str)->list[dict]:
    root=ET.fromstring(raw)
    issuer=_text(root,'issuer/issuerName') or filing.get('company','')
    issuer_ticker=_text(root,'issuer/issuerTradingSymbol') or ticker
    owner=_text(root,'reportingOwner/reportingOwnerId/rptOwnerName')
    rel=root.find('reportingOwner/reportingOwnerRelationship'); roles=[]
    if rel is not None:
        if _text(rel,'isDirector')=='1':roles.append('Director')
        if _text(rel,'isOfficer')=='1':roles.append(_text(rel,'officerTitle') or 'Officer')
        if _text(rel,'isTenPercentOwner')=='1':roles.append('>10% Owner')
    role=', '.join(roles)
    txs=list(root.findall('nonDerivativeTable/nonDerivativeTransaction'))+list(root.findall('derivativeTable/derivativeTransaction'))
    out=[]
    for i,tx in enumerate(txs):
        code=_text(tx,'transactionCoding/transactionCode'); txd=_value(tx,'transactionDate')
        shares=_float(_value(tx,'transactionAmounts/transactionShares')); price=_float(_value(tx,'transactionAmounts/transactionPricePerShare'))
        ad=_value(tx,'transactionAmounts/transactionAcquiredDisposedCode'); after=_float(_value(tx,'postTransactionAmounts/sharesOwnedFollowingTransaction'))
        value=shares*price if shares is not None and price is not None else None
        direction='acquired' if ad=='A' else 'disposed' if ad=='D' else 'transacted'
        ev={**filing,'event_id':f"{filing['accession']}:{i}:{code}:{txd}",'event_type':'insider_transaction','company':issuer,'ticker':issuer_ticker or ticker,'cik':cik,'actor':owner,'role':role,'transaction_code':code,'transaction_date':txd,'shares':shares,'price':price,'value':value,'ownership_after':after,'detail':''}
        ev['summary']=f"{owner or 'Insider'} {direction} {shares:,.0f} shares" if shares is not None else f"{owner or 'Insider'} reported a transaction"
        if price is not None:ev['summary']+=f' at ${price:,.2f}'
        sc,rs=score_event(ev);ev['score']=sc;ev['score_band']=band(sc);ev['reasons']='; '.join(rs);out.append(ev)
    return out

def parse_144(raw:bytes,filing:dict,ticker:str,cik:str)->dict:
    try: root=ET.fromstring(raw); vals=_all_values(root)
    except Exception: vals={}
    actor=_pick(vals,'nameofpersonforwhoseaccount','personforwhoseaccount','sellername','filername')
    shares=_float(_pick(vals,'noofunitssold','numberofsharestobesold','securitiestobesold'))
    value=_float(_pick(vals,'aggregatemarketvalue','marketvalue'))
    sale_date=_pick(vals,'approxsaledate','approximatedateofsale')
    broker=_pick(vals,'brokername')
    ev={**filing,'event_id':filing['accession'],'event_type':'proposed_sale','ticker':ticker,'cik':cik,'actor':actor,'role':'Affiliate','transaction_code':'144','transaction_date':sale_date,'shares':shares,'price':None,'value':value,'ownership_after':None,'detail':broker}
    ev['summary']='Proposed affiliate sale'
    if shares:ev['summary']+=f' of {shares:,.0f} shares'
    if value:ev['summary']+=f' (~${value:,.0f})'
    sc,rs=score_event(ev);ev['score']=sc;ev['score_band']=band(sc);ev['reasons']='; '.join(rs);return ev

def parse_ownership(raw:bytes,filing:dict,ticker:str,cik:str)->dict:
    text=''; actor=''; pct=None; issuer=filing.get('company',''); symbol=ticker
    try:
        root=ET.fromstring(raw); vals=_all_values(root)
        issuer=_pick(vals,'nameofissuer','issuername') or issuer
        symbol=_pick(vals,'issuertradingsymbol','tradingsymbol') or symbol
        actor=_pick(vals,'reportingpersonname','nameofreportingperson','reportingownername')
        pct=_float(_pick(vals,'percentofclass','percentageofclass','percentclass'))
        text=' '.join(v for arr in vals.values() for v in arr[:1])
    except Exception:
        soup=BeautifulSoup(raw,'html.parser'); text=soup.get_text(' ',strip=True)
    if pct is None:
        m=re.search(r'(?:percent(?:age)?\s+of\s+class|percent of class represented[^0-9]{0,80})(\d{1,2}(?:\.\d+)?)\s*%',text,re.I)
        if m:pct=_float(m.group(1))
    ev={**filing,'event_id':filing['accession'],'event_type':'ownership','company':issuer,'ticker':symbol,'cik':cik,'actor':actor,'role':'Beneficial owner','transaction_code':'','transaction_date':filing.get('report_date',''),'shares':None,'price':None,'value':None,'ownership_after':pct,'detail':''}
    ev['summary']=('New ' if not filing['form'].endswith('/A') else '')+f"{filing['form'].replace('SC ','Schedule ')} beneficial ownership filing"
    if pct:ev['summary']+=f' reporting {pct:.1f}% ownership'
    sc,rs=score_event(ev);ev['score']=sc;ev['score_band']=band(sc);ev['reasons']='; '.join(rs);return ev

def parse_8k(raw:bytes,filing:dict,ticker:str,cik:str)->dict:
    items=filing.get('items','') or ''
    if not items:
        text=BeautifulSoup(raw,'html.parser').get_text(' ',strip=True)[:120000]
        found=[]
        for code in ['1.01','1.02','2.01','2.02','2.03','2.04','3.01','3.02','4.01','5.02','5.07','7.01','8.01']:
            if re.search(rf'Item\s+{re.escape(code)}\b',text,re.I):found.append(code)
        items=', '.join(found)
    labels={'1.01':'material agreement','2.01':'acquisition/disposition','2.02':'earnings/results','2.03':'new financial obligation','3.02':'unregistered securities sale','4.01':'auditor change','5.02':'executive/director change','7.01':'Reg FD disclosure','8.01':'other material event'}
    meaningful=[labels[x] for x in labels if x in items]
    detail=', '.join(meaningful[:3])
    ev={**filing,'event_id':filing['accession'],'event_type':'material_event','ticker':ticker,'cik':cik,'actor':'','role':'','transaction_code':'','transaction_date':filing.get('report_date',''),'shares':None,'price':None,'value':None,'ownership_after':None,'detail':items}
    ev['summary']='8-K filed'+(f": {detail}" if detail else '')
    sc,rs=score_event(ev)
    if any(x in items for x in ['1.01','2.01','3.02','4.01','5.02']):sc=min(100,sc+8);rs.append('higher-impact 8-K item')
    ev['score']=sc;ev['score_band']=band(sc);ev['reasons']='; '.join(rs);return ev

def scan_market(client:SecClient,pages_per_form:dict|None=None)->list[dict]:
    pages_per_form=pages_per_form or {'4':3,'144':1,'SC 13D':1,'SC 13D/A':1,'SC 13G':1,'SC 13G/A':1,'8-K':2}
    _,by_cik=client.ticker_maps(); raw_entries=[]
    for form,pages in pages_per_form.items():
        for p in range(pages):
            try: raw_entries.extend(parse_atom(client.current_feed(form,p*100,100),form))
            except Exception: break
    # de-duplicate and prioritize newest/high signal forms
    seen=set(); entries=[]
    for e in raw_entries:
        key=(e.get('accession'),e.get('form'))
        if not e.get('accession') or key in seen:continue
        seen.add(key);entries.append(e)
    events=[]; sub_cache={}
    for e in entries:
        cik=e.get('cik',''); info=by_cik.get(cik,{}); ticker=info.get('ticker',''); company=info.get('title') or e.get('company','')
        e['company']=company; e.setdefault('report_date',''); e.setdefault('primary_document','')
        if not cik:
            sc,rs=score_event(e);e.update({'event_id':e['accession'],'event_type':'filing','ticker':'','actor':'','role':'','transaction_code':'','transaction_date':'','shares':None,'price':None,'value':None,'ownership_after':None,'summary':f"{e['form']} filed",'score':sc,'score_band':band(sc),'reasons':'; '.join(rs),'detail':''});events.append(e);continue
        try:
            if cik not in sub_cache:sub_cache[cik]=client.submissions(cik)
            meta=_find_primary(sub_cache[cik],e['accession']) or {}
            e.update(meta); primary=e.get('primary_document','')
            raw=client.filing_document(cik,e['accession'],primary) if primary else b''
            form=e['form']
            if form in {'4','4/A'} and b'<ownershipDocument' in raw[:4000]: events.extend(parse_form4(raw,e,ticker,cik)); continue
            if form=='144':events.append(parse_144(raw,e,ticker,cik));continue
            if form.startswith('SC 13'):events.append(parse_ownership(raw,e,ticker,cik));continue
            if form.startswith('8-K'):events.append(parse_8k(raw,e,ticker,cik));continue
        except Exception:
            pass
        generic={**e,'event_id':e['accession'],'event_type':'filing','ticker':ticker,'cik':cik,'actor':'','role':'','transaction_code':'','transaction_date':e.get('report_date',''),'shares':None,'price':None,'value':None,'ownership_after':None,'detail':'','summary':f"{e['form']} filed"}
        sc,rs=score_event(generic);generic['score']=sc;generic['score_band']=band(sc);generic['reasons']='; '.join(rs);events.append(generic)
    # cluster-buy enhancement
    buys={}
    for ev in events:
        if ev.get('event_type')=='insider_transaction' and ev.get('transaction_code')=='P':buys.setdefault(ev.get('ticker',''),[]).append(ev)
    for t,arr in buys.items():
        actors={x.get('actor') for x in arr if x.get('actor')}
        if t and len(actors)>=2:
            for ev in arr:
                ev['score']=min(100,int(ev.get('score',0))+10);ev['score_band']=band(ev['score']);ev['reasons']=(ev.get('reasons','')+'; multiple insiders buying in current scan').strip('; ')
    return events
