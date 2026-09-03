# T16 — Movement label derived from measured diff_score

## Goal
Replace the VLM's per-frame `movement` guess with a deterministic label
derived from the measured frame-to-frame pixel diff (`diff_score`) that
already ships in every payload. Ships as v0.2.3.

## Context
User request 2026-09-03: "the camera triggers on movements, when that
happens it can't be 'still'... Is it possible to make a comparison of two
pictures to see how much movement happened?" The two-picture comparison
already exists (FrameDiffGate, src/jchick/diff.py: mean-abs grayscale diff
of 64x64 thumbnails, published as diff_score). Research 2026-09-03: for
fixed-camera motion detection, frame-difference is the standard method
(Springer 2024 two-frame differencing; arXiv:2503.09132 frame differences
as motion cues; SAGE 2018 block-wise frame difference; ACM TOMM joint
diff+background subtraction). Phase 1 = derive the label from the measured
score. Phase 2 (block-wise locality metric) captured separately as T17.

Evidence the VLM label is uninformative (on-box journal 2026-08-31 to
2026-09-03, n=252 inferenced frames): diff_score distributions by VLM
label are near-identical — still: median 0.0222, p90 0.053, max 1.0;
calm: median 0.0299, p90 0.046, max 0.17; active: only 3 frames at
0.048-0.061 (one dusk event window). Every diff-gate-passed frame had
pixel motion >= 0.012 by definition (live JCHICK_DIFF_THRESHOLD=0.012),
so "still" on an inferenced frame is semantically wrong. The VLM judges
one frozen JPEG and the prompt forces still on empty frames
(ollama.py:51-53); it cannot see motion.

Band grounding (same journal review):
- Real dusk bird motion: 0.012-0.093
- VLM-"active" frames: 0.048-0.061 (all inside the calm band below)
- Dusk light-shift outlier: 0.1734
- Warmup frames: diff_score sentinel 1.0 (FrameDiffGate returns 1.0 on
  warmup; label must still be defined)

Bands (0..1 mean-abs scale):
- score < 0.030            -> "still"
- 0.030 <= score < 0.080   -> "calm"   (bulk of observed real motion,
                                        contains the 3 VLM-active frames)
- 0.080 <= score < 0.150   -> "active" (top of observed bird motion 0.093)
- score >= 0.150           -> "agitated" (scene-scale change: door swing,
                                        human, predator; 0.173 outlier)

Wire safety (both sides cited per AGENTS.md):
- Producer: src/jchick/app.py:246,262 publishes movement in gate/detail.
- Consumer: coop_door_controller/src/nats_integration.c:359-371 extracts
  movement only into a 16-char log buffer — no branching. All labels fit
  ("agitated" = 8 chars).
- Top-level chickens key order is untouched (movement lives inside
  gate/detail sub-objects; _publish wraps host/ip/ts first).

## Files touched
- `src/jchick/diff.py` — add `movement_label(score)` at end of module.
- `src/jchick/app.py` — derive label in `_publish_inference`; pass to HUD.
- `src/jchick/mjpeg_server.py` — `update_last_result` takes `movement` kwarg.
- `src/jchick/__init__.py` — version 0.2.2 -> 0.2.3.

## Pre-conditions
- [x] None (no dependencies)

## Exact changes required

### Change 1: movement_label() in diff.py
File: `src/jchick/diff.py`
Action: INSERT at end of module, after `_mean_abs_diff`

    # ---- movement label bands ---------------------------------------
    # The published movement label comes from the measured diff score,
    # not the VLM: a single frozen frame cannot show motion (the gate
    # prompt even forces "still" on empty frames), while the diff gate
    # is the real motion detector (see cascade.py). Bands grounded in
    # the on-box journal review 2026-08-31 -> 2026-09-03 (tasks/T16):
    # real dusk bird motion 0.012-0.093, dusk light-shift outlier 0.173,
    # warmup sentinel 1.0.
    #   score < 0.030          -> "still"    (smallest real motion)
    #   0.030..0.080           -> "calm"     (bulk of observed motion)
    #   0.080..0.150           -> "active"   (substantial motion)
    #   score >= 0.150         -> "agitated" (scene-scale change)

    def movement_label(score: float) -> str:
        """Map a diff score (0..1) to a movement label. Total over [0, 1]."""
        if score < 0.030:
            return "still"
        if score < 0.080:
            return "calm"
        if score < 0.150:
            return "active"
        return "agitated"

### Change 2: app.py _publish_inference override
File: `src/jchick/app.py`
Action: REPLACE body of `_publish_inference` — derive label once, use in
gate_payload, detail_payload, and HUD.

Current code (lines 240-273):
    async def _publish_inference(self, r: CascadeResult, *, diff_score: float) -> None:
        chosen = r.detail or r.gate
        self._last_chickens = chosen.chickens
        gate_payload = {
            "chickens": r.gate.chickens,
            "other_animals": r.gate.other_animals,
            "movement": r.gate.movement,
            "confidence": r.gate.confidence,
            "model": r.gate.model,
            "latency_ms": r.gate.latency_ms,
        }
        if not r.fired:
            await self._publish("inference.gated", {
                "diff_score": round(diff_score, 4),
                "gate": gate_payload,
            })
            return
        detail_payload = None
        if r.detail is not None:
            detail_payload = {
                "chickens": r.detail.chickens,
                "other_animals": r.detail.other_animals,
                "movement": r.detail.movement,
                "confidence": r.detail.confidence,
                "notes": r.detail.notes,
                "model": r.detail.model,
                "latency_ms": r.detail.latency_ms,
            }
        await self._publish("inference.fired", {
            "chickens": chosen.chickens,
            "diff_score": round(diff_score, 4),
            "gate": gate_payload,
            "detail": detail_payload,
        })

New code:
    async def _publish_inference(self, r: CascadeResult, *, diff_score: float) -> None:
        chosen = r.detail or r.gate
        self._last_chickens = chosen.chickens
        # Movement is a measured property of the frame pair, not a VLM
        # guess from a frozen image: derive it from diff_score so the
        # label matches what actually moved (the VLM label was
        # uninformative — see tasks/T16 for the distribution evidence).
        movement = movement_label(diff_score)
        gate_payload = {
            "chickens": r.gate.chickens,
            "other_animals": r.gate.other_animals,
            "movement": movement,
            "confidence": r.gate.confidence,
            "model": r.gate.model,
            "latency_ms": r.gate.latency_ms,
        }
        if not r.fired:
            await self._publish("inference.gated", {
                "diff_score": round(diff_score, 4),
                "gate": gate_payload,
            })
            return
        detail_payload = None
        if r.detail is not None:
            detail_payload = {
                "chickens": r.detail.chickens,
                "other_animals": r.detail.other_animals,
                "movement": movement,
                "confidence": r.detail.confidence,
                "notes": r.detail.notes,
                "model": r.detail.model,
                "latency_ms": r.detail.latency_ms,
            }
        await self._publish("inference.fired", {
            "chickens": chosen.chickens,
            "diff_score": round(diff_score, 4),
            "gate": gate_payload,
            "detail": detail_payload,
        })

### Change 3: app.py HUD call passes derived label
File: `src/jchick/app.py`
Action: REPLACE in `_inference_loop` (line ~152)

Before:
                if self._mjpeg is not None:
                    self._mjpeg.update_last_result(result, diff_score=score)

After:
                if self._mjpeg is not None:
                    self._mjpeg.update_last_result(
                        result, diff_score=score,
                        movement=movement_label(score),
                    )

### Change 4: mjpeg_server.py update_last_result kwarg
File: `src/jchick/mjpeg_server.py`
Action: REPLACE signature and movement field (lines 88-110)

Before:
    def update_last_result(self, result: Any, *, diff_score: float) -> None:
        ...
        chosen = result.detail or result.gate
        self._last = LastResult(
            ...
            movement=chosen.movement,
            ...

After:
    def update_last_result(self, result: Any, *, diff_score: float,
                           movement: str | None = None) -> None:
        ...
        chosen = result.detail or result.gate
        self._last = LastResult(
            ...
            movement=movement if movement is not None else chosen.movement,
            ...

(Kwarg keeps the old behavior for any caller that omits it; app.py is
the only caller and always passes it.)

### Change 5: app.py import
File: `src/jchick/app.py`
Action: REPLACE import line 31

Before:
    from .diff import FrameDiffGate

After:
    from .diff import FrameDiffGate, movement_label

### Change 6: version bump
File: `src/jchick/__init__.py`
Action: REPLACE `__version__ = "0.2.2"` with `__version__ = "0.2.3"`

## Validation plan
    python3 -m py_compile src/jchick/*.py
    # Expected: no output, exit 0

    PYTHONPATH=src python3 - <<'EOF'
    from jchick.diff import movement_label
    assert movement_label(0.0) == "still"
    assert movement_label(0.0299) == "still"
    assert movement_label(0.030) == "calm"
    assert movement_label(0.05) == "calm"
    assert movement_label(0.0799) == "calm"
    assert movement_label(0.080) == "active"
    assert movement_label(0.093) == "active"
    assert movement_label(0.1499) == "active"
    assert movement_label(0.150) == "agitated"
    assert movement_label(0.1734) == "agitated"
    assert movement_label(1.0) == "agitated"
    print("movement_label bands OK")
    EOF
    # Expected: movement_label bands OK

    PYTHONPATH=src python3 - <<'EOF'
    # Publish-path smoke: gate/detail both carry the derived label and
    # top-level key order is preserved.
    import asyncio, json
    from unittest.mock import AsyncMock, MagicMock
    from jchick.app import App
    from jchick.cascade import CascadeResult
    from jchick.ollama import VisionResult

    vr = VisionResult(chickens=3, other_animals=[], movement="still",
                      confidence=0.9, notes="n", model="m",
                      latency_ms=10, raw={})
    app = App.__new__(App)
    app._cfg = MagicMock(host="pichick")
    app._pub = AsyncMock()
    app._ip = "1.2.3.4"
    app._last_chickens = 0
    captured = {}
    async def fake_pub(suffix, payload): captured[suffix] = payload
    async def real_publish(self, suffix, payload):
        payload = {"host": self._cfg.host, "ip": self._ip,
                   "ts": "t", **payload}
        captured[suffix] = payload
    App._publish = real_publish
    cr = CascadeResult(gate=vr, detail=vr, fired=True)
    asyncio.run(App._publish_inference(app, cr, diff_score=0.05))
    p = captured["inference.fired"]
    assert json.dumps(p).index('"chickens"') < json.dumps(p).index('"diff_score"')
    assert p["gate"]["movement"] == "calm" and p["detail"]["movement"] == "calm"
    print("publish smoke OK:", p["chickens"], p["gate"]["movement"])
    EOF
    # Expected: publish smoke OK: 3 calm

## Success criteria
- [ ] All validation commands return expected output
- [ ] git diff shows only intended changes
- [ ] HAR (High Accuracy Review) of the full diff passes
- [ ] Deployed live: journal shows inference payloads with band-derived
      movement labels (verify: gated/fired lines where diff_score and
      movement agree per the band table)

## Rollback
    git checkout src/jchick/diff.py src/jchick/app.py src/jchick/mjpeg_server.py src/jchick/__init__.py

## Next task
None. T17 (block-wise metric) is captured separately — user decision.