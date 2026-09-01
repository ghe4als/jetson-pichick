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

Stakes note: the consumer stays at CHICKENS_CONFIRM_STREAK=1 (the
planned streak bump was dropped by user decision 2026-08-31), so a
single conf>=0.8 phantom 3-count during the close window latches
all_home and closes the door. The zero-phantom criterion below is
safety-critical, not just a quality metric.

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

## Final verdict (2026-09-01 ~01:00 UTC, ground truth from user)

- Happy path PASS: chickens counted on return (3-counts 17:18-17:28
  local), door closed on real birds, latched correctly.
- Single-pass PASS: detail latency == gate latency throughout.
- Zero-phantom FAIL (pre-fix): after dark, 11+ phantom detection events
  (dog x6, cat x5, fish, pigeon, pig post-close), several at conf 0.9,
  some self-contradictory ("No animals visible." + dog). User confirmed
  NO dog exists; coop light is ON. Root cause (corrected — see post-
  deploy section): the roosting flock IS visible at night; the model
  intermittently MISCLASSIFIES the birds as other species. The earlier
  "birds roost out of camera view" claim was my wrong inference from
  chickens:0 frames and is retracted.
- The 23:54 human detection was REAL (user was working on the coop) —
  the model can correctly report a real intruder; suppression must be
  surgical, not blanket.
- User requirements: chicken counting is the job; other animals only if
  real; only plausible intruders in a small coop are human and
  mice/rats (drawn to feed).

## Follow-up fix built same night (v0.2.1, pending deploy approval)

Species allowlist filter, code-side in _build_result (no prompt change,
so no menu effect): JCHICK_ALLOWED_SPECIES (default
human,mouse,mice,rat,rats; '*' disables). Implausible species dropped
with WARNING log; poultry fold unchanged for flock-splits; birdless
phantom poultry (e.g. lone "duck" on empty coop) dropped entirely.
Verified: 11/11 unit scenarios; E2E on live night frames on the Jetson
(model hallucinated duck+goose -> filter dropped both; flock-split
duck frame -> folded to 3 at clamped 0.5, matching real bird count).
Wire effect after deploy: only detection.human / detection.mouse /
detection.rat-class events can ever publish.

Incident during testing: OOM killer killed llama-server again (~00:50
UTC, same ~7GB anon-rss pattern); stock ollama.service Restart=always
recovered it in ~65s. Third occurrence in 3 days - still within
known-issue tolerance, but recurring nightly.

## Post-deploy verification (v0.2.1 live, 2026-09-01 00:55-01:00 UTC)

- Deploy user-approved 00:55 UTC; service active PID 612878;
  status.startup version=0.2.1; /etc/jchick/jchick.env seeded with
  JCHICK_ALLOWED_SPECIES=human,mouse,mice,rat,rats.
- Root-cause evidence: within ~20 minutes, the SAME closed-coop night
  scene produced raw "Two black ducks / A duck and a goose" outputs
  (00:50 E2E), dog/cat/pig reports (00:19-00:38), and clean
  chickens:2-3 counts at conf 0.85-0.9 (00:56-00:57 post-restart
  frames). The birds are visible; their classification flickers
  chicken <-> duck <-> dog/cat. The phantom stream was largely the real
  flock being misread, not animals invented from an empty view.
- Filter behavior post-deploy: zero non-chicken detection.* published
  by the new process; zero species-drop warnings so far (model
  reporting chickens directly since restart — drop path itself is
  proven by E2E runs + 11/11 unit scenarios).
- Counts at night still flicker 2<->3 (identical black birds) — known
  model limit; consumer CLOSED-latch + hard close absorb it. Post-close
  3-count frames at conf>=0.85 are published but harmlessly ignored by
  the consumer's door-closed count latch.
- Zero tracebacks, zero alert.ollama; single-pass latencies confirmed
  (gate == detail, ~13-17s).

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