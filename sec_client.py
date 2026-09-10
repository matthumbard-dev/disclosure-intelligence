from __future__ import annotations
import re, time, hashlib, json, logging, traceback
import xml.etree.ElementTree as ET

import html as _html

def _clean_sec_xml_payload(payload):
    """Normalize SEC ownership XML embedded in HTML/submission wrappers."""
    if payload is None:
        return ""
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")
    text = str(payload).lstrip("\ufeff \t\r\n")
    # If a SEC submission/document wrapper was returned, isolate ownership XML.
    starts = [p for p in (text.find("<ownershipDocument"), text.find("<?xml")) if p >= 0]
    if starts:
        start = min(starts)
        # Prefer ownershipDocument start if XML declaration occurs in wrapper noise.
        od = text.find("<ownershipDocument")
        if od >= 0:
            start = od
        end = text.find("</ownershipDocument>", start)
        if end >= 0:
            text = text[start:end + len("</ownershipDocument>")]
        else:
            text = text[start:]
    # XML declarations are illegal after any preceding wrapper/content.
    text = re.sub(r"<\?xml[^>]*\?>", "", text, flags=re.I).strip()
    # SEC ownership XML occasionally contains HTML-ish entities in text fields.
    text = text.replace("&nbsp;", " ")
    # Escape bare ampersands while preserving legal XML entities.
    text = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9A-Fa-f]+;)", "&amp;", text)
    return text

def _parse_sec_xml(payload):
    cleaned = _clean_sec_xml_payload(payload)
    try:
        return ET.fromstring(cleaned)
    except ET.ParseError:
        # Last-resort repair for common SEC XHTML line-break tags inside text.
        repaired = re.sub(r"<br\s*>", "<br/>", cleaned, flags=re.I)
        repaired = re.sub(r"<hr\s*>", "<hr/>", repaired, flags=re.I)
        return ET.fromstring(repaired)

from dataclasses import dataclass
from html import unescape
from urllib.parse import urlencode
from datetime import datetime, timezone, timedelta
import requests
from bs4 import BeautifulSoup
from scoring import score_event, band
from event_extractor import extract_8k_event

SEC_DATA='https://data.sec.gov'
SEC_WWW='https://www.sec.gov'
SEC_TICKERS='https://www.sec.gov/files/company_tickers.json'
CURRENT='https://www.sec.gov/cgi-bin/browse-edgar'
FORMS=['4','4/A','144','SC 13D','SC 13D/A','SC 13G','SC 13G/A','8-K','8-K/A']

LOG=logging.getLogger('disclosure.sec')

def _log(msg,*args):
    LOG.info('[SEC] '+msg,*args)


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
    def filing_index(self,cik:str,accession:str):
        base=f"{SEC_WWW}/Archives/edgar/data/{int(cik)}/{accession.replace('-','')}"
        return self._get(base+'/index.json').json()

    def primary_from_index(self,cik:str,accession:str,form_hint:str=''):
        try:
            data=self.filing_index(cik,accession)
            items=(data.get('directory') or {}).get('item') or []
            names=[str(x.get('name','')) for x in items if isinstance(x,dict)]
            # Prefer XML ownership/Form 144 documents, then HTML filing docs.
            xml=[n for n in names if n.lower().endswith('.xml') and not any(x in n.lower() for x in ('_cal.xml','_def.xml','_lab.xml','_pre.xml','filingsummary.xml','metalinks'))]
            html=[n for n in names if n.lower().endswith(('.htm','.html')) and 'index' not in n.lower()]
            if form_hint in {'4','4/A','144'} and xml:
                # XML primary docs are usually small and contain the structured filing.
                return sorted(xml,key=lambda n:(0 if ('form4' in n.lower() or 'primary' in n.lower()) else 1,len(n)))[0]
            return (html or xml or [''])[0]
        except Exception:
            return ''

    def current_feed(self,form:str,start:int=0,count:int=100):
        q=urlencode({'action':'getcurrent','type':form,'owner':'include','start':start,'count':count,'output':'atom'})
        return self._get(f'{CURRENT}?{q}').content

    def daily_master_index(self, day=None):
        day = day or datetime.now(timezone.utc).date()
        qtr = ((day.month - 1) // 3) + 1
        url = f'{SEC_WWW}/Archives/edgar/daily-index/{day.year}/QTR{qtr}/master.{day:%Y%m%d}.idx'
        return self._get(url).text

    def direct_document(self,url:str,max_bytes:int=3000000):
        wait=self.min_interval-(time.time()-self._last)
        if wait>0: time.sleep(wait)
        r=self.s.get(url,timeout=30,stream=True); self._last=time.time(); r.raise_for_status()
        chunks=[]; total=0
        try:
            for chunk in r.iter_content(65536):
                if not chunk: continue
                total += len(chunk)
                if total>max_bytes: break
                chunks.append(chunk)
            return b''.join(chunks)
        finally:
            r.close()

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
        if not cik:
            m=re.search(r'[?&]CIK=(\d+)',href,re.I)
            if m:cik=m.group(1).zfill(10)
        if not cik:
            m=re.search(r'CIK[^0-9]{0,20}(\d{6,10})',summary,re.I)
            if m:cik=m.group(1).zfill(10)
        if not cik:
            m=re.search(r'\((\d{6,10})\)',title)
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

def extract_submission_document(raw:bytes, form_hint:str)->bytes:
    """Extract the primary document of a given TYPE from an EDGAR .txt submission.

    This bypasses a second submissions/index lookup and works directly from the
    archive object referenced by the daily master index.
    """
    if not raw: return raw
    head=raw[:5000].decode('utf-8','ignore')
    if '<DOCUMENT>' not in head.upper() and b'<DOCUMENT>' not in raw[:200000].upper():
        return raw
    text=raw.decode('utf-8','ignore')
    docs=re.findall(r'<DOCUMENT>(.*?)</DOCUMENT>',text,re.I|re.S)
    wanted=form_hint.upper().replace('SC ','SC ')
    best=''
    fallback=''
    for d in docs:
        tm=re.search(r'<TYPE>\s*([^\r\n<]+)',d,re.I)
        typ=(tm.group(1).strip().upper() if tm else '')
        xm=re.search(r'<TEXT>(.*?)(?:</TEXT>|$)',d,re.I|re.S)
        body=xm.group(1) if xm else d
        if not fallback and body.strip(): fallback=body
        if typ==wanted or (wanted=='4/A' and typ=='4/A') or (wanted.startswith('8-K') and typ.startswith('8-K')) or (wanted.startswith('SC 13') and typ.startswith(wanted.split('/')[0])):
            best=body; break
    body=best or fallback
    return body.encode('utf-8','ignore') if body else raw

def parse_form4(raw:bytes,filing:dict,ticker:str,cik:str)->list[dict]:
    root=_parse_sec_xml(raw); issuer=_text(root,'issuer/issuerName') or filing.get('company',''); issuer_ticker=_text(root,'issuer/issuerTradingSymbol') or ticker
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
    root=_parse_sec_xml(raw); vals=_leaf_map(root)
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
        root=_parse_sec_xml(raw); vals=_leaf_map(root); issuer=_first(vals,'nameOfIssuer','issuerName') or issuer; symbol=_first(vals,'issuerTradingSymbol','tradingSymbol') or symbol
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

def parse_master_index(text:str, target_forms=None):
    target_forms=set(target_forms or FORMS)
    rows=[]
    in_data=False
    for line in text.splitlines():
        if not in_data:
            if line.startswith('-----'):
                in_data=True
            continue
        parts=line.split('|')
        if len(parts)!=5:
            continue
        cik,company,form,filed,filename=[x.strip() for x in parts]
        if form not in target_forms:
            continue
        m=re.search(r'(\d{10}-\d{2}-\d{6})',filename)
        acc=m.group(1) if m else ''
        if not acc:
            continue
        rows.append({
            'form':form,'accession':acc,'cik':str(cik).zfill(10),
            'filed_at':filed,'company':company,
            'source_url':f'{SEC_WWW}/Archives/{filename}',
            'master_filename':filename
        })
    return rows

def recent_index_entries(client:SecClient, days_back:int=7):
    """Return the most recent *completed* daily index.

    SEC daily index files are generally created after the filing day closes, so an
    intraday application must not assume today's master index exists. We walk
    backward until we find the latest available business-day index.
    """
    today=datetime.now(timezone.utc).date()
    for offset in range(days_back):
        day=today-timedelta(days=offset)
        try:
            rows=parse_master_index(client.daily_master_index(day))
        except Exception:
            continue
        if rows:
            return rows
    return []


def _discover_candidates(client:SecClient, feed_count:int=80):
    """Merge intraday current feeds with the latest completed daily index."""
    seen=set(); entries=[]; stats={'atom_entries':0,'index_entries':0,'discovery_errors':0,'discovery_error_details':[]}
    _log('Discovery started. feed_count=%s forms=%s',feed_count,','.join(FORMS))

    for form in FORMS:
        try:
            raw=client.current_feed(form,0,feed_count)
            batch=parse_atom(raw,form)
            stats['atom_entries'] += len(batch)
            _log('Current feed %-7s -> %d entries',form,len(batch))
        except Exception as exc:
            stats['discovery_errors'] += 1
            detail=f'{form}: {type(exc).__name__}: {exc}'[:240]
            stats['discovery_error_details'].append(detail)
            _log('Current feed %-7s FAILED: %s',form,detail)
            batch=[]
        for e in batch:
            key=e.get('accession') or (e.get('source_url'),e.get('form'))
            if key and key not in seen:
                seen.add(key); entries.append(e)

    try:
        idx=recent_index_entries(client,days_back=7)
        stats['index_entries']=len(idx)
        _log('Latest completed daily index -> %d target-form entries',len(idx))
    except Exception as exc:
        stats['discovery_errors'] += 1
        detail=f'index: {type(exc).__name__}: {exc}'[:240]
        stats['discovery_error_details'].append(detail)
        _log('Daily index FAILED: %s',detail)
        idx=[]
    for e in idx:
        key=e.get('accession') or (e.get('source_url'),e.get('form'))
        if key and key not in seen:
            seen.add(key); entries.append(e)

    priority={'4':0,'4/A':1,'144':2,'SC 13D':3,'SC 13D/A':4,'SC 13G':5,'SC 13G/A':6,'8-K':7,'8-K/A':8}
    entries.sort(key=lambda x:(x.get('filed_at',''), -priority.get(x.get('form'),9)), reverse=True)
    stats['candidate_entries']=len(entries)
    _log('Discovery complete -> %d unique candidates',len(entries))
    return entries,stats

def scan_market(client:SecClient,max_filings:int=32,feed_count:int=80,on_event=None)->list[dict]:
    """Process SEC candidates until max_filings usable, ticker-resolved events are saved.

    Direct-source parsing is attempted first. For daily-index .txt objects, the
    matching <DOCUMENT>/<TYPE> payload is extracted locally. Only then do we fall
    back to submissions/index lookups.
    """
    _log('SCAN START max_usable=%d feed_count=%d',max_filings,feed_count)
    stats={}
    try:
        _,by_cik=client.ticker_maps()
        _log('Ticker map loaded -> %d CIK mappings',len(by_cik))
    except Exception as exc:
        _log('Ticker map FAILED: %s: %s',type(exc).__name__,exc)
        by_cik={}

    entries,stats=_discover_candidates(client,feed_count=feed_count)
    events=[]; accepted=0; attempted=0; parser_errors=0; no_ticker=0; no_primary=0; fetch_errors=0; direct_ok=0; fallback_ok=0
    error_samples=[]; form_attempts={}; form_saved={}
    max_attempts=max(max_filings*10,200)

    for e in entries:
        if accepted>=max_filings or attempted>=max_attempts: break
        attempted += 1
        form=e.get('form',''); form_attempts[form]=form_attempts.get(form,0)+1
        cik=str(e.get('cik','') or '').zfill(10) if e.get('cik') else ''
        info=by_cik.get(cik,{})
        ticker=_valid_ticker(info.get('ticker',''))
        company=info.get('title') or e.get('company','')
        e['company']=company; e.setdefault('report_date',''); e.setdefault('primary_document','')
        if attempted<=10 or attempted%25==0:
            _log('Candidate %d/%d form=%s accession=%s filer_cik=%s initial_ticker=%s source=%s',attempted,min(len(entries),max_attempts),form,e.get('accession',''),cik,ticker,e.get('source_url','')[-90:])
        produced=[]; raw=None
        try:
            # Shortest path: parse the URL SEC already gave us.
            src=e.get('source_url','')
            if src and src.startswith('http'):
                try:
                    direct=client.direct_document(src)
                    if src.lower().endswith('.txt'):
                        direct=extract_submission_document(direct,form)
                    # Atom links may be filing detail HTML rather than primary docs; only
                    # count direct path as usable when it contains substantive bytes.
                    if direct and len(direct)>100:
                        raw=direct; direct_ok+=1
                        _log('Direct fetch OK form=%s accession=%s bytes=%d',form,e.get('accession',''),len(raw))
                except Exception as exc:
                    fetch_errors += 1
                    if len(error_samples)<12: error_samples.append(f'direct {form} {e.get("accession","")}: {type(exc).__name__}: {exc}'[:300])
                    _log('Direct fetch FAILED form=%s accession=%s: %s: %s',form,e.get('accession',''),type(exc).__name__,exc)

            # Fallback to filing directory/submissions if direct bytes were unavailable
            # or parsing them later fails.
            def fallback_raw():
                nonlocal fallback_ok, no_primary
                meta={}
                try:
                    if cik:
                        sub=client.submissions(cik); meta=_find_primary(sub,e.get('accession','')) or {}
                except Exception as exc:
                    _log('Submissions lookup failed accession=%s: %s',e.get('accession',''),exc)
                e.update(meta)
                primary=e.get('primary_document','')
                if not primary and cik:
                    primary=client.primary_from_index(cik,e.get('accession',''),form); e['primary_document']=primary
                if not primary:
                    no_primary += 1; return None
                b=client.filing_document(cik,e.get('accession',''),primary); fallback_ok+=1; return b

            def parse_bytes(b):
                if form in {'4','4/A'}: return parse_form4(b,e,ticker,cik)
                if form=='144': return [parse_144(b,e,ticker,cik)]
                if form.startswith('SC 13'): return [parse_ownership(b,e,ticker,cik)]
                if form.startswith('8-K'): return [parse_8k(b,e,ticker,cik)]
                return []

            if raw is not None:
                try:
                    produced=parse_bytes(raw)
                except Exception as first_exc:
                    if parser_errors < 5 or attempted % 25 == 0:
                        _log('Direct parse FAILED form=%s accession=%s: %s: %s; trying fallback',form,e.get('accession',''),type(first_exc).__name__,first_exc)
                    fb=fallback_raw()
                    if fb is not None: produced=parse_bytes(fb)
                    else: raise first_exc
            else:
                fb=fallback_raw()
                if fb is None: continue
                produced=parse_bytes(fb)
        except Exception as exc:
            parser_errors += 1
            if len(error_samples)<12: error_samples.append(f'parse {form} {e.get("accession","")}: {type(exc).__name__}: {exc}'[:300])
            if parser_errors <= 5 or attempted % 25 == 0:
                _log('Candidate FAILED form=%s accession=%s: %s: %s',form,e.get('accession',''),type(exc).__name__,exc)
            continue

        for ev in produced:
            ev['ticker']=_valid_ticker(ev.get('ticker',''))
            if not ev['ticker']:
                no_ticker += 1
                if no_ticker<=10: _log('Discarded no ticker form=%s accession=%s actor=%s company=%s',form,e.get('accession',''),ev.get('actor',''),ev.get('company',''))
                continue
            if on_event: on_event(ev)
            else: events.append(ev)
            accepted += 1; form_saved[form]=form_saved.get(form,0)+1
            _log('SAVED %d/%d ticker=%s form=%s event=%s score=%s',accepted,max_filings,ev['ticker'],form,ev.get('event_type',''),ev.get('score',''))
            if accepted>=max_filings: break

    stats.update({'attempted':attempted,'accepted':accepted,'parser_errors':parser_errors,'fetch_errors':fetch_errors,'no_ticker':no_ticker,'no_primary':no_primary,'direct_fetch_ok':direct_ok,'fallback_fetch_ok':fallback_ok,'max_attempts':max_attempts,'form_attempts':form_attempts,'form_saved':form_saved,'error_samples':error_samples})
    client.last_scan_stats=stats
    _log('SCAN COMPLETE accepted=%d attempted=%d candidates=%d atom=%d index=%d parse_errors=%d fetch_errors=%d no_ticker=%d no_primary=%d direct_ok=%d fallback_ok=%d',accepted,attempted,stats.get('candidate_entries',0),stats.get('atom_entries',0),stats.get('index_entries',0),parser_errors,fetch_errors,no_ticker,no_primary,direct_ok,fallback_ok)
    if error_samples:
        for sample in error_samples[:8]: _log('ERROR SAMPLE %s',sample)
    return [] if on_event else events

