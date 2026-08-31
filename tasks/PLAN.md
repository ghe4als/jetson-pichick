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
c3d5703 (see tasks/T11_consumer_streak.md context for the evidence).

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
Harden the detection pipeline against the two failure modes measured in
the 2026-08-31 live review: phantom-predator false alerts at dusk and
single-frame door-close on a flickering count signal.

## Context
3-day `inference.fired` review (575 frames): 2↔3 count flicker (209
flips), 13 phantom-predator frames passing the consumer conf≥0.80
filter, rooster-split undercounts (9 frames), gate+detail running the
same model twice (+5.5s/frame). Producer side shipped as v0.2.0
(f674442: visibility prompt + poultry folding + conf clamp; c3d5703:
same-model single-pass) and was verified live on an empty lit coop the
same day. Remaining work is in the consumer repo and one observation
window.

## Status (detection tuning)
Current task: T12 (PENDING — needs dusk window ~16:30-19:40 local)
Last completed: v0.2.0 producer deploy (empty-coop verified) 2026-08-31

## Task list (detection tuning)
- [ ] T11 — Raise CHICKENS_CONFIRM_STREAK 1→2 in coop_door_controller (consumer repo)  ⬜ TODO
- [ ] T12 — Dusk verification of v0.2.0 detection tuning (observation only)           ⬜ PENDING dusk

## Blocked
T12 blocked until the next dusk window (~16:30 local) with chickens
returning. T11 is not blocked.

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