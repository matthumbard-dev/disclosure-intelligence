from __future__ import annotations
import os, sqlite3
from pathlib import Path
import pandas as pd
DB_PATH=Path(os.getenv('DB_PATH') or Path(__file__).with_name('disclosures.db'))
SCHEMA='''
CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY,accession TEXT,form TEXT,filed_at TEXT,transaction_date TEXT,company TEXT,ticker TEXT,cik TEXT,event_type TEXT,actor TEXT,role TEXT,transaction_code TEXT,shares REAL,price REAL,value REAL,ownership_after REAL,source_url TEXT,primary_document TEXT,score INTEGER,score_band TEXT,reasons TEXT,summary TEXT,detail TEXT,ingested_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_events_filed_at ON events(filed_at);CREATE INDEX IF NOT EXISTS idx_events_ticker ON events(ticker);CREATE INDEX IF NOT EXISTS idx_events_form ON events(form);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
'''
def connect():
    c=sqlite3.connect(DB_PATH);c.executescript(SCHEMA);return c
def upsert_events(events):
    if not events:return 0
    cols=['event_id','accession','form','filed_at','transaction_date','company','ticker','cik','event_type','actor','role','transaction_code','shares','price','value','ownership_after','source_url','primary_document','score','score_band','reasons','summary','detail']
    q=f"INSERT OR REPLACE INTO events ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})"
    with connect() as c:c.executemany(q,[[e.get(x) for x in cols] for e in events]);c.commit()
    return len(events)
def read_events(days=30):
    with connect() as c:return pd.read_sql_query("SELECT * FROM events WHERE date(filed_at)>=date('now',?) ORDER BY filed_at DESC,score DESC",c,params=(f'-{int(days)} day',))
def set_meta(k,v):
    with connect() as c:c.execute('INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)',(k,v));c.commit()
def get_meta(k,default=''):
    with connect() as c:
        r=c.execute('SELECT value FROM meta WHERE key=?',(k,)).fetchone();return r[0] if r else default
