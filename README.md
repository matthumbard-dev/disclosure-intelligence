# Disclosure Intelligence — Live SEC Dashboard

This version is designed for Render and scans current SEC EDGAR filings market-wide.

## What it collects
- Form 4 insider transactions, including transaction code, shares, price, value and role when available
- Form 144 proposed affiliate sales
- Schedule 13D / 13G beneficial-ownership filings
- Form 8-K material-event filings

## Render settings
Build command:
`pip install -r requirements.txt`

Start command:
`uvicorn web_app:app --host 0.0.0.0 --port $PORT`

Environment variable:
`SEC_USER_AGENT=Your Name your-email@example.com`

## Updating an existing GitHub/Render deployment
Upload/replace these files in the repository root and commit them. Render auto-deploys from the main branch.

The app automatically begins a SEC scan when it starts, checks for stale data when the dashboard is opened, and refreshes approximately every 30 minutes while the Render instance is awake.

## Important limitation
Render's free web-service filesystem is ephemeral. This version repopulates current SEC data automatically after a restart, so it works as a live dashboard, but long-term historical storage should be moved to PostgreSQL in the next infrastructure step.
