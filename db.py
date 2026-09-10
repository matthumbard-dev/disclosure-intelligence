from __future__ import annotations
import sqlite3
from pathlib import Path
import pandas as pd

DB_PATH = Path(__file__).with_name("disclosures.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    accession TEXT,
    form TEXT,
    filed_at TEXT,
    transaction_date TEXT,
    company TEXT,
    ticker TEXT,
    cik TEXT,
    event_type TEXT,
    actor TEXT,
    role TEXT,
    transaction_code TEXT,
    shares REAL,
    price REAL,
    value REAL,
    ownership_after REAL,
    source_url TEXT,
    primary_document TEXT,
    score INTEGER,
    score_band TEXT,
    reasons TEXT,
    summary TEXT,
    ingested_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_events_filed_at ON events(filed_at);
CREATE INDEX IF NOT EXISTS idx_events_ticker ON events(ticker);
CREATE INDEX IF NOT EXISTS idx_events_form ON events(form);
"""


def connect():
    con = sqlite3.connect(DB_PATH)
    con.executescript(SCHEMA)
    return con


def upsert_events(events: list[dict]) -> int:
    if not events:
        return 0
    cols = [
        "event_id","accession","form","filed_at","transaction_date","company","ticker","cik",
        "event_type","actor","role","transaction_code","shares","price","value","ownership_after",
        "source_url","primary_document","score","score_band","reasons","summary"
    ]
    sql = f"INSERT OR REPLACE INTO events ({','.join(cols)}) VALUES ({','.join(['?']*len(cols))})"
    rows = [[e.get(c) for c in cols] for e in events]
    with connect() as con:
        con.executemany(sql, rows)
        con.commit()
    return len(rows)


def read_events(days: int = 30) -> pd.DataFrame:
    q = """
    SELECT * FROM events
    WHERE date(filed_at) >= date('now', ?)
    ORDER BY filed_at DESC, score DESC
    """
    with connect() as con:
        return pd.read_sql_query(q, con, params=(f"-{int(days)} day",))


def clear_db():
    with connect() as con:
        con.execute("DELETE FROM events")
        con.commit()
