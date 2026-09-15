# ADR 004: user authorization for continued internal research

Date: 2026-08-31 UTC  
Status: accepted for internal research

## Decision

The user explicitly instructed the project to continue research without
treating unresolved license paperwork as a blocker. The user represented that
permissions from almost all relevant parties are already held and that any
missing permissions will be requested later.

For this project, that statement authorizes continued internal computation,
bounded retrieval, metadata ingest, and technical experimentation. G3 is no
longer a blocker for those activities.

## Limits

- This ADR records a user assertion; it is not independent evidence of any
  third-party permission.
- Existing source-license observations remain in provenance records.
- Publication, redistribution, or external contact is not performed merely
  because internal research is authorized.
- Source-load limits, data integrity checks, and technical correctness gates
  remain fully active.

## Consequence

Bounded A2 engineering may proceed immediately. Bulk decoding remains blocked
by A0/A0.5 correctness failures, not by licensing.
