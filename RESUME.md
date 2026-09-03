# RESUME — jetson-pichick

Last updated: 2026-09-03 ~07:20 (T14 complete; repo pushed)

## Current state
- v0.2.2 LIVE on the Jetson (deployed 07:59 2026-09-02, recycle-only):
  llama-server watermark recycle (JCHICK_OLLAMA_RECYCLE_RSS_MB=6000)
  is the OOM fix — live-verified by the T14 overnight soak
  (2026-09-02 07:30 → 2026-09-03 07:16): 22 recycle events, all during
  inference; recycle→next-inference-completion 12.6-15.9s (no reload
  stall >120s); kernel OOM kills 0 (baseline 10-28/day); phantom
  species 0; alert.ollama 0; inference errors 0; service active.
  Door-open window (~07:15) fired chickens:3 conf 0.9 — top-level
  chickens serializes FIRST, payload v0.2.1-identical (wire contract
  verified from journal publish lines).
- Inference downscale (JCHICK_INFERENCE_MAX_DIM) SHIPS DISABLED (=0):
  on-box G4 found the 640-px re-encode pushes model confidence past the
  consumer's 0.80 gate on marginal lit frames (0.75→0.90, 0.57→0.90) —
  door-flip risk with CHICKENS_CONFIRM_STREAK=1. User decision
  2026-09-02. Chicken counts were unaffected (78/78 parity). Re-enable
  only via .env.example + redeploy.
- Harness evidence (tasks/T15): L1 leak confirmed on-box (both runs);
  prompt_eval_count=926 BOTH arms → fixed-336 letterbox measured, not
  inferred. Live kill #180 observed 07:14:56 at 6.92GB anon-rss, 62 min
  after fresh restart.
- Git: local main == origin/main at 59df91e (pushed 2026-09-03 on user
  request). dusk_watch.log stays untracked.

## Next actions
- None pending. All phases closed: MJPEG (T01-T05), diff-gate fix
  (T06-T10), detection tuning (T12-T14), inference downscale + OOM fix
  (T15). Watch-list items only (not tasks):
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
- tasks/PLAN.md — all phases closed, T14 done 2026-09-03
- tasks/T15_inference_downscale.md — full evidence trail + transcripts