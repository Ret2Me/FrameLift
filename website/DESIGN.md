---
version: alpha
name: "Telemetry Yield"
description: "An evidence-led Polish research preview for an offline satellite decoder."
colors:
  ink: "#172b3a"
  paper: "#ffffff"
  field: "#edf3f7"
  blue: "#164bcb"
  muted: "#506474"
  line: "#c7d4df"
typography:
  display:
    fontFamily: "Georgia, 'Times New Roman', serif"
  sans:
    fontFamily: "'Segoe UI', Arial, sans-serif"
  mono:
    fontFamily: "'Courier New', monospace"
rounded:
  DEFAULT: "0.375rem"
spacing:
  section-gap: "6rem"
  page-max: "76rem"
components:
  button: {}
  evidence-plot: {}
  mode-control: {}
---

# Telemetry Yield Design System

## Overview

### Creative North Star
The annotation sheet beside a receiver: patient, precise and explicit about what was measured. The signature is a full-width packet-set instrument, with common, added and missed frames accounted for in plain text. Not a fabricated waterfall.

### Product context and register
- Audience: Polish station operators and researchers evaluating archived audio recovery.
- Market: research/engineering, no commercial territory or distribution terms inferred.
- Locale: Polish UI, English paper and literal English CLI identifiers.
- Usage: desktop technical evaluation, accessible mobile reading and downloads.
- Register: marketing/content site, no application dashboard or hosted decoding service.
- Restraint: no animated orbit, speculative AI imagery, generic pricing cards or fake release link.
- Plan revision: replaced the standard giant-percentage hero with a material thesis and an accounting graphic. The performance claim stays beside its denominator and four misses.
- Runtime ownership: `styles.css :root` is canonical; frontmatter mirrors its six palette values and role fonts. No generated token system or independent theme adapter.

## Colors
White reading surface, cool field panels, dark blue-gray ink; blue means the native receiver or a primary action, not success. Muted ink remains readable on field. Borders structure the plot. Misses use a patterned dark outline and a text label rather than red-only coding. Light theme only; forced colors use system colors.

## Typography
Georgia is reserved for the thesis and major headings, connecting the website to the paper. Segoe UI/Arial provides Polish-readable body copy; Courier New annotates experiments, commands and metrics. No external font requests or font swap. Numeric labels use tabular figures, Polish spacing and decimal commas.

## Layout
76rem maximum, fluid gutters, generous 6rem section rhythm. Hero uses two unequal columns; at 760px all content becomes one column. The comparison graphic is horizontal but its textual equivalent remains complete at every size. Document owns scrolling. Code alone has local overflow.

## Elevation & Depth
No shadows, glass, sticky layers or decorative backgrounds. Tonal instrument panels distinguish measurements from explanatory prose.

## Shapes
Controls use 0.375rem corners; instrument track endpoints are square. Round radio controls retain native semantics. No decorative numbered steps except the actual processing sequence.

## Components

### Foundational visual states
All actions have pointer, hover, active and visible focus treatments. Native radio selection carries the mode state. Copy feedback occupies a reserved live region; failure offers manual selection. No async server states exist.

### Buttons and actions
One solid blue paper CTA; outlined guide and download actions. No unsupported download, billing, submission or contact affordances. Links to local assets require those assets to be copied during packaging.

### Navigation and data display
Simple wrapping anchor navigation, no mobile menu. Packet counts always include scope and frozen timestamp. The 189/266 snapshot is not live. Graphics have equivalent visible prose and semantic labels.

### Forms and overlays
Only a native radio group selecting example CLI text. It is disabled until JavaScript initializes; the no-JavaScript note provides equivalent manual command changes. No upload, modal, account, forms or personal-data collection. Clipboard is optional progressive enhancement. Mobile copy status reserves 2.5rem for its two-line fallback, so the console does not move when permission is denied.

### Iconography
An inline three-line signal mark is decorative; all actions have text labels. No icon library.

### Motion
Only short color feedback; reduced motion disables transitions and scrolling animation. No auto-play or chart animation.

### Content and data visualization
Offer a research tool, not proven universal superiority. Per-observation unique frames are not globally unique telemetry. The independent replay reference is not the complete native SatNOGS chain. The strongest claim must retain these limits locally, not merely in a footer.

## Do's and Don'ts
- Do preserve the added/missed accounting and the completed/planned denominator.
- Do keep modes described as budgets, not hard end-to-end deadlines.
- Don't attribute the progressive result to experimental codec weighting.
- Don't publish or invent licensing, prices, release URLs or IEEE endorsement.
