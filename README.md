# Disclosure Intelligence - Recovery v4

This build fixes a zero-data regression introduced during the editorial UI iteration.

## Core change
SEC discovery now uses the official daily EDGAR master index as the primary source. The app filters the index to the target forms (4, 144, SC 13D/G, 8-K), processes a bounded batch sequentially, and only then enriches ticker-resolved records. The older current-filings Atom feed is retained as a fallback.

## Deployment
Upload the contents of this folder to the root of the existing GitHub repository, replacing matching files. Keep the existing Render `SEC_USER_AGENT` environment variable. After Render shows the new commit as Live, hard refresh the site and click **Refresh market now** once.

The low-memory single-worker architecture is preserved.
