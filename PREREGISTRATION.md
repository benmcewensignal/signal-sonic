# Pre-registered analysis: does sound lead bookings?

STATUS: DRAFT — to be frozen (this file edited to STATUS: FROZEN with a
dated commit) BEFORE the full backfill completes. Analyses run after
freezing follow this spec; deviations must be reported as deviations.

## Hypothesis
Scene-level sonic change (drift in the chart/release fingerprint) precedes
booking-momentum change for the same scene by a usable margin.

## Data
- Sonic: monthly backfill series (flat, per scene) 2024-09..2026-07, and
  live weekly chart-weighted series thereafter.
- Bookings: earlysignal scene booking-momentum history (to be snapshotted;
  the comparison requires it exists).

## Primary measure
Cross-correlation of per-scene sonic-change rate vs booking-momentum
change rate at lags of -6..+6 months. "Sound leads" = peak |correlation|
at a positive lag (sonic first) of >= 2 months, in >= half the scenes
with usable data.

## Confound controls (from the 6-month pilot analysis)
- Chunk/batch effects: any month-boundary aligned with a fetch-chunk or
  decoder-fingerprint change is excluded from change-rate estimates.
- Mastering: loudness is regressed out of drift magnitudes before the
  primary measure.
- Multiple testing: BH correction across scenes at q=0.10.

## What counts as confirmation / disconfirmation
- CONFIRM: primary measure passes as defined above.
- DISCONFIRM: peak correlation at lag <= 0 in most scenes, or no
  significant peak anywhere.
- Either result is reported.
