from __future__ import annotations
import argparse
import os
from sec_client import SecClient, ingest_ticker
from db import upsert_events


def main():
    p = argparse.ArgumentParser(description='Sync SEC disclosure events for a ticker watchlist.')
    p.add_argument('--watchlist', default='watchlist.txt')
    p.add_argument('--max-filings', type=int, default=120)
    p.add_argument('--user-agent', default=os.getenv('SEC_USER_AGENT', ''))
    args = p.parse_args()
    if not args.user_agent:
        raise SystemExit('Set SEC_USER_AGENT or pass --user-agent "AppName you@example.com"')
    with open(args.watchlist, encoding='utf-8') as f:
        tickers = sorted({line.strip().upper() for line in f if line.strip() and not line.startswith('#')})
    client = SecClient(args.user_agent)
    mapping = client.ticker_map()
    events=[]
    for ticker in tickers:
        info = mapping.get(ticker)
        if not info:
            print(f'SKIP {ticker}: not found')
            continue
        ev = ingest_ticker(client, ticker, info['cik'], info['title'], args.max_filings)
        events.extend(ev)
        print(f'{ticker}: {len(ev)} normalized events')
    print(f'Upserted {upsert_events(events)} events')

if __name__ == '__main__':
    main()
