from __future__ import annotations
import re, time, hashlib, json
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from html import unescape
from urllib.parse import urlencode
import requests
from bs4 import BeautifulSoup
from scoring import score_event, band
from event_extractor import extract_8k_event

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
        if not self.user_agent or '@' not in self.user_agent: raise ValueError('SEC_USER_AGENT must contain a contact email.')
        self.s=requests.Session(); self.s.headers.update({'User-Agent':self.user_agent,'Accept-Encoding':'gzip, deflate'})
        self._last=0.0
    def _get(self,url:str):
        wait=self.min_interval-(time.time()-self._last)
        if wait>0: time.sleep(wait)
        r=self.s.get(url,timeout=30); self._last=time.time(); r.raise_for_status(); return r
    def ticker_maps(self):
        raw=self._get(SEC_TICKERS).json(); by_ticker={}; by_cik={}
        for v in raw.values():
            cik=str(v['cik_str']).zfill(10); t=v['ticker'].upper(); title=v['title']
            by_ticker[t]={'cik':cik,'title':title}; by_cik[cik]={'ticker':t,'title':title}
        return by_ticker,by_cik
    def submissions(self,cik:str): return self._get(f'{SEC_DATA}/submissions/CIK{str(cik).zfill(10)}.json').json()
    def filing_document(self,cik:str,accession:str,primary:str,max_bytes:int=2500000):
        url=f"{SEC_WWW}/Archives/edgar/data/{int(cik)}/{accession.replace('-','')}/{primary}"
        wait=self.min_interval-(time.time()-self._last)
        if wait>0: time.sleep(wait)
        r=self.s.get(url,timeout=30,stream=True); self._last=time.time(); r.raise_for_status(); chunks=[]; total=0
        try:
            for chunk in r.iter_content(65536):
                if not chunk: continue
                total += len(chunk)
                if total>max_bytes: break
                chunks.append(chunk)
            return b''.join(chunks)
        finally:r.close()
    def current_feed(self,form:str,start:int=0,count:int=100):
        q=urlencode({'action':'getcurrent','type':form,'owner':'include','start':start,'count':count,'output':'atom'})
        return self._get(f'{CURRENT}?{q}').content

def _local(tag): return tag.split('}',1)[-1].lower()
def _norm(s): return re.sub(r'[^a-z0-9]','',str(s).lower())
def _text(node,path,default=''):
    x=node.find(path); return (x.text or '').strip() if x is not None else default
def _value(node,path,default=''):
    x=node.find(path)
    if x is None:return default
    v=x.find('value'); return ((v.text if v is not None else x.text) or '').strip()
def _float(v):
    try:return float(str(v).replace(',','').replace('$','').replace('%','').strip())
    except:return None
def _truth(v): return str(v or '').strip().lower() in {'1','true','yes','y','checked'}

def _leaf_map(root):
    out={}
    for e in root.iter():
        txt=(e.text or '').strip()
        if txt:
            k=_norm(_local(e.tag)); out.setdefault(k,[]).append(txt)
    return out

def _first(vals,*names):
    for name in names:
        n=_norm(name)
        if n in vals and vals[n]: return vals[n][0]
    # controlled suffix/contains fallback for schema variants
    for name in names:
        n=_norm(name)
        for k,arr in vals.items():
            if arr and (k.endswith(n) or n.endswith(k) or (len(n)>7 and n in k)): return arr[0]
    return ''

def _all(vals,*names):
    out=[]
    for name in names:
        n=_norm(name)
        if n in vals: out.extend(vals[n])
    return out

def _node_record(node):
    d={}
    for e in node.iter():
        if e is node: continue
        txt=(e.text or '').strip()
        if txt:d[_norm(_local(e.tag))]=txt
    return d

def _records_with(root,*keys):
    ns=[_norm(k) for k in keys]; out=[]
    for node in root.iter():
        rec=_node_record(node)
        if rec and sum(1 for k in ns if k in rec)>=2:
            # only favor reasonably local row-like containers
            if len(rec)<=30: out.append(rec)
    # remove duplicates caused by nested containers
    uniq=[]; seen=set()
    for r in sorted(out,key=len):
        sig=tuple(sorted(r.items()))
        if sig not in seen: seen.add(sig); uniq.append(r)
    return uniq[:60]

def parse_atom(raw:bytes,form_hint:str)->list[dict]:
    root=ET.fromstring(raw); ns={'a':'http://www.w3.org/2005/Atom'}; entries=[]
    for e in root.findall('a:entry',ns):
        title=(e.findtext('a:title',default='',namespaces=ns) or '').strip(); link=e.find('a:link',ns); href=link.attrib.get('href','') if link is not None else ''
        updated=(e.findtext('a:updated',default='',namespaces=ns) or '')[:10]; ident=e.findtext('a:id',default='',namespaces=ns) or ''; summary=e.findtext('a:summary',default='',namespaces=ns) or ''
        acc=''; m=re.search(r'accession-number=([0-9-]+)',ident,re.I)
        if m:acc=m.group(1)
        if not acc:
            m=re.search(r'(\d{10}-\d{2}-\d{6})',href); acc=m.group(1) if m else ''
        cik=''; m=re.search(r'/data/(\d+)/',href)
        if m:cik=m.group(1).zfill(10)
        filed=updated; m=re.search(r'Filed:\s*</b>\s*([0-9-]+)',summary,re.I)
        if m:filed=m.group(1)
        company=re.sub(r'^.*?\s+-\s+','',title).strip(); company=re.sub(r'\s+\(\d{10}\).*','',company).strip() or title
        entries.append({'form':form_hint,'accession':acc,'cik':cik,'filed_at':filed,'company':unescape(company),'source_url':href})
    return entries

def _find_primary(sub:dict,accession:str):
    recent=sub.get('filings',{}).get('recent',{}); accs=recent.get('accessionNumber',[])
    try:i=accs.index(accession)
    except ValueError:return None
    def at(k):
        a=recent.get(k,[]); return a[i] if i<len(a) else ''
    return {'primary_document':at('primaryDocument'),'form':at('form'),'report_date':at('reportDate'),'items':at('items')}

def _footnotes(root):
    f={}
    for n in root.findall('.//footnote'):
        fid=n.attrib.get('id',''); txt=' '.join(''.join(n.itertext()).split())
        if fid:f[fid]=txt
    return f

def _tx_footnotes(tx,footnotes):
    ids=[]
    for n in tx.findall('.//footnoteId'):
        fid=n.attrib.get('id','')
        if fid:ids.append(fid)
    return ' '.join(footnotes.get(x,'') for x in ids if footnotes.get(x)).strip()

def parse_form4(raw:bytes,filing:dict,ticker:str,cik:str)->list[dict]:
    root=ET.fromstring(raw); issuer=_text(root,'issuer/issuerName') or filing.get('company',''); issuer_ticker=_text(root,'issuer/issuerTradingSymbol') or ticker
    owner=_text(root,'reportingOwner/reportingOwnerId/rptOwnerName'); rel=root.find('reportingOwner/reportingOwnerRelationship'); roles=[]
    if rel is not None:
        if _text(rel,'isDirector')=='1':roles.append('Director')
        if _text(rel,'isOfficer')=='1':roles.append(_text(rel,'officerTitle') or 'Officer')
        if _text(rel,'isTenPercentOwner')=='1':roles.append('>10% Owner')
        if _text(rel,'isOther')=='1': roles.append(_text(rel,'otherText') or 'Other')
    role=', '.join(roles); fnotes=_footnotes(root); out=[]
    txs=[(x,False) for x in root.findall('nonDerivativeTable/nonDerivativeTransaction')]+[(x,True) for x in root.findall('derivativeTable/derivativeTransaction')]
    for i,(tx,is_deriv) in enumerate(txs):
        code=_text(tx,'transactionCoding/transactionCode'); txd=_value(tx,'transactionDate'); shares=_float(_value(tx,'transactionAmounts/transactionShares')); price=_float(_value(tx,'transactionAmounts/transactionPricePerShare'))
        ad=_value(tx,'transactionAmounts/transactionAcquiredDisposedCode'); after=_float(_value(tx,'postTransactionAmounts/sharesOwnedFollowingTransaction')); value=shares*price if shares is not None and price is not None else None
        security=_value(tx,'securityTitle') or ('Derivative security' if is_deriv else 'Common stock'); direct=_value(tx,'ownershipNature/directOrIndirectOwnership'); indirect=_value(tx,'ownershipNature/natureOfOwnership')
        foot=_tx_footnotes(tx,fnotes); prior=None; pct_change=None
        if shares is not None and after is not None:
            prior=after-shares if ad=='A' else after+shares if ad=='D' else None
            if prior and prior>0: pct_change=(shares/prior)*100
        direction='acquired' if ad=='A' else 'disposed' if ad=='D' else 'transacted'
        details={'security':security,'acquired_disposed':ad,'is_derivative':is_deriv,'direct_or_indirect':direct,'nature_of_ownership':indirect,'footnotes':foot,'prior_ownership':prior,'holdings_change_pct':pct_change}
        ev={**filing,'event_id':f"{filing['accession']}:{i}:{code}:{txd}",'event_type':'insider_transaction','company':issuer,'ticker':issuer_ticker or ticker,'cik':cik,'actor':owner,'role':role,'transaction_code':code,'transaction_date':txd,'shares':shares,'price':price,'value':value,'ownership_after':after,'detail':foot[:1200],'details_json':details}
        ev['summary']=f"{owner or 'Insider'} {direction} {shares:,.0f} shares" if shares is not None else f"{owner or 'Insider'} reported a transaction"
        if price is not None:ev['summary']+=f' at ${price:,.2f}'
        sc,rs=score_event(ev); ev['score']=sc;ev['score_band']=band(sc);ev['reasons']='; '.join(rs);out.append(ev)
    return out

def parse_144(raw:bytes,filing:dict,ticker:str,cik:str)->dict:
    root=ET.fromstring(raw); vals=_leaf_map(root)
    symbol=_first(vals,'issuerTradingSymbol','tradingSymbol','issuerSymbol','symbol').upper().strip(); symbol=re.sub(r'[^A-Z0-9.\-]','',symbol)
    issuer=_first(vals,'issuerName','nameOfIssuer','issuerInformationName') or filing.get('company','')
    actor=_first(vals,'nameOfPersonForWhoseAccount','personForWhoseAccount','sellerName','filerName','nameOfPersonForWhoseAccountTheSecuritiesAreToBeSold')
    role=_first(vals,'relationshipToIssuer','relationship') or 'Affiliate'
    shares=_float(_first(vals,'noOfUnitsSold','numberOfSharesToBeSold','numberOfSharesOrOtherUnitsToBeSold','securitiesToBeSold','unitsToBeSold'))
    value=_float(_first(vals,'aggregateMarketValue','marketValue','aggregateMarketValueOfSecuritiesToBeSold'))
    sale_date=_first(vals,'approxSaleDate','approximateDateOfSale'); broker=_first(vals,'brokerName','nameOfBroker'); exchange=_first(vals,'securitiesExchangeName','nameOfEachSecuritiesExchange','exchangeName')
    class_title=_first(vals,'securityClassTitle','titleOfClass','titleOfTheClassOfSecuritiesToBeSold'); outstanding=_float(_first(vals,'noOfUnitsOutstanding','numberOfSharesOrOtherUnitsOutstanding'))
    notice_date=_first(vals,'noticeDate','dateOfNotice') or filing.get('filed_at',''); remarks=_first(vals,'remarks')
    plan_date=_first(vals,'planAdoptionDate','dateOfPlanAdoption','adoptionDateOfTradingPlan','dateOfAdoptionOfWrittenTradingPlan')
    plan_flag=_truth(_first(vals,'planAdoptionFlag','is10b51','tenb51','writtenTradingPlanFlag')) or bool(plan_date)
    # Prior-sale section
    nothing=_truth(_first(vals,'nothingToReportFlag','nothingToReport','securitiesSoldPast3MonthsNothingToReportFlag','securitiesSoldDuringPast3MonthsNothingToReport'))
    prior_rows=_records_with(root,'amountOfSecuritiesSold','dateOfSale')
    prior_sales=[]
    for r in prior_rows:
        qty=_float(r.get(_norm('amountOfSecuritiesSold')) or r.get(_norm('amountSold'))); dt=r.get(_norm('dateOfSale'),''); gross=_float(r.get(_norm('grossProceeds')) or r.get(_norm('amountOfGrossProceeds')))
        if qty or dt or gross:prior_sales.append({'date':dt,'shares':qty,'gross_proceeds':gross})
    # Acquisition / securities-to-be-sold history rows. This is especially useful for distinguishing RSUs/options/open market.
    acq_rows=_records_with(root,'natureOfAcquisitionTransaction','amountOfSecuritiesAcquired')
    acquisitions=[]
    for r in acq_rows:
        nature=r.get(_norm('natureOfAcquisitionTransaction'),''); qty=_float(r.get(_norm('amountOfSecuritiesAcquired'))); dt=r.get(_norm('dateYouAcquired')) or r.get(_norm('dateAcquired')) or ''
        payment=r.get(_norm('natureOfPayment'),''); source=r.get(_norm('nameOfPersonFromWhomAcquired'),'')
        if nature or qty or dt: acquisitions.append({'date':dt,'nature':nature,'shares':qty,'payment':payment,'source':source})
    # De-duplicate same acquisition rows.
    aq=[]; seen=set()
    for r in acquisitions:
        sig=(r['date'],r['nature'],r['shares'],r['payment'],r['source'])
        if sig not in seen: seen.add(sig); aq.append(r)
    acquisitions=aq[:40]
    if not nothing and not prior_sales:
        # SEC XML often encodes checkbox wording in a nearby leaf rather than a clean boolean.
        texts=' '.join(x for arr in vals.values() for x in arr)
        if re.search(r'nothing\s+to\s+report',texts,re.I): nothing=True
    details={'security_class':class_title,'broker':broker,'exchange':exchange,'shares_outstanding':outstanding,'notice_date':notice_date,'remarks':remarks,'tenb5_1_plan':plan_flag,'plan_adoption_date':plan_date,'prior_3_month_sales_none':nothing,'prior_3_month_sales':prior_sales[:12],'acquisition_history':acquisitions}
    ev={**filing,'event_id':filing['accession'],'event_type':'proposed_sale','company':issuer,'ticker':symbol or ticker,'cik':cik,'actor':actor,'role':role,'transaction_code':'144','transaction_date':sale_date,'shares':shares,'price':None,'value':value,'ownership_after':None,'detail':broker,'details_json':details}
    ev['summary']='Proposed affiliate sale'
    if shares:ev['summary']+=f' of {shares:,.0f} shares'
    if value:ev['summary']+=f' (~${value:,.0f})'
    sc,rs=score_event(ev);ev['score']=sc;ev['score_band']=band(sc);ev['reasons']='; '.join(rs);return ev

def parse_ownership(raw:bytes,filing:dict,ticker:str,cik:str)->dict:
    actor=''; pct=None; issuer=filing.get('company',''); symbol=ticker; text=''; details={}
    try:
        root=ET.fromstring(raw); vals=_leaf_map(root); issuer=_first(vals,'nameOfIssuer','issuerName') or issuer; symbol=_first(vals,'issuerTradingSymbol','tradingSymbol') or symbol
        actor=_first(vals,'reportingPersonName','nameOfReportingPerson','reportingOwnerName','nameOfReportingPersons')
        pct=_float(_first(vals,'percentOfClass','percentageOfClass','percentClass','percentOfClassRepresentedByAmount'))
        shares=_float(_first(vals,'aggregateAmountBeneficiallyOwned','amountBeneficiallyOwned','aggregateAmountBeneficiallyOwnedByEachReportingPerson'))
        sole_vote=_float(_first(vals,'soleVotingPower')); shared_vote=_float(_first(vals,'sharedVotingPower')); sole_disp=_float(_first(vals,'soleDispositivePower')); shared_disp=_float(_first(vals,'sharedDispositivePower'))
        text=' '.join(v for arr in vals.values() for v in arr[:1])
        details={'shares_beneficially_owned':shares,'sole_voting_power':sole_vote,'shared_voting_power':shared_vote,'sole_dispositive_power':sole_disp,'shared_dispositive_power':shared_disp}
    except Exception:
        soup=BeautifulSoup(raw,'html.parser'); text=soup.get_text(' ',strip=True)
    if pct is None:
        m=re.search(r'(?:percent(?:age)?\s+of\s+class|percent of class represented[^0-9]{0,80})(\d{1,2}(?:\.\d+)?)\s*%',text,re.I)
        if m:pct=_float(m.group(1))
    # Pull a compact Item 4 / Purpose description for 13D when present.
    purpose=''
    if '13D' in filing.get('form','').upper():
        plain=BeautifulSoup(raw,'html.parser').get_text(' ',strip=True)
        m=re.search(r'Item\s*4\.?\s*(?:Purpose of Transaction)?\s*(.{0,5000}?)(?=Item\s*5\b|Item\s*6\b|$)',plain,re.I|re.S)
        if m: purpose=' '.join(m.group(1).split())[:1800]
    details['purpose']=purpose
    ev={**filing,'event_id':filing['accession'],'event_type':'ownership','company':issuer,'ticker':symbol,'cik':cik,'actor':actor,'role':'Beneficial owner','transaction_code':'','transaction_date':filing.get('report_date',''),'shares':details.get('shares_beneficially_owned'),'price':None,'value':None,'ownership_after':pct,'detail':purpose[:1200],'details_json':details}
    ev['summary']=('New ' if not filing['form'].endswith('/A') else '')+f"{filing['form'].replace('SC ','Schedule ')} beneficial ownership filing"
    if pct:ev['summary']+=f' reporting {pct:.1f}% ownership'
    sc,rs=score_event(ev);ev['score']=sc;ev['score_band']=band(sc);ev['reasons']='; '.join(rs);return ev

ITEM_LABELS={'1.01':'Material agreement','1.02':'Material agreement terminated','2.01':'Acquisition or disposition','2.02':'Earnings / operating results','2.03':'New debt or financial obligation','2.04':'Triggering event affecting an obligation','2.05':'Exit or disposal plan','2.06':'Material impairment','3.01':'Exchange listing/compliance event','3.02':'Unregistered sale of equity','3.03':'Material modification to security-holder rights','4.01':'Auditor/accountant change','4.02':'Financial statements should no longer be relied upon','5.01':'Change in control','5.02':'Director/executive change or compensation event','5.03':'Charter/bylaw change','5.07':'Shareholder vote','5.08':'Shareholder director nominations','6.01':'ABS informational/computational material','7.01':'Regulation FD disclosure','8.01':'Other material event','9.01':'Financial statements/exhibits'}

def parse_8k(raw:bytes,filing:dict,ticker:str,cik:str)->dict:
    items=filing.get('items','') or ''; soup=BeautifulSoup(raw,'html.parser'); plain=' '.join(soup.get_text(' ',strip=True).split())
    if not items:
        found=[]
        for code in ITEM_LABELS:
            if re.search(rf'Item\s+{re.escape(code)}\b',plain,re.I):found.append(code)
        items=', '.join(found)
    codes=[]
    for m in re.finditer(r'\b\d\.\d{2}\b',items):
        if m.group(0) not in codes:codes.append(m.group(0))
    if not codes:
        for code in ITEM_LABELS:
            if re.search(rf'Item\s+{re.escape(code)}\b',plain,re.I):codes.append(code)
    sections=[]
    for code in codes[:5]:
        m=re.search(rf'Item\s+{re.escape(code)}\b\s*[.:\-]?\s*(.*?)(?=Item\s+\d\.\d{{2}}\b|SIGNATURES?\b|$)',plain,re.I|re.S)
        body=' '.join(m.group(1).split())[:1400] if m else ''
        sections.append({'code':code,'label':ITEM_LABELS.get(code,'8-K item'),'summary_text':body})
    event_facts=extract_8k_event(codes,sections)
    details={'item_codes':codes,'items':[ITEM_LABELS.get(c,'8-K item') for c in codes],'sections':sections,'event_facts':event_facts}
    label=', '.join(ITEM_LABELS.get(c,c) for c in codes[:3])
    ev={**filing,'event_id':filing['accession'],'event_type':'material_event','ticker':ticker,'cik':cik,'actor':'','role':'','transaction_code':'','transaction_date':filing.get('report_date',''),'shares':None,'price':None,'value':None,'ownership_after':None,'detail':items,'details_json':details}
    ev['summary']='8-K: '+label if label else '8-K filed'
    sc,rs=score_event(ev)
    if any(x in codes for x in ['1.01','2.01','2.05','2.06','3.01','3.02','4.01','4.02','5.01','5.02']): sc=min(100,sc+8);rs.append('higher-impact 8-K item')
    ev['score']=sc;ev['score_band']=band(sc);ev['reasons']='; '.join(rs);return ev

def _generic_event(e,ticker,cik,company):
    g={**e,'event_id':e.get('accession') or hashlib.sha1(str(e).encode()).hexdigest(),'event_type':'filing','ticker':ticker,'cik':cik,'company':company,'actor':'','role':'','transaction_code':'','transaction_date':e.get('report_date',''),'shares':None,'price':None,'value':None,'ownership_after':None,'detail':'','details_json':{},'summary':f"{e.get('form','Filing')} filed"}
    sc,rs=score_event(g);g['score']=sc;g['score_band']=band(sc);g['reasons']='; '.join(rs);return g

def _valid_ticker(value:str)->str:
    t=(value or '').upper().strip()
    return t if t and len(t)<=12 and re.fullmatch(r'[A-Z0-9][A-Z0-9.\-]*',t) else ''

def scan_market(client:SecClient,max_filings:int=32,feed_count:int=35,on_event=None)->list[dict]:
    _,by_cik=client.ticker_maps(); entries=[]; seen=set()
    for form in FORMS:
        try: batch=parse_atom(client.current_feed(form,0,feed_count),form)
        except Exception:continue
        for e in batch:
            key=e.get('accession')
            if key and key not in seen:seen.add(key);entries.append(e)
    priority={'4':0,'144':1,'SC 13D':2,'SC 13D/A':3,'SC 13G':4,'SC 13G/A':5,'8-K':6}
    entries.sort(key=lambda x:(priority.get(x.get('form'),9),x.get('filed_at','')),reverse=False); entries=entries[:max_filings]; events=[]
    for e in entries:
        cik=e.get('cik',''); info=by_cik.get(cik,{}); ticker=_valid_ticker(info.get('ticker','')); company=info.get('title') or e.get('company',''); e['company']=company;e.setdefault('report_date','');e.setdefault('primary_document','');produced=[]
        try:
            if not cik: produced=[_generic_event(e,ticker,cik,company)]
            else:
                sub=client.submissions(cik); meta=_find_primary(sub,e['accession']) or {}; e.update(meta); primary=e.get('primary_document',''); raw=client.filing_document(cik,e['accession'],primary) if primary else b''; form=e.get('form','')
                if form in {'4','4/A'} and b'<ownershipDocument' in raw[:8000]: produced=parse_form4(raw,e,ticker,cik)
                elif form=='144': produced=[parse_144(raw,e,ticker,cik)]
                elif form.startswith('SC 13'): produced=[parse_ownership(raw,e,ticker,cik)]
                elif form.startswith('8-K'): produced=[parse_8k(raw,e,ticker,cik)]
                else: produced=[_generic_event(e,ticker,cik,company)]
        except Exception as ex:
            g=_generic_event(e,ticker,cik,company); g['detail']=f'Parser fallback: {type(ex).__name__}'; produced=[g]
        for ev in produced:
            ev['ticker']=_valid_ticker(ev.get('ticker',''))
            if not ev['ticker']:continue
            if on_event:on_event(ev)
            else:events.append(ev)
    return [] if on_event else events
