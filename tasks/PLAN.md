# Project Plan — MJPEG stream with detection overlay

## Goal
Port the half-built MJPEG server from the stale `jchick/` tree into the
packaged `src/jchick/` tree, add detection overlay (text-based — no
bounding boxes available from current Ollama schema), add a shared
last-result store, configure the port, deploy to the running Jetson,
and verify the stream works in a browser.

## Context
The MJPEG server was implemented in `jchick/mjpeg_server.py` (250 lines,
looks complete) plus `http_port` in `jchick/config.py` and wiring in
`jchick/app.py`. But `pyproject.toml:24` packages `src/jchick/`, not
`jchick/`. The Jetson `pip install -e /opt/jchick` only sees `src/jchick/`.
Result: the MJPEG code never ran on the device.

Confirmed on the Jetson:
```
/opt/jchick/.venv/bin/python -c "import jchick, inspect; print(inspect.getsourcefile(jchick))"
→ /opt/jchick/src/jchick/__init__.py
```
So `python -m jchick` loads `src/jchick/`, which has no mjpeg_server.py,
no http_port in config, no wiring in app.py.

Overlay design constraint: `VisionResult` (src/jchick/ollama.py:33-42)
has chickens (int), other_animals (list[str]), movement (str),
confidence (float), notes (str), model, latency_ms — NO bounding boxes.
The Ollama prompt only asks for count/species/movement. Overlay is
therefore text HUD: chicken count, confidence %, movement, gate/detail
labels, diff score, fps, timestamp, frame counter. No boxes.

Additional bug in the stale tree: `jchick/app.py:75` references
`cfg.http_port` but the local is `self._cfg` — would NameError at
startup. Must fix when porting.

## Status (MJPEG phase)
Current task: T05 (DONE)
Last completed: T05 (commit: f507efb)

## Task list (MJPEG phase)
- [x] T01 — Port mjpeg_server.py into src/jchick/ with overlay + last-result store   ✅ DONE
- [x] T02 — Add http_port to src/jchick/config.py + JCHICK_HTTP_PORT to .env.example   ✅ DONE
- [x] T03 — Wire MJPEG server into src/jchick/app.py (fix cfg→self._cfg bug)           ✅ DONE
- [x] T04 — Delete stale jchick/ top-level tree                                         ✅ DONE
- [x] T05 — Deploy to Jetson and verify stream in browser                               ✅ DONE

## Notes (MJPEG phase)
- T01-T03 touch at most 5 files and can be one task per the 5-file rule,
  but splitting keeps each commit reviewable and lets the build catch
  errors between steps. We'll do T01+T02+T03 as separate commits but
  they share one design session.
- Overlay uses PIL.ImageDraw on the JPEG before re-encoding for stream.
  No new deps — pillow is already in pyproject.toml.
- Shared state: a single asyncio-guarded `LastResult` dataclass in
  mjpeg_server.py, written by inference_loop, read by stream loop.
  No lock needed — single asyncio event loop, no threads.

---

# Diff-gate model fix — 2026-08-28

## Goal
Resolve the gate VLM reporting `chickens: 0` on every frame even with
chickens present. Per the user's read, `llava-phi3:3.8b` is a capable
model and the fault is the empty-coop prompt bias from commit `12c7722`.
Plan: prove it with a controlled before/after A/B on the SAME model,
keeping the ability to swap models for future testing.

## Context
Investigation: `tasks/diff-gate-investigation-2026-08-28.md` (Case C:
gate model cannot see chickens). The pixel diff gate is healthy. The
investigation had the model's verdicts (journalctl) but not the frame
pixels, so it couldn't distinguish *model/prompt can't recognize
chickens* from *camera isn't framing/lighting chickens*. The plan
captures the disputed frames once, then builds an offline harness to
A/B the prompt (and any model) against that fixed test set — so prompt
iteration does not need repeated daylight windows or deploys. Same
model (`llava-phi3:3.8b`) throughout; `--model` on the harness is the
"change between them for testing" mechanism.

## Status (diff-gate fix) — CLOSED 2026-08-31
Phase superseded. Premise (gate reports chickens:0 with birds present)
was already resolved by the earlier model swap to llava-phi3:3.8b (3-day
fired history shows counts 0-3, not stuck-at-0). The offline-harness
route (T06-T09) was replaced by live empty-coop A/B testing during the
2026-08-31 session. T10's goal shipped as v0.2.0 via commits f674442 +
c3d5703 (evidence: 3-day inference.fired review, summarized in the
detection-tuning phase Context below).

## Task list (diff-gate fix) — CLOSED
- [x] T06 — Gate-frame audit dump        ✘ DROPPED (superseded; live A/B used instead)
- [x] T07 — Offline gate eval harness    ✘ DROPPED (superseded; live A/B used instead)
- [x] T08 — Deploy + capture baseline    ✘ DROPPED (superseded; live A/B used instead)
- [x] T09 — "After" test offline         ✘ DROPPED (superseded; live A/B used instead)
- [x] T10 — Apply winning prompt + confirm live   ✅ DONE via v0.2.0 (f674442 + c3d5703,
      deployed 2026-08-31, empty-coop verified live; mechanism differed from plan —
      see tasks/T12_dusk_verification.md for the remaining dusk-window check)

## Blocked
None.

---

# Detection tuning — 2026-08-31

## Goal
Verify the v0.2.0 producer detection fixes against the dusk failure
window: phantom-predator false alerts at dusk and count flicker with
the birds present.

## Context
3-day `inference.fired` review (575 frames): 2↔3 count flicker (209
flips), 13 phantom-predator frames passing the consumer conf≥0.80
filter, rooster-split undercounts (9 frames), gate+detail running the
same model twice (+5.5s/frame). Producer side shipped as v0.2.0
(f674442: visibility prompt + poultry folding + conf clamp; c3d5703:
same-model single-pass) and was verified live on an empty lit coop the
same day. Remaining work is one observation window.

## Status (detection tuning)
Current task: none — T14 done, phase closed
Last completed: T14 morning soak 2026-09-03 (verified live on-box:
phantom species 0, OOM kills 0 overnight, 22 recycles with no reload
stall, service active; committed 59df91e+)

## Task list (detection tuning)
- [x] T12 — Dusk verification of v0.2.0   ✅ DONE 2026-09-01
      (counts PASS, single-pass PASS, box closed correctly on real
      birds; zero-phantom FAIL pre-fix: dog/cat/pig/fish phantoms at
      conf 0.9 after dark, ground truth confirmed no dog, the 23:54
      human was real, light on, birds roost out of camera view)
- [x] T13 — Deploy v0.2.1 species-allowlist filter  ✅ DONE 2026-09-01
      (user-approved; verified: startup v0.2.1 PID 612878, allowlist
      seeded, zero non-chicken species published post-restart, wire
      contract intact; post-deploy detail in T12 task file)
- [x] T14 — Morning soak check  ✅ DONE 2026-09-03 (verified on-box over
      the full 2026-09-02→09-03 overnight journal: phantom species 0
      (expect 0); kernel OOM kills 0 (baseline 10-28/day); 22 recycle
      events, all during inference — recycle→next-inference-completion
      12.6-15.9s, no reload stall >120s; overnight alert.ollama 0,
      inference errors 0; service active; door-open window fired with
      chickens:3 conf 0.9, top-level chickens serializes FIRST and
      payload v0.2.1-identical)

T11 (consumer CHICKENS_CONFIRM_STREAK 1→2) removed 2026-08-31 by user
decision — consumer behavior stays as-is: the door can still close on a
single qualifying frame (conf≥0.8, chickens≥3).

## Blocked
None.

## Notes (diff-gate fix)
- Same model throughout: `JCHICK_GATE_MODEL`/`JCHICK_DETAIL_MODEL` stay
  `llava-phi3:3.8b`. No model-swap task. The eval harness `--model` flag
  and the existing env vars are the mechanism for testing other models;
  T09 lists an optional `--model llava:7b` run that does NOT change the
  live service.
- Before/after framing: T08 captures the "before" (current prompt,
  sidecar JSONs hold the verdicts). T09 runs the de-biased prompt
  against the same frames for the "after". T10 ships the winner only
  after T09 confirms it, then re-confirms end-to-end in daylight.
- T10 is conditional on T09 = `PROMPT FIX CONFIRMED`. If T09 decides
  `PROMPT FIX INSUFFICIENT` or `HARDWARE/FRAMING`, mark T10 dropped:
  - INSUFFICIENT → live code stays on the current prompt; the harness
    supports further ad-hoc prompt/model experiments without new tasks.
  - HARDWARE/FRAMING → out of software scope; recommend camera aim
    (`scripts/cam-view.sh`) or lighting.
- Audit dump (`JCHICK_GATE_AUDIT_DIR`) has disk/IO cost; T10 disables
  it (revert `.env.example` to empty) once diagnosis is complete.
- Keeping `assets/gate_prompt_debiased.txt` and the shipped `PROMPT` in
  sync (T10 adds a Python equality check) is what makes future harness
  runs representative of the live prompt.

---

# Inference downscale + llama-server OOM fix — 2026-09-02

## Goal
Stop the nightly llama-server OOM-kill loop with a watermark-triggered
runner recycle (keep_alive: 0 at VmRSS >= 6000 MB) and ship 640-px
inference downscale as payload hygiene. Ships as v0.2.2.

## Context
Plan: .omo/plans/inference-downscale.md (dual-approved round-2,
2026-09-01). The leak matches ollama#18106 (per-request anonymous
leak); the vision encoder letterboxes every input to fixed 336x336 /
576 tokens, so downscale is hygiene and recycle is the fix. T14's
morning soak absorbs this deploy's overnight checks.

## Status
Current task: T15 (DONE — recycle deployed + verified; downscale shipped
disabled per G4 verdict)
Last completed: T15 2026-09-02 (v0.2.2 recycle-only deploy, commit
fb83341+; live-verified: 2 recycle events in first hour, zero OOM
kills, inference flowing across recycles)

## Task list
- [x] T15 — Watermark recycle (OOM fix) + inference downscale hygiene
      (v0.2.2 deployed recycle-only 2026-09-02; downscale code ships but
      JCHICK_INFERENCE_MAX_DIM=0 — lit-frame G4 found confidence straddle
      past the consumer 0.80 gate; task file:
      T15_inference_downscale.md)  ✅ DONE

## Blocked
None.
