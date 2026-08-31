# RESUME — jetson-pichick

**State (2026-08-31, post-commit):** v0.2.0 deployed to Jetson AND
committed (f674442 + c3d5703, on main, not pushed). Working tree clean
apart from this file and the task-plan updates (housekeeping commit).
Service verified live: PID 324723, empty-coop frames gating correctly
(chickens=0, conf 0.9, no phantoms).

**Session finding (load-bearing):** llava-phi3:3.8b hallucinates any
animal named in the prompt, even in negation. Never put species words
in PROMPT (src/jchick/ollama.py comment records the evidence). Poultry
reclassification lives in code (`_build_result`, conf clamp 0.5).

**Pending tasks (tasks/PLAN.md, detection-tuning phase):**
- T11 — consumer repo: CHICKENS_CONFIRM_STREAK 1→2 (door currently
  closes on ONE 3-count frame at conf≥0.8; single phantom could lock
  the flock out). Task file: tasks/T11_consumer_streak.md
- T12 — dusk verification window (~16:30-19:40 local): watch
  `nats sub -s nats://192.168.0.151:4222 'home.coop.pichick.>'`;
  success = zero phantom detection.<species>, correct 3-counts,
  single-pass latency ~12-13s on fired frames.
  Task file: tasks/T12_dusk_verification.md

**Known issues, deliberately not touched:**
- Service stop takes ~50s (open MJPEG /stream holds wait_closed);
  within systemd's 90s TimeoutStop, cosmetic.
- status.startup publish drops when NATS connect races startup
  (publisher lossy by design; heartbeat proves liveness).
- OOM killer killed llama-server once (7GB anon-rss on 7.5GB SoC);
  auto-recovered ~5s. Keep exactly one model loaded.

**Next:** run T11 (one-line consumer change, separate repo), then T12
at dusk; close the phase from T12 results.