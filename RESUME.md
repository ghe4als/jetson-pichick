# RESUME — jetson-pichick

**State (2026-09-01 ~01:00 UTC):** v0.2.1 deployed to Jetson and verified
live (user-approved; PID 612878, startup version=0.2.1, allowlist
seeded, zero non-chicken species published post-restart, broker
delivery + wire contract confirmed). v0.2.1 code NOT yet committed —
working tree holds the 7-file change set (config/ollama/app/env/version
+ PLAN.md + T12 file) plus untracked dusk_watch.log. v0.2.0 is committed
and pushed (origin/main = d7a2808).

**T12 verdict (2026-08-31 dusk, full detail in tasks/T12):** door logic
PASS (closed correctly on real birds), single-pass PASS, zero-phantom
FAIL pre-fix. Root cause (corrected during T13): the flock IS visible at
night; llava-phi3 flickers its classification chicken↔duck↔dog/cat on
identical black birds. The 23:54 "human" was real (user working).
Phantom "dog lying in wooden box" = likely a roosting hen misread.

**Load-bearing findings:**
- Never put species words in PROMPT — model invents them (menu effect,
  even in negation). Suppression is code-side only.
- Species allowlist (JCHICK_ALLOWED_SPECIES, default
  human,mouse,mice,rat,rats) is the shipped fix: implausible species
  dropped with WARNING; flock-splits fold + clamp 0.5; birdless phantom
  poultry drops entirely. First live catch within 20 min of restart
  (model tried "dog" again → dropped).
- Night counts flicker 2↔3 (identical black birds) — model limit,
  absorbed by consumer's door-closed count latch; harmless post-close.

**Pending tasks (tasks/PLAN.md, detection-tuning phase):**
- T14 — morning soak check: overnight phantom-species count (expect 0),
  OOM-kill count, consumer door-open count-reset (~07:15 local opens).
  Commands: journalctl by current PID + `nats sub` spot check.

**Known issues, deliberately not touched:**
- OOM killer killed llama-server 3×/3 days (~7GB anon-rss, 7.5GB SoC);
  stock ollama.service auto-recovers in ~65s. Recurring nightly — if it
  keeps up, look at model memory profile or a smaller gate model.
- Transient 00:58 UTC ollama blip post-restart (4 alert.ollama, 5s,
  self-recovered) — same OOM recovery pattern.
- Service stop ~50s (open MJPEG /stream holds wait_closed); within
  systemd's 90s TimeoutStop, cosmetic.
- status.startup publish drops in the NATS connect race (lossy by
  design; heartbeat at 300s proves liveness).
- Dev-box .venv/bin/python hangs (~30s+); use /usr/bin/python3 locally
  and /opt/jchick/.venv/bin/python on the Jetson.

**Next:** T14 in the morning; then commit v0.2.1 (offer pending user
approval — commit style per git-master: split src vs docs commits).