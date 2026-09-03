# T17 — Block-wise motion metric in FrameDiffGate (optional, not scheduled)

## Goal
Distinguish LOCAL motion (a chicken moving: few blocks change a lot) from
GLOBAL scene change (dusk light shift, camera bump: all blocks change a
little) by adding a block-wise diff to the gate, so dusk light shifts stop
inflating diff_score and the T16 movement bands get a cleaner signal.

## Context
Research 2026-09-03 (tasks/T16): block-wise frame difference is the
standard illumination-robust upgrade for fixed-camera motion detection
(SAGE 2018, journals.sagepub.com/doi/full/10.1177/1729881418783633).
Observed motivation on-box: a dusk frame scored 0.1734 ("calm" per VLM)
that was almost certainly a light shift, not bird motion — a block-wise
fraction would have shown it as global.

## Status
NOT SCHEDULED — user decision pending. This changes the gate's motion
metric; per repo discipline (T15 shipped recycle-only after G4), any
change to what diff_score MEANS needs its own live A/B before shipping:
the live JCHICK_DIFF_THRESHOLD=0.012 is tuned on the current mean-abs
metric and would need re-tuning on any new scale. Do not execute without
an explicit "execute T17".

## Sketch (not final)
- Keep current mean-abs score as the GATE decision (wire stability).
- Add to FrameDiffGate.evaluate's return or payload: fraction of 8x8
  blocks whose mean-abs diff exceeds a per-block threshold (locality
  signal), e.g. movement:"global"|"local" annotation or a new
  block_change_fraction field in the payload.
- Requires: consumer-safety check for any new payload field (consumer
  extracts only known keys — json_str_nested style parse, safe), and a
  live tuning window to pick the per-block threshold.

## Validation plan (to be written when scheduled)
    # placeholder — must be filled before execution per repo rules

## Rollback
    git checkout src/jchick/diff.py src/jchick/app.py