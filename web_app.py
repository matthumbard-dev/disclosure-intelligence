from __future__ import annotations
import gc, os, threading, time, resource, logging, json, traceback
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, BackgroundTasks, Response
from fastapi.responses import FileResponse
from db import read_events,recent_tickers,upsert_event,set_meta,get_meta,purge_demo,purge_unresolved
from sec_client import SecClient,scan_market
from market_layers import price_snapshot,gdelt_news,cftc_cot,finra_short_interest,reddit_status,options_status,social_status
from market_store import init_market,put_price,put_news,put_cot,read_market
from story_builder import build_story

BASE=Path(__file__).resolve().parent
app=FastAPI(title='Disclosure Intelligence Instrumented')
_job_lock=threading.Lock()
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s', force=True)
LOG=logging.getLogger('disclosure.app')
def log(msg,*args): LOG.info('[APP] '+msg,*args)

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
    log('SEC stage entered. user_agent_configured=%s',bool(ua and '@' in ua))
    if not ua or '@' not in ua:
        msg='SEC_USER_AGENT is not configured on the server.'
        set_meta('last_error',msg); set_meta('sync_status','error'); log(msg); return 0
    set_meta('sync_status','syncing'); set_meta('sync_stage','starting'); set_meta('last_error',''); set_meta('current_saved_count','0'); set_meta('memory_before_sec',memory_mb())
    count=0
    try:
        client=SecClient(ua)
        def save_one(ev):
            nonlocal count
            upsert_event(ev); count+=1
            set_meta('current_saved_count',count); set_meta('last_partial_update',now_iso())
            set_meta('sync_stage',f'saved {count} usable events')
        batch=int(os.getenv('SEC_BATCH_SIZE','32')); feed=int(os.getenv('SEC_FEED_COUNT','80'))
        log('SEC scan calling scan_market batch=%d feed=%d peak_mem=%.1fMB',batch,feed,memory_mb())
        set_meta('sync_stage','discovering SEC filings')
        scan_market(client,max_filings=batch,feed_count=feed,on_event=save_one)
        stats=getattr(client,'last_scan_stats',{}) or {}
        set_meta('last_scan_stats',json.dumps(stats,separators=(',',':')))
        set_meta('last_sync',now_iso()); set_meta('last_count',count); set_meta('sync_status','idle'); set_meta('sync_stage',f'complete: {count} usable events')
        log('SEC stage complete count=%d stats=%s peak_mem=%.1fMB',count,json.dumps(stats,separators=(',',':'))[:1800],memory_mb())
        return count
    except Exception as e:
        msg=f'{type(e).__name__}: {e}'[:1200]
        set_meta('last_error',msg); set_meta('sync_status','error'); set_meta('sync_stage','failed')
        log('SEC stage EXCEPTION %s\n%s',msg,traceback.format_exc())
        return count
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
    if not _job_lock.acquire(blocking=False):
        log('Cycle request ignored: another cycle is running'); return
    try:
        set_meta('job_status','running'); set_meta('job_started',now_iso())
        log('FULL CYCLE START peak_mem=%.1fMB',memory_mb())
        sec_count=sync_sec_bounded()
        gc.collect()
        log('SEC returned %d usable events; starting enrichment',sec_count)
        set_meta('sync_stage','market enrichment')
        sync_layers_bounded()
        set_meta('job_status','idle'); set_meta('last_cycle',now_iso()); set_meta('sync_stage','idle')
        log('FULL CYCLE COMPLETE peak_mem=%.1fMB',memory_mb())
    except Exception as exc:
        set_meta('job_status','error'); set_meta('last_error',f'{type(exc).__name__}: {exc}'[:1200]); set_meta('sync_stage','cycle failed')
        log('FULL CYCLE EXCEPTION %s\n%s',exc,traceback.format_exc())
    finally:
        gc.collect(); _job_lock.release()

def scheduler():
    # Small initial delay lets the web process become healthy before outbound work.
    time.sleep(8)
    if stale('last_sync',30): full_cycle()
    while True:
        time.sleep(1800)
        if stale('last_sync',25): full_cycle()

@app.middleware('http')
async def no_cache_api(request, call_next):
    response = await call_next(request)
    if request.url.path == '/' or request.url.path.startswith('/api/'):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response

@app.on_event('startup')
def startup():
    init_market(); purge_demo(); purge_unresolved()
    set_meta('boot_memory_mb',memory_mb())
    log('STARTUP db=%s peak_mem=%.1fMB',str(getattr(__import__('db'),'DB_PATH','')),memory_mb())
    threading.Thread(target=scheduler,daemon=True,name='bounded-collector').start()

@app.get('/')
def home(): return FileResponse(BASE/'index.html')

@app.get('/api/events')
def events(days:int=30):
    # Read-only: browsing the dashboard never starts collection work.
    rows=read_events(max(1,min(days,365)),1200)
    for row in rows: row['story']=build_story(row)
    return rows

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
    
    import json
    try: scan_stats=json.loads(get_meta('last_scan_stats','{}') or '{}')
    except Exception: scan_stats={}
    return {'last_sync':get_meta('last_sync'),'last_count':get_meta('last_count','0'),'sync_status':get_meta('sync_status','idle'),'sync_stage':get_meta('sync_stage','idle'),'job_started':get_meta('job_started',''),'last_error':get_meta('last_error',''),'scan_stats':scan_stats,'layer_sync':get_meta('layer_sync'),'layer_status':get_meta('layer_status','idle'),'job_status':get_meta('job_status','idle'),'current_saved_count':get_meta('current_saved_count','0'),'last_partial_update':get_meta('last_partial_update',''),'memory_mb':memory_mb(),'boot_memory_mb':get_meta('boot_memory_mb',''),'memory_after_sec':get_meta('memory_after_sec',''),'memory_after_layers':get_meta('memory_after_layers','')}

@app.post('/api/sync-market')
def sync():
    log('POST /api/sync-market received lock=%s',_job_lock.locked())
    if _job_lock.locked(): return {'ok':True,'message':'A collection cycle is already running.'}
    set_meta('job_status','queued'); set_meta('sync_stage','queued by dashboard')
    threading.Thread(target=full_cycle,daemon=True,name='manual-collector').start()
    return {'ok':True,'message':'Instrumented SEC + market collection started.'}

@app.post('/api/sync-layers')
def layers(background_tasks:BackgroundTasks):
    if _job_lock.locked(): return {'ok':True,'message':'A collection cycle is already running.'}
    background_tasks.add_task(full_cycle)
    return {'ok':True}

@app.get('/health')
def health(): return {'ok':True,'memory_mb':memory_mb(),'job':get_meta('job_status','idle')}
