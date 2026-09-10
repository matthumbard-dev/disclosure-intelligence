from __future__ import annotations
import json
from db import connect

def init_market():
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS market_snapshots(ticker TEXT PRIMARY KEY,payload TEXT,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS news_items(url TEXT PRIMARY KEY,ticker TEXT,title TEXT,domain TEXT,published TEXT,source TEXT,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
        CREATE TABLE IF NOT EXISTS commodity_snapshots(commodity TEXT PRIMARY KEY,payload TEXT,updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
        '''); c.commit()

def put_price(x):
    if not x:return
    with connect() as c:
        c.execute('INSERT OR REPLACE INTO market_snapshots(ticker,payload,updated_at) VALUES (?,?,CURRENT_TIMESTAMP)',(x['ticker'],json.dumps(x,separators=(',',':')))); c.commit()

def put_news(items):
    if not items:return
    with connect() as c:
        c.executemany('INSERT OR REPLACE INTO news_items(url,ticker,title,domain,published,source,updated_at) VALUES (?,?,?,?,?,?,CURRENT_TIMESTAMP)',[(x['url'],x['ticker'],x['title'],x['domain'],x['published'],x['source']) for x in items if x.get('url')]); c.commit()

def put_cot(items):
    if not items:return
    with connect() as c:
        c.executemany('INSERT OR REPLACE INTO commodity_snapshots(commodity,payload,updated_at) VALUES (?,?,CURRENT_TIMESTAMP)',[(x['commodity'],json.dumps(x,separators=(',',':'))) for x in items]); c.commit()

def read_market():
    init_market()
    with connect() as c:
        prices=[json.loads(r[0]) for r in c.execute('SELECT payload FROM market_snapshots ORDER BY ticker').fetchall()]
        news=[dict(zip(['url','ticker','title','domain','published','source'],r)) for r in c.execute('SELECT url,ticker,title,domain,published,source FROM news_items ORDER BY published DESC LIMIT 100').fetchall()]
        cot=[json.loads(r[0]) for r in c.execute('SELECT payload FROM commodity_snapshots ORDER BY commodity').fetchall()]
    return prices,news,cot
