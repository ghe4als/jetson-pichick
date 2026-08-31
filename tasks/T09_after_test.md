# T09 — "After" test: de-biased prompt vs baseline, same model

## Goal
Run the offline eval harness (T07) with the de-biased prompt against the baseline frames (T08), using the SAME model `llava-phi3:3.8b`. Compare how many frames each prompt recognizes chickens in. This is the "after" half of the before/after test the user asked for, run offline against the fixed test set — no deploy, no daylight window. Also optionally test other models via `--model` (the "be sure we can change between them for testing" capability), without committing to a swap.

## Context
T08 left the baseline frames on the Jetson at `/var/lib/jchick/gate-audit/`, each with a JSON sidecar holding the CURRENT prompt's verdict (the "before": `chickens: 0` on every frame, per the investigation). The harness (T07) can replay any prompt/model against those same JPEGs. So the "after" is: run the de-biased prompt (`assets/gate_prompt_debiased.txt`) against the frames and count `chickens > 0`. If the de-biased prompt recognizes chickens on frames the current prompt missed — same model — that confirms the prompt was the fault, not the model (matching the user's belief that `llava-phi3:3.8b` works fine).

Same model throughout. The `--model` flag is available for additional experiments (e.g. `--model llava:7b` after `ollama pull`) but no task forces a model change.

## Files touched
- `tasks/diff-gate-findings-2026-08-28.md` — NEW. Records the before/after numbers, per-frame comparison, and the decision that feeds T10.

No code changes. This task runs the already-shipped harness against already-captured frames.

## Pre-conditions
- [ ] T07 complete and committed (harness shipped).
- [ ] T08 complete (baseline frames on the Jetson at `/var/lib/jchick/gate-audit/`, copied to `/tmp/jchick-audit-baseline/` on the dev box).

## Exact changes required

### Step 1: confirm the baseline frames are present
```
ssh pichick@192.168.0.18 'ls -1 /var/lib/jchick/gate-audit/*.jpg | wc -l'
# Expected: > 0
```

### Step 2: "before" — re-confirm current prompt verdicts (optional; sidecars already hold them)
The sidecar JSONs from T08 ARE the "before" (current shipped prompt + llava-phi3:3.8b). To get a clean comparable table, run the harness with the shipped prompt (omit `--prompt-file`):
```
ssh pichick@192.168.0.18 '/opt/jchick/.venv/bin/python /opt/jchick/scripts/eval_gate.py \
    --dir /var/lib/jchick/gate-audit \
    --model llava-phi3:3.8b \
    --top 30' 2>&1 | tee /tmp/before.txt
# Expected: a table; summary line "chickens>0 on 0 frames" (the known failure).
```
`--top 30` keeps the 30 highest-diff-score frames (most likely to contain real motion / chickens) to keep the run fast. Adjust N up for a larger sample.

### Step 3: "after" — de-biased prompt, same model
```
ssh pichick@192.168.0.18 '/opt/jchick/.venv/bin/python /opt/jchick/scripts/eval_gate.py \
    --dir /var/lib/jchick/gate-audit \
    --model llava-phi3:3.8b \
    --prompt-file /opt/jchick/assets/gate_prompt_debiased.txt \
    --top 30' 2>&1 | tee /tmp/after.txt
# Compare the summary line: chickens>0 count vs the "before" run.
```
Use the SAME `--top N` as Step 2 so both runs evaluate the identical frame subset.

### Step 4 (optional): test other models against the same frames
This is the "change between them for testing" capability. Only if a model is already pulled on the Jetson:
```
ssh pichick@192.168.0.18 'ollama list'   # see what's available
# e.g. if llava:7b is pulled:
ssh pichick@192.168.0.18 '/opt/jchick/.venv/bin/python /opt/jchick/scripts/eval_gate.py \
    --dir /var/lib/jchick/gate-audit \
    --model llava:7b \
    --prompt-file /opt/jchick/assets/gate_prompt_debiased.txt \
    --top 30' 2>&1 | tee /tmp/after_llava7b.txt
```
This does NOT change the live service — it only queries Ollama with a different model. Pull first with `ssh pichick@192.168.0.18 'ollama pull llava:7b'` if you want to try it. (README warns llava:7b is 4.7GB and may not share VRAM; the live service uses gate==detail to avoid swap stalls, so a live swap is a separate decision — see T10 notes.)

### Step 5: spot-check a few frames visually (sanity-check the model's reading)
Pull a few of the highest-diff baseline frames to the dev box and `inspect_image` them to confirm chickens are actually visible (so a "chickens>0" result from the de-biased prompt is a true positive, not a new hallucination):
```
mkdir -p /tmp/jchick-audit-check
rsync -av pichick@192.168.0.18:/var/lib/jchick/gate-audit/ /tmp/jchick-audit-check/
python3 - <<'PY'
from pathlib import Path
import json
d = Path('/tmp/jchick-audit-check')
pairs = [(json.loads(j.read_text()).get('diff_score',0), j, j.with_suffix('.jpg'))
         for j in d.glob('*.json')]
pairs.sort(reverse=True)
for score, j, jpg in pairs[6:]:   # keep top 6 by diff_score
    try: j.unlink(); jpg.unlink()
    except OSError: pass
print('kept', len(list(d.glob('*.jpg'))))
PY
```
Then `inspect_image` each kept frame (write JSON args to `xd://inspect_image`):
```json
{"path": "/tmp/jchick-audit-check/<stem>.jpg",
 "question": "Chicken-coop security camera still frame. Are chickens visible? Estimate count and position. Describe framing/lighting briefly."}
```
Cross-check against the harness's verdict for that frame.

### Step 6: write the finding
File: `tasks/diff-gate-findings-2026-08-28.md` (NEW). Record:
- Baseline: N frames captured, "before" (current prompt, llava-phi3:3.8b) chickens>0 count.
- "After" (de-biased prompt, llava-phi3:3.8b) chickens>0 count, mean confidence, mean latency.
- Optional other-model results (model, chickens>0 count).
- Visual spot-check: for a few top frames, what `inspect_image` saw vs the de-biased prompt's verdict (true positives vs hallucinations).
- Decision — one of:
  - `PROMPT FIX CONFIRMED` — de-biased prompt recognizes chickens on baseline frames the current prompt missed, with no new hallucinations → T10 applies the de-biased prompt to the live code and confirms in daylight.
  - `PROMPT FIX INSUFFICIENT` — de-biased prompt still mostly says 0, or recognizes "chickens" by hallucinating on empty frames → do NOT ship it; document, and note the harness supports further prompt/model experiments. Live code stays on the current prompt.
  - `HARDWARE/FRAMING` — visual spot-check shows chickens are NOT in frame on the baseline frames (the camera isn't seeing the coop) → out of software scope; recommend camera aim (`scripts/cam-view.sh`) or lighting. No prompt will help.

## Validation plan
```
test -f tasks/diff-gate-findings-2026-08-28.md && \
grep -E 'PROMPT FIX CONFIRMED|PROMPT FIX INSUFFICIENT|HARDWARE/FRAMING' tasks/diff-gate-findings-2026-08-28.md
# Expected: exit 0 (one decision string present)

# before/after summary lines captured:
test -s /tmp/before.txt && test -s /tmp/after.txt && \
grep -q 'chickens>0' /tmp/before.txt && grep -q 'chickens>0' /tmp/after.txt
# Expected: exit 0
```

## Success criteria
- [ ] "before" and "after" harness runs both completed with summary lines.
- [ ] `tasks/diff-gate-findings-2026-08-28.md` exists with a named decision and the before/after numbers.
- [ ] Visual spot-check of at least a few top frames recorded (true-positive vs hallucination check).
- [ ] Same model (`llava-phi3:3.8b`) used for the before/after comparison.

## Rollback
No code was changed; nothing to roll back. `/tmp/before.txt`, `/tmp/after.txt`, and the dev-box frame copies are scratch artifacts.

## Next task
- If `PROMPT FIX CONFIRMED`: execute T10 (apply the de-biased prompt to the live code, deploy, confirm in daylight, disable audit).
- If `PROMPT FIX INSUFFICIENT` or `HARDWARE/FRAMING`: no T10 as written — update PLAN.md. For `INSUFFICIENT`, further harness experiments (more prompt variants, other models) can be run ad hoc using T07's tool without new tasks. For `HARDWARE/FRAMING`, hand to the user for camera/lighting.