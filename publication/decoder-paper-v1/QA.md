# Verification — 2026-09-12

- Main PDF: 5 pages; 190-word abstract; official IEEE Access 2026-05-13 class; all fonts embedded; pages 1–5 visually inspected.
- `make` and `make verify`: pass. Two known empty title-constructor box warnings remain explicitly recorded, not suppressed; no body overflow or unresolved bibliography references.
- Source download: independently built with its own Makefile after extracting the supplied official template; layout/citation verification passes; ZIP integrity passes.
- `node verify-evidence.cjs --local`: pass on 189 distinct cohort members and 945 decoder-arm source/input identity bindings; exact packet sets, counts, additions/misses, timings, subgroup and cutoff checks pass. Does not replace raw-artifact FCS auditing.
- Separate agent reviewed technical methods and numerical statements. Corrected the MLSE gain expression and the wording of progressive budgets.
- Website: strict static audit has no findings; DESIGN lint has zero errors and seven advisory token-naming/reference warnings.
- Browser: Chromium 117 / Playwright 1.38.1, widths 1440/768/390/320; no horizontal page overflow or JavaScript errors; radio click/keyboard behavior, skip link, clipboard success/denial and no-JavaScript fallback pass. An initially visible hidden copy button was fixed and the full suite rerun.
- Website PDF hash matches the final manuscript PDF. Local asset and anchor checks pass; loopback HTTP PDF returns 200 with correct content type; out-of-scope file request returns 404.
- Not a full accessibility certification: PDF is not tagged; browser coverage is one engine; no external hosting or publication/submission performed.
- Frozen decoder campaign remains active and was not restarted, rebuilt or modified for this manuscript/website task.

Website browser evidence: `../../website/qa/results.json` and screenshots. Site can be served privately using `node ../../website/preview.cjs`; it binds loopback only and prints its assigned URL.
