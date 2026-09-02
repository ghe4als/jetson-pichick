# RESUME — jetson-pichick

Last updated: 2026-09-02 ~09:05 (T15 complete)

## Current state
- v0.2.2 LIVE on the Jetson (deployed 07:59 2026-09-02, recycle-only):
  llama-server watermark recycle (JCHICK_OLLAMA_RECYCLE_RSS_MB=6000)
  is the OOM fix — live-verified in the first hour: 2 recycle WARNINGs
  (08:12:05 @ 6186MB, 08:57:30 @ 6123MB), ZERO OOM kills (night
  baseline ~11/hr), inference flowed across both recycles (13 s to next
  inference; no reload stall), service active.
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
- Local main is AHEAD of origin by 10 commits (2a40104 … fb83341);
  push ONLY on explicit user request. dusk_watch.log stays untracked.

## Next actions
- T14 morning soak (absorbs T15 overnight checks): overnight
  llama-server OOM kill count target 0 (baseline 10-28/day); recycle
  event count + whether any reload stalled past 120 s; overnight
  alert.ollama count; phantom-species count 0; ~07:15 door-open reset;
  dusk inference.fired frames show chickens key + v0.2.1-identical
  payload (live nats sub was not observable this morning — coop empty).
- Watch upstream: ollama#18106, #18099 (our dataset = useful data
  point). Remaining levers if recycle proves insufficient:
  OLLAMA_FLASH_ATTENTION, KV quant (#8597), systemd timer/MemoryMax —
  separate decisions.
- Box quirks: ollama -v and /api/version both report 0.0.0 (stripped
  build — version caveat unverifiable); jetson-pichick stop can need
  SIGKILL (stop-sigterm 90s timeout with inference in flight —
  pre-existing, expect it on future restarts).

## Task index
- tasks/PLAN.md — T15 DONE (all rows current)
- tasks/T15_inference_downscale.md — full evidence trail + transcripts