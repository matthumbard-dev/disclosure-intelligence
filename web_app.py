from __future__ import annotations
import gc, os, threading, time, resource
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import FileResponse
from db import read_events,recent_tickers,upsert_event,set_meta,get_meta,purge_demo
from sec_client import SecClient,scan_market
from market_layers import price_snapshot,gdelt_news,cftc_cot,finra_short_interest,reddit_status,options_status,social_status
from market_store import init_market,put_price,put_news,put_cot,read_market

BASE=Path(__file__).resolve().parent
app=FastAPI(title='Disclosure Intelligence Low Memory')
_job_lock=threading.Lock()

def now_iso(): return datetime.now(timezone.utc).isoformat()

def memory_mb():
    # Linux ru_maxrss is KB. This is peak RSS, useful for Render diagnostics.
    return round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/1024,1)

def stale(key,minutes):
    s=get_meta(key,'')
    if not s:return True
    try:return (datetime.now(timezone.utc)-datetime.fromisoformat(s)).total_seconds()>minutes*60
    except Exception:return True

def sync_sec_bounded():
    ua=os.getenv('SEC_USER_AGENT','').strip()
    if not ua or '@' not in ua:
        set_meta('last_error','SEC_USER_AGENT is not configured on the server.'); return 0
    set_meta('sync_status','syncing'); set_meta('last_error',''); set_meta('memory_before_sec',memory_mb())
    count=0
    try:
        client=SecClient(ua)
        def save_one(ev):
            nonlocal count
            upsert_event(ev); count+=1
        # Hard bounds are deliberate for Render Free (512 MB).
        scan_market(client,max_filings=int(os.getenv('SEC_BATCH_SIZE','32')),feed_count=35,on_event=save_one)
        set_meta('last_sync',now_iso()); set_meta('last_count',count); set_meta('sync_status','idle')
        return count
    except Exception as e:
        set_meta('last_error',str(e)[:800]); set_meta('sync_status','error'); return count
    finally:
        gc.collect(); set_meta('memory_after_sec',memory_mb())

def sync_layers_bounded():
    set_meta('layer_status','syncing'); set_meta('layer_error',''); set_meta('memory_before_layers',memory_mb())
    try:
        pairs=recent_tickers(14,int(os.getenv('ENRICH_TICKERS','10')))
        for ticker,company in pairs:
            put_price(price_snapshot(ticker))
            put_news(gdelt_news(ticker,company,3))
            gc.collect(); time.sleep(.08)
        put_cot(cftc_cot())
        set_meta('layer_sync',now_iso()); set_meta('layer_status','idle')
    except Exception as e:
        set_meta('layer_error',str(e)[:800]); set_meta('layer_status','error')
    finally:
        gc.collect(); set_meta('memory_after_layers',memory_mb())

def full_cycle():
    if not _job_lock.acquire(blocking=False): return
    try:
        set_meta('job_status','running')
        sync_sec_bounded()
        # Never overlap SEC parsing and enrichment in memory.
        gc.collect()
        sync_layers_bounded()
        set_meta('job_status','idle'); set_meta('last_cycle',now_iso())
    finally:
        gc.collect(); _job_lock.release()

def scheduler():
    # Small initial delay lets the web process become healthy before outbound work.
    time.sleep(8)
    if stale('last_sync',30): full_cycle()
    while True:
        time.sleep(1800)
        if stale('last_sync',25): full_cycle()

@app.on_event('startup')
def startup():
    init_market(); purge_demo()
    set_meta('boot_memory_mb',memory_mb())
    threading.Thread(target=scheduler,daemon=True,name='bounded-collector').start()

@app.get('/')
def home(): return FileResponse(BASE/'index.html')

@app.get('/api/events')
def events(days:int=30):
    # Read-only: browsing the dashboard never starts collection work.
    return read_events(max(1,min(days,365)),1200)

@app.get('/api/market')
def market():
    p,n,c=read_market()
    return {'prices':p,'news':n,'commodities':c,'status':{'layer_sync':get_meta('layer_sync'),'layer_status':get_meta('layer_status','idle'),'layer_error':get_meta('layer_error','')}}

@app.get('/api/connectors')
def connectors():
    symbols=[x[0] for x in recent_tickers(30,25)]
    return {'short_interest':finra_short_interest(symbols),'reddit':reddit_status(),'options':options_status(),'broader_social':social_status()}

@app.get('/api/status')
def status():
    return {'last_sync':get_meta('last_sync'),'last_count':get_meta('last_count','0'),'sync_status':get_meta('sync_status','idle'),'last_error':get_meta('last_error',''),'layer_sync':get_meta('layer_sync'),'layer_status':get_meta('layer_status','idle'),'job_status':get_meta('job_status','idle'),'memory_mb':memory_mb(),'boot_memory_mb':get_meta('boot_memory_mb',''),'memory_after_sec':get_meta('memory_after_sec',''),'memory_after_layers':get_meta('memory_after_layers','')}

@app.post('/api/sync-market')
def sync(background_tasks:BackgroundTasks):
    if _job_lock.locked(): return {'ok':True,'message':'A bounded collection cycle is already running.'}
    background_tasks.add_task(full_cycle)
    return {'ok':True,'message':'Bounded SEC + market enrichment cycle started.'}

@app.post('/api/sync-layers')
def layers(background_tasks:BackgroundTasks):
    if _job_lock.locked(): return {'ok':True,'message':'A collection cycle is already running.'}
    background_tasks.add_task(full_cycle)
    return {'ok':True}

@app.get('/health')
def health(): return {'ok':True,'memory_mb':memory_mb(),'job':get_meta('job_status','idle')}
