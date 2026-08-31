# T12 — Dusk verification of v0.2.0 detection tuning

## Goal
Observe one full dusk window (≈16:30–19:40 local) with producer v0.2.0
live and record: zero phantom `detection.<species>` events, correct
3-counts when the birds return, single-pass latency on fired frames.

## Context
Producer fixes shipped 2026-08-31 (v0.2.0, commits f674442 + c3d5703,
deployed and empty-coop verified same day). The historical failure
window is dusk: over 3 prior days, 13 phantom-predator frames passed
the consumer conf≥0.80 filter, clustered 19:23–19:40 local (02:23–02:39
UTC cluster in journal data), e.g. "A cat is sitting on a wooden shelf
next to two chickens" ×5. Empty-coop behavior was verified at deploy
time (gated frames, chickens=0, conf 0.9); the dusk regime (low light,
reflections, shadows) is the untested one. Chickens return to the coop
each evening; the flock is 3.

## Files touched
- None (observation only). Evidence lands in this file's "Results"
  section appended below after the window.

## Pre-conditions
- [ ] Producer v0.2.0 live (verified: status.startup version=0.2.0,
      PID 324723, 2026-08-31 ~11:31 local)
- [ ] T11 optional but preferred (streak=2 makes a bad night safe)

## Procedure (exact commands)
On any LAN host with nats CLI, for the whole window:

    nats sub -s nats://192.168.0.151:4222 'home.coop.pichick.>' | tee dusk_watch.log

Then analyze:

    grep detection. dusk_watch.log        # phantom species check
    grep inference.fired dusk_watch.log   # counts + latencies when birds return

## Success criteria (from the 3-day baseline)
- [ ] `detection.<species>` events with species ∉ {chicken}: ZERO
      (baseline: ~4/day passing the consumer filter)
- [ ] `inference.fired` shows chickens=3 on frames where 3 birds are
      visible (spot-check against http://192.168.0.18:8090/ HUD or
      /snapshot.jpg at the same minute)
- [ ] fired frames show gate latency ≈12-13s and NO detail call
      (single-pass: gate.latency_ms present, detail identical to gate —
      v0.2.0 reuses the gate result; detail block present but same
      latency/model, total wall ~12-13s not ~18s)
- [ ] Any folded-poultry frames appear as notes containing
      "[poultry folded into chickens" with confidence ≤ 0.5

## If it fails
- Phantom species at conf ≥0.8 → the prompt cannot name species to
  suppress them (menu effect — see f674442 commit message); next lever
  is the consumer's INFERENCE_MIN_CONFIDENCE or a different gate model
  (llava-llama3:8b is the candidate; watch the 7.5 GB RAM ceiling).
- Undercounts (2 when 3 visible) in GOOD light → camera framing issue;
  check /snapshot.jpg for an occluded bird before touching code.

## Rollback
None needed (observation only).

## Next task
Close out detection-tuning phase; update PLAN.md status from results.