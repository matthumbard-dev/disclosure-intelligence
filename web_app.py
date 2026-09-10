from __future__ import annotations
import os, threading, time
from datetime import datetime, timezone
from pathlib import Path
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import FileResponse
from db import read_events,upsert_events,set_meta,get_meta
from sec_client import SecClient,scan_market
BASE=Path(__file__).resolve().parent
app=FastAPI(title='Disclosure Intelligence Live')
_lock=threading.Lock()

def sync_market():
    if not _lock.acquire(blocking=False):return
    try:
        ua=os.getenv('SEC_USER_AGENT','').strip()
        if not ua or '@' not in ua:
            set_meta('last_error','SEC_USER_AGENT is not configured on the server.');return
        set_meta('sync_status','syncing');set_meta('last_error','')
        ev=scan_market(SecClient(ua));upsert_events(ev)
        set_meta('last_sync',datetime.now(timezone.utc).isoformat());set_meta('last_count',str(len(ev)));set_meta('sync_status','idle')
    except Exception as e:
        set_meta('last_error',str(e));set_meta('sync_status','error')
    finally:_lock.release()

def stale(minutes=30):
    s=get_meta('last_sync','')
    if not s:return True
    try:return (datetime.now(timezone.utc)-datetime.fromisoformat(s)).total_seconds()>minutes*60
    except:return True

@app.on_event('startup')
def startup():
    if stale(5):threading.Thread(target=sync_market,daemon=True).start()
    def loop():
        while True:
            time.sleep(1800)
            if stale(25):sync_market()
    threading.Thread(target=loop,daemon=True).start()

@app.get('/')
def home():return FileResponse(BASE/'index.html')
@app.get('/api/events')
def events(days:int=30):
    if stale(30):threading.Thread(target=sync_market,daemon=True).start()
    df=read_events(max(1,min(days,365)))
    import json
    return json.loads(df.to_json(orient='records',date_format='iso')) if not df.empty else []
@app.get('/api/status')
def status():return {'last_sync':get_meta('last_sync'),'last_count':get_meta('last_count','0'),'sync_status':get_meta('sync_status','idle'),'last_error':get_meta('last_error','')}
@app.post('/api/sync-market')
def sync(background_tasks:BackgroundTasks):
    background_tasks.add_task(sync_market);return {'ok':True,'message':'Market scan started'}
@app.get('/health')
def health():return {'ok':True}
