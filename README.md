# Disclosure Intelligence - Low Memory Render Build

This build is specifically refactored for Render Free (512 MB RAM).

## Changes from the previous build

- Removed pandas/numpy entirely.
- One Uvicorn worker only.
- Dashboard requests are read-only and never trigger a scan.
- One collector job at a time; SEC and market enrichment never overlap.
- SEC work is bounded to 32 current filings per cycle by default.
- Events are written to SQLite one at a time instead of holding a market-wide result set in memory.
- Individual filing downloads are capped at 2 MB.
- 8-K HTML is not deeply parsed in this low-memory build; it is retained as a filing-level event.
- Price/news enrichment is limited to 10 recent high-signal tickers per cycle.
- News is limited to 3 results per ticker.
- Demo tickers are automatically purged at startup.
- `/health` and `/api/status` expose process peak RSS so memory can be observed on Render.

## Render settings

Keep your existing `SEC_USER_AGENT` environment variable.

Start command:

`uvicorn web_app:app --host 0.0.0.0 --port $PORT --workers 1`

No paid Render upgrade should be necessary for this test.

## Important development limitation

SQLite on Render Free is not durable across redeploys/replacements. This version is intended to prove stable low-memory ingestion. After stability is confirmed, move persistence to PostgreSQL/Supabase before accumulating historical data.


## Ticker-required feed
Investor-facing events are now displayed only when a valid exchange ticker can be resolved. Form 144 and Schedule 13D/G parsers prefer issuer trading symbols embedded in the filing and fall back to the SEC CIK-to-ticker reference map. Legacy unresolved rows are purged at startup.
