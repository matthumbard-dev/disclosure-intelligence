# Disclosure Intelligence — Event Extraction v2

This release fixes the biggest interpretation gap in the prior deep-parser build.

## What changed

- 8-K cards no longer stop at generic item labels such as "Material agreement".
- The parser now attempts to classify the underlying event as equity financing, debt financing, acquisition/disposition, executive/board change, cybersecurity incident, bankruptcy/restructuring, listing/compliance issue, or other.
- It extracts headline-worthy dollar figures, share/unit counts, security types, dates, and counterparties where the filing text exposes them.
- Financing cards explain potential dilution instead of merely saying "material agreement".
- Generic/unresolved 8-Ks are downgraded to SKIP/LOW INFO rather than receiving a reel recommendation.
- Existing low-memory and ticker-required behavior is preserved.

Upload all files to the repository root, replacing matching files. Keep the existing Render `SEC_USER_AGENT` environment variable.
