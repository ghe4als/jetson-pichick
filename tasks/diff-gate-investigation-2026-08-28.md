# Diff-gate investigation — 2026-08-28

## Diagnosis: Case C — gate model cannot see chickens

The pixel diff gate is working correctly. Frames are passing through.
The gate model (`llava-phi3:3.8b`) returns `chickens: 0, movement: still`
on every frame, even during daylight with the door open and chickens
present in the coop.

## Evidence

### During the close window (16:46-19:16 local, 23:46-02:16 UTC)

Captured ~20 `inference.gated` messages from journalctl between 19:00
and 19:12 PDT. Every single one shows:

- `diff_score`: 0.0124 to 0.0355 — ALL above the 0.012 threshold,
  meaning frames ARE passing the pixel diff gate. The diff gate is
  NOT the problem.
- `gate.chickens`: 0 on every frame.
- `gate.movement`: "still" on every frame.
- `gate.confidence`: 0.9-1.0 (model is confident there are no chickens).

The heartbeat at 19:04:54 shows:
- `frames_seen: 314654`
- `frames_inferenced: 2274` (frames passed diff gate + ran model)
- `frames_fired: 1309` (but `last_chickens: 0` — no recent fires with chickens)

### After restart with DEBUG logging (21:02-21:04 PDT, nighttime)

- 3 warmup frames passed (score=1.0, forced) → all `gate.chickens: 0`
- All subsequent frames: `diff_score: 0.0000` — scene is completely
  static (dark, door closed, no movement). Expected for nighttime.
- Manual trigger test: bypassed diff gate, model still returned
  `gate.chickens: 0, movement: still`. Expected — it's dark, door
  is closed, no chickens visible to the camera.

### Root cause

The gate model `llava-phi3:3.8b` cannot detect chickens in the current
camera framing/lighting. The pixel diff gate is healthy (scores 0.012-
0.036 during daylight = enough motion to pass). The problem is purely
model accuracy: the LLM sees the frame but says "no chickens, still".

This aligns with commit `12c7722` (2026-08-13) which rewrote the prompt
to explicitly allow `chickens: 0` to prevent false fires on empty coops.
The fix over-corrected: the model now says 0 even when chickens ARE
present.

## Next action

1. **Check camera framing**: open `http://192.168.0.18:8090/` during
   daylight and verify chickens are visible in the frame. If the framing
   is wrong, adjust the camera.

2. **Try a different gate model**: `llava-phi3:3.8b` may not be capable
   enough. Options:
   - `llava:7b` — larger, may see better. But check VRAM (README warns
     about 7.5GB shared memory on Orin Nano).
   - `llava-llama3:8b` — newer, potentially sharper.
   - Pull on Jetson: `ollama pull llava:7b`, edit `.env.example`
     `JCHICK_GATE_MODEL=llava:7b`, rsync + install.sh + restart.

3. **Consider prompt tuning**: the gate prompt in
   `jetson-pichick/src/jchick/ollama.py` was rewritten in commit
   `12c7722` to allow `chickens: 0`. It may be too biased toward
   "empty coop". Review the prompt and consider adding examples or
   language that helps the model recognize chickens in low light or
   at angles.

4. **Consider lighting**: if the coop interior is too dim at dusk
   (when the close window starts), the model may not be able to see
   the chickens even if they're there. Adding a low-wattage light
   near the camera could help — the powersave mode already controls
   a light, so this may be a hardware/placement issue.

## Config changes made

- `jetson-pichick/.env.example` line 43: `JCHICK_LOG_LEVEL=DEBUG`
  (changed from INFO). Keep DEBUG — it's useful for future diagnosis
  and low overhead.

No other config changes. `JCHICK_DIFF_THRESHOLD` stays at 0.012 —
the diff gate is NOT the problem.