# RESUME — jetson-pichick

Last updated: 2026-09-03 ~08:25 (T45 design written — cross-repo)

## Current state
- v0.2.3 LIVE on the Jetson (deployed 07:43 2026-09-03, commit cf53066):
  movement label in inference.gated/fired payloads is now DERIVED from
  the measured diff_score (jchick.diff.movement_label) instead of the
  VLM's frozen-frame guess. Bands: <0.030 still / <0.080 calm /
  <0.150 active / >=0.150 agitated (grounded in the 2026-08-31→09-03
  journal review; VLM label was uninformative — still/calm distributions
  near-identical, n=252). MJPEG HUD shows the same derived label.
- HAR (High Accuracy Review) passed pre-deploy: all 7 contracts (wire
  order, key order, totality, HUD consistency, gate untouched, no
  semantic drift, version) with both wire sides cited — consumer parses
  chickens order-independently, movement is log-only, no branching.
- Live verification on-box post-deploy: warmup sentinel diff_score 1.0
  -> movement "agitated"; manual-trigger frame 0.0126 -> "still";
  service active, 0 errors/alerts post-restart; subjects flowing.
- v0.2.2 OOM fix still live and holding: 22 recycle events in the T14
  overnight soak, zero OOM kills, no reload stalls (T14 closed PASS).
- T17 (block-wise motion metric) captured in tasks/T17 — NOT scheduled;
  needs user decision + its own live tuning window (changes what
  diff_score means; JCHICK_DIFF_THRESHOLD=0.012 is tuned on the current
  mean-abs scale).
- Cross-repo: consumer light-scheduling design (T45) lives in
  ../coop_door_controller/.opencode/plans/T45_powersave_close_light_off.md
  — powersave light off 20 min after door close; DESIGN ONLY, not
  scheduled; 2 decision points await the user (close-only 20 vs
  all-windows 20; override holds to window-end vs morning). Consumer
  PLAN.md status row updated to point at it.
- Git: dusk_watch.log stays untracked.

## Next actions
- Cross-repo T45 (powersave light close-off) awaits your 2 decisions:
  close-only-20 vs all-windows-20, and override-to-window-end vs
  override-to-morning. Design: ../coop_door_controller/.opencode/plans/
  T45_powersave_close_light_off.md. Execution needs an OTA/USB flash
  plan for the ESP32.
- Repo-local phases all closed: MJPEG (T01-T05), diff-gate fix
  (T06-T10), detection tuning (T12-T14), inference downscale + OOM fix
  (T15), movement label from diff (T16). Optional T17 captured but NOT
  scheduled. Watch-list items only (not tasks):
  - ollama upstream: #18106, #18099 (our T14 soak data = useful data
    point for the per-request anonymous-leak issue).
  - Remaining levers if recycle proves insufficient over longer soaks:
    OLLAMA_FLASH_ATTENTION, KV quant (#8597), systemd timer/MemoryMax —
    separate decisions, not pending work.
  - Box quirks: ollama -v and /api/version both report 0.0.0 (stripped
    build — version caveat unverifiable); jetson-pichick stop can need
    SIGKILL (stop-sigterm 90s timeout with inference in flight —
    pre-existing, expect it on future restarts).

## Task index
- tasks/PLAN.md — all phases closed; T14 + T16 done 2026-09-03
- tasks/T16_movement_from_diff.md — band evidence + HAR record
- tasks/T15_inference_downscale.md — full evidence trail + transcripts