from __future__ import annotations
import os, threading, webbrowser
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from db import read_events, upsert_events, clear_db
from demo_data import demo_events
from sec_client import SecClient, ingest_ticker

BASE = Path(__file__).resolve().parent
app = FastAPI(title='Disclosure Intelligence')

class SyncRequest(BaseModel):
    user_agent: str
    tickers: list[str]
    max_filings: int = 80

@app.get('/')
def home():
    return FileResponse(BASE / 'index.html')

@app.get('/api/events')
def events(days: int = 30):
    days = max(1, min(days, 365))
    df = read_events(days)
    if df.empty:
        return []
    import json
    return json.loads(df.to_json(orient='records', date_format='iso'))

@app.post('/api/demo')
def load_demo():
    n = upsert_events(demo_events())
    return {'ok': True, 'events': n}

@app.post('/api/clear')
def clear():
    clear_db()
    return {'ok': True}

@app.post('/api/sync')
def sync(req: SyncRequest):
    if not req.user_agent or '@' not in req.user_agent:
        raise HTTPException(400, 'SEC User-Agent must include an email address.')
    tickers = sorted({t.strip().upper() for t in req.tickers if t.strip()})
    if not tickers:
        raise HTTPException(400, 'Enter at least one ticker.')
    try:
        client = SecClient(req.user_agent)
        mapping = client.ticker_map()
        all_events = []
        missing = []
        for ticker in tickers:
            info = mapping.get(ticker)
            if not info:
                missing.append(ticker)
                continue
            all_events.extend(ingest_ticker(client, ticker, info['cik'], info['title'], max_filings=req.max_filings))
        n = upsert_events(all_events)
        return {'ok': True, 'events': n, 'missing': missing}
    except Exception as exc:
        raise HTTPException(500, str(exc))

@app.get('/health')
def health():
    return {'ok': True}

if __name__ == '__main__':
    import uvicorn
    port = int(os.getenv('PORT', '8765'))
    threading.Timer(1.2, lambda: webbrowser.open(f'http://127.0.0.1:{port}')).start()
    uvicorn.run(app, host='127.0.0.1', port=port, log_level='warning')
