from __future__ import annotations
import csv, io, json, os, re, time
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
import requests

S=requests.Session(); S.headers.update({'User-Agent':'DisclosureIntelligence/2.0 market research dashboard'})

def _get(url, params=None, timeout=12, headers=None):
    h=dict(S.headers); h.update(headers or {})
    r=S.get(url,params=params,timeout=timeout,headers=h); r.raise_for_status(); return r

def price_snapshot(ticker):
    # Stooq: free delayed daily OHLCV, no key. Good baseline; provider is swappable.
    try:
        sym=ticker.lower()+'.us'
        r=_get('https://stooq.com/q/d/l/',{'s':sym,'d1':(datetime.now()-timedelta(days=55)).strftime('%Y%m%d'),'d2':datetime.now().strftime('%Y%m%d'),'i':'d'})
        rows=list(csv.DictReader(io.StringIO(r.text)))
        rows=[x for x in rows if x.get('Close') not in ('','N/D',None)]
        if len(rows)<2:return None
        closes=[float(x['Close']) for x in rows]; vols=[float(x.get('Volume') or 0) for x in rows]
        last=rows[-1]; prev=closes[-2]
        avg20=sum(vols[-21:-1])/max(1,len(vols[-21:-1]))
        def ret(n): return round((closes[-1]/closes[-1-n]-1)*100,2) if len(closes)>n else None
        return {'ticker':ticker,'asof':last['Date'],'price':closes[-1],'change_1d':round((closes[-1]/prev-1)*100,2),'change_5d':ret(5),'change_30d':ret(30),'volume':vols[-1],'avg_volume_20d':round(avg20),'relative_volume':round(vols[-1]/avg20,2) if avg20 else None,'source':'Stooq delayed EOD'}
    except Exception:return None

def gdelt_news(ticker, company, maxrecords=3):
    # Broad global news discovery; links remain to original publishers.
    try:
        q=f'("{company}" OR "${ticker}" OR "{ticker} stock")'
        r=_get('https://api.gdeltproject.org/api/v2/doc/doc',{'query':q,'mode':'artlist','maxrecords':maxrecords,'format':'json','sort':'datedesc'},timeout=18)
        d=r.json(); out=[]
        for a in d.get('articles',[]):
            out.append({'ticker':ticker,'title':a.get('title',''),'url':a.get('url',''),'domain':a.get('domain',''),'published':a.get('seendate',''),'language':a.get('language',''),'source':'GDELT'})
        return out
    except Exception:return []

def cftc_cot():
    # Current disaggregated futures-only COT. Socrata dataset 72hh-3qpy.
    try:
        url='https://publicreporting.cftc.gov/resource/72hh-3qpy.json'
        params={'$limit':300,'$order':'report_date_as_yyyy_mm_dd DESC'}
        rows=_get(url,params,timeout=20).json()
        if not rows:return []
        latest=max(x.get('report_date_as_yyyy_mm_dd','') for x in rows)
        rows=[x for x in rows if x.get('report_date_as_yyyy_mm_dd')==latest]
        wanted=['GOLD','SILVER','COPPER','CRUDE OIL','NATURAL GAS','CORN','WHEAT','SOYBEANS','COCOA','COFFEE','COTTON','SUGAR']
        out=[]
        for x in rows:
            name=x.get('market_and_exchange_names','').upper()
            label=next((w for w in wanted if w in name),None)
            if not label:continue
            try:
                lng=float(x.get('m_money_positions_long_all') or 0); sht=float(x.get('m_money_positions_short_all') or 0)
                ch_l=float(x.get('change_in_m_money_long_all') or 0); ch_s=float(x.get('change_in_m_money_short_all') or 0)
                out.append({'commodity':label,'market':x.get('market_and_exchange_names'),'report_date':latest[:10],'managed_money_long':lng,'managed_money_short':sht,'managed_money_net':lng-sht,'weekly_net_change':ch_l-ch_s,'open_interest':float(x.get('open_interest_all') or 0),'source':'CFTC COT'})
            except Exception:pass
        # one representative per label
        seen=set(); clean=[]
        for x in out:
            if x['commodity'] not in seen:clean.append(x);seen.add(x['commodity'])
        return clean
    except Exception:return []

def finra_short_interest(symbols):
    # FINRA Query API requires credentials for production use. We expose readiness/status
    # rather than scraping around access controls.
    token=os.getenv('FINRA_API_TOKEN','').strip()
    if not token:return {'available':False,'reason':'Add FINRA_API_TOKEN in Render to enable FINRA short-interest ingestion.','items':[]}
    return {'available':False,'reason':'FINRA credential detected; adapter needs account-specific dataset entitlement configuration.','items':[]}

def reddit_status():
    cid=os.getenv('REDDIT_CLIENT_ID','').strip(); sec=os.getenv('REDDIT_CLIENT_SECRET','').strip()
    if cid and sec:return {'available':True,'reason':'Credentials configured. Reddit ingestion can be enabled after confirming approved Data API access/terms.'}
    return {'available':False,'reason':'Requires approved Reddit Data API access plus REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET.'}

def options_status():
    key=os.getenv('POLYGON_API_KEY','').strip()
    return {'available':bool(key),'reason':'Polygon market-data key configured.' if key else 'Add POLYGON_API_KEY for options/open-interest/IV data.'}

def social_status():
    x=os.getenv('X_BEARER_TOKEN','').strip()
    return {'available':bool(x),'reason':'X API token configured.' if x else 'Add X_BEARER_TOKEN for broader social monitoring.'}
