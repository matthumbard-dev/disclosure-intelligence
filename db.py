from __future__ import annotations
import os, sqlite3
from pathlib import Path

DB_PATH = Path(os.getenv('DB_PATH') or Path(__file__).with_name('disclosures.db'))
SCHEMA = '''
CREATE TABLE IF NOT EXISTS events(
 event_id TEXT PRIMARY KEY, accession TEXT, form TEXT, filed_at TEXT, transaction_date TEXT,
 company TEXT, ticker TEXT, cik TEXT, event_type TEXT, actor TEXT, role TEXT,
 transaction_code TEXT, shares REAL, price REAL, value REAL, ownership_after REAL,
 source_url TEXT, primary_document TEXT, score INTEGER, score_band TEXT,
 reasons TEXT, summary TEXT, detail TEXT, ingested_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_events_filed_at ON events(filed_at);
CREATE INDEX IF NOT EXISTS idx_events_ticker ON events(ticker);
CREATE INDEX IF NOT EXISTS idx_events_form ON events(form);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
'''
COLS=['event_id','accession','form','filed_at','transaction_date','company','ticker','cik','event_type','actor','role','transaction_code','shares','price','value','ownership_after','source_url','primary_document','score','score_band','reasons','summary','detail']

def connect():
    c=sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory=sqlite3.Row
    c.executescript(SCHEMA)
    return c

def upsert_event(e):
    q=f"INSERT OR REPLACE INTO events ({','.join(COLS)}) VALUES ({','.join(['?']*len(COLS))})"
    with connect() as c:
        c.execute(q,[e.get(x) for x in COLS]); c.commit()

def upsert_events(events):
    if not events:return 0
    q=f"INSERT OR REPLACE INTO events ({','.join(COLS)}) VALUES ({','.join(['?']*len(COLS))})"
    with connect() as c:
        c.executemany(q,[[e.get(x) for x in COLS] for e in events]); c.commit()
    return len(events)

def read_events(days=30, limit=1200):
    with connect() as c:
        rows=c.execute("SELECT * FROM events WHERE ticker IS NOT NULL AND TRIM(ticker)<>'' AND date(filed_at)>=date('now',?) ORDER BY filed_at DESC,score DESC LIMIT ?",(f'-{int(days)} day',int(limit))).fetchall()
    return [dict(r) for r in rows]

def recent_tickers(days=14, limit=12):
    with connect() as c:
        rows=c.execute("SELECT ticker, MAX(company) company, MAX(score) max_score FROM events WHERE ticker<>'' AND ticker IS NOT NULL AND date(filed_at)>=date('now',?) GROUP BY ticker ORDER BY max_score DESC LIMIT ?",(f'-{int(days)} day',int(limit))).fetchall()
    return [(r['ticker'],r['company'] or r['ticker']) for r in rows]

def set_meta(k,v):
    with connect() as c:
        c.execute('INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)',(k,str(v))); c.commit()

def get_meta(k,default=''):
    with connect() as c:
        r=c.execute('SELECT value FROM meta WHERE key=?',(k,)).fetchone()
    return r[0] if r else default


def purge_unresolved():
    """Remove legacy rows that cannot be tied to a listed ticker."""
    with connect() as c:
        c.execute("DELETE FROM events WHERE ticker IS NULL OR TRIM(ticker)=''")
        c.commit()

def purge_demo():
    fake=('ACME','CLDF','NSTR','VBIO','HBR')
    with connect() as c:
        c.execute(f"DELETE FROM events WHERE ticker IN ({','.join(['?']*len(fake))})",fake); c.commit()
